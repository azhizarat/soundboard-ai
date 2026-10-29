import json
from pathlib import Path
import numpy as np
from store import SoundboardStore
from ai_analyzer import analyze_acoustics, generate_smart_metadata, merge_diarized_segments
from audio_engine import trim_and_normalize, ReplayRingBuffer, compute_waveform_peaks, PlaybackSession
from main import SoundboardAPI


def test_store_crud_and_deduplication(tmp_path):
    store = SoundboardStore(data_dir=str(tmp_path))
    c1 = store.add_or_update_clip({
        "title": "Relaxa",
        "file_path": "clips/test1.mp3",
        "category": "Call",
        "speaker_id": "interlocutor_1",
        "transcript": "Não vai esfolar, relaxe.",
    })
    assert c1["id"]
    # Re-importing same file_path updates existing clip instead of duplicating
    c2 = store.add_or_update_clip({
        "title": "Relaxa Editado",
        "file_path": "clips/test1.mp3",
        "category": "Meme",
    })
    assert c1["id"] == c2["id"]
    assert len(store.state["clips"]) == 1
    assert store.search_clips(query="esfolar")[0]["title"] == "Relaxa Editado"


def test_import_separados_skips_missing_files(tmp_path):
    sep_dir = tmp_path / "separados"
    (sep_dir / "interlocutor_1").mkdir(parents=True)
    valid_mp3 = sep_dir / "interlocutor_1" / "01.mp3"
    valid_mp3.write_bytes(b"RIFF_FAKE_AUDIO")
    manifest = [
        {"interlocutor": 1, "arquivo_trecho": "interlocutor_1/01.mp3", "duracao_s": 2.0, "transcricao": "Oi tudo bem"},
        {"interlocutor": 2, "arquivo_trecho": "interlocutor_2/missing.mp3", "duracao_s": 1.5, "transcricao": "Sumiu"},
    ]
    (sep_dir / "mapa_de_falas.json").write_text(json.dumps(manifest), encoding="utf-8")
    store = SoundboardStore(data_dir=str(tmp_path / "data"))
    imported = store.import_separados_manifest(str(sep_dir))
    assert len(imported) == 1
    assert imported[0]["speaker_id"] == "interlocutor_1"


def test_analyze_acoustics_handles_silence_and_moods():
    sr = 16000
    # 1. Pure silence must not produce NaN/Inf
    silence = np.zeros(sr, dtype=np.float32)
    res_sil = analyze_acoustics(silence, sr)
    assert np.isfinite(res_sil["rms_db"])
    assert res_sil["mood"] == "Calmo / Sussurro"

    # 2. High-amplitude clipped signal -> Rage / Grito
    t = np.linspace(0, 1.5, int(1.5 * sr), endpoint=False)
    rage_wave = np.clip(0.95 * np.sin(2 * np.pi * 340 * t), -1.0, 1.0).astype(np.float32)
    res_rage = analyze_acoustics(rage_wave, sr)
    assert res_rage["mood"] in ("Rage / Grito", "Fala Animada")

    # 3. Rhythmic 4.5 Hz laughter bursts
    env = (0.5 * (1 + np.sin(2 * np.pi * 4.5 * t))) ** 3
    laugh_wave = (0.6 * env * np.sin(2 * np.pi * 280 * (1 + 0.15 * np.sin(2 * np.pi * 9 * t)) * t)).astype(np.float32)
    res_laugh = analyze_acoustics(laugh_wave, sr)
    assert res_laugh["mood"] == "Risada"


def test_generate_smart_metadata_ptbr():
    meta = generate_smart_metadata(
        transcript="Não vai esfolar, relaxa cara!",
        mood="Calmo / Sussurro",
        speaker_name="João",
        duration_s=2.0,
        use_llm=False,
    )
    assert 2 <= len(meta["title"]) <= 32
    assert meta["category"] in ("Rage", "Risada", "Meme", "Vinhetas", "Call")
    assert meta["emoji"]
    assert meta["color"].startswith("#")


def test_trim_and_normalize_bounds_and_silence():
    sr = 48000
    stereo = np.ones((sr * 2, 2), dtype=np.float32) * 0.25
    # Valid 0.5s to 1.5s trim + peak normalize to ~0.92
    out = trim_and_normalize(stereo, sr, trim_start=0.5, trim_end=1.5, volume=1.0, normalize=True)
    assert out.shape == (sr, 2)
    assert 0.85 <= float(np.max(np.abs(out))) <= 0.96

    # Invalid trim_end <= trim_start clamps safely without empty crash
    safe = trim_and_normalize(stereo, sr, trim_start=1.8, trim_end=0.5, volume=1.0, normalize=True)
    assert len(safe) > 0


def test_replay_ring_buffer_snapshot():
    buf = ReplayRingBuffer(max_seconds=2.0, sr=100, channels=2)
    for i in range(3):
        buf.append(np.full((100, 2), float(i + 1), dtype=np.float32))
    snap = buf.snapshot(last_seconds=2.0)
    assert snap.shape == (200, 2)
    assert np.allclose(snap[:100], 2.0)
    assert np.allclose(snap[100:], 3.0)


def test_soundboard_api_smoke(tmp_path):
    api = SoundboardAPI(data_dir=str(tmp_path), start_background_services=False)
    state = api.get_initial_state()
    assert "clips" in state
    assert "devices" in state
    assert "speakers" in state


def test_soundboard_api_exposes_no_public_non_callable_objects(tmp_path):
    """Prevents pywebview from recursively crawling window.native or internal objects."""
    api = SoundboardAPI(data_dir=str(tmp_path), start_background_services=False)
    api._set_window(object())
    public_non_callables = [
        name for name in dir(api)
        if not name.startswith("_") and not callable(getattr(api, name))
    ]
    assert public_non_callables == []


def test_configurable_replay_seconds_and_60s_buffer(tmp_path):
    api = SoundboardAPI(data_dir=str(tmp_path), start_background_services=False)
    # Ring buffer must hold at least 60s capacity so increasing clip duration doesn't drop history
    assert api._audio.replay_buffer.max_samples >= 60 * api._audio.TARGET_SR
    updated = api.update_settings({"replay_seconds": 4})
    assert updated["replay_seconds"] == 4.0
    updated_long = api.update_settings({"replay_seconds": 45})
    assert updated_long["replay_seconds"] == 45.0
    assert api._audio.replay_buffer.max_samples >= 60 * api._audio.TARGET_SR


def test_multi_speaker_merge_and_store_filtering(tmp_path):
    raw_segs = [
        {"start": 0.1, "end": 1.2, "speaker_id": "interlocutor_1"},
        {"start": 1.4, "end": 2.3, "speaker_id": "interlocutor_1"},  # gap 0.2s -> merges with first
        {"start": 2.5, "end": 4.0, "speaker_id": "interlocutor_2"},  # different speaker -> stays separate
    ]
    merged = merge_diarized_segments(raw_segs, total_duration=4.5, max_merge_gap=0.65)
    assert len(merged) == 2
    assert merged[0]["speaker_id"] == "interlocutor_1"
    assert merged[1]["speaker_id"] == "interlocutor_2"

    store = SoundboardStore(data_dir=str(tmp_path))
    clip = store.add_or_update_clip({
        "title": "Diálogo Call",
        "file_path": "clips/dialog.wav",
        "speaker_id": "interlocutor_1",
        "speaker_ids": ["interlocutor_1", "interlocutor_2"],
        "segments": merged,
    })
    assert "interlocutor_1" in clip["speaker_ids"]
    assert "interlocutor_2" in clip["speaker_ids"]
    # Filtering by either speaker returns the multi-speaker clip
    assert len(store.search_clips(speaker_id="interlocutor_1")) == 1
    assert len(store.search_clips(speaker_id="interlocutor_2")) == 1


def test_delete_speaker_and_vinheta_import_without_person(tmp_path):
    import soundfile as sf

    store = SoundboardStore(data_dir=str(tmp_path / "store_data"))
    cat_ids = [c["id"] for c in store.state["categories"]]
    assert "Vinhetas" in cat_ids

    store.add_or_update_clip({
        "title": "Fala dupla",
        "file_path": "clips/fala.wav",
        "speaker_id": "interlocutor_1",
        "speaker_ids": ["interlocutor_1", "interlocutor_2"],
    })
    assert "interlocutor_1" in store.state["speakers"]
    assert store.delete_speaker("interlocutor_1") is True
    assert "interlocutor_1" not in store.state["speakers"]
    clip = store.state["clips"][0]
    assert clip["speaker_id"] == "interlocutor_2"
    assert clip["speaker_ids"] == ["interlocutor_2"]

    # Import directly as 'Vinhetas' with skip_speaker_id=True must not assign any speaker
    wav_file = tmp_path / "vinheta_boom.wav"
    sf.write(str(wav_file), np.sin(np.linspace(0, 50, 16000)).astype("float32"), 16000)

    api = SoundboardAPI(data_dir=str(tmp_path / "api_data"), start_background_services=False)
    api._ai._get_whisper = lambda: None
    res = api.import_audio_files([str(wav_file)], skip_speaker_id=True, forced_category="Vinhetas")
    assert res["ok"] is True
    imported_clip = res["imported"][0]
    assert imported_clip["category"] == "Vinhetas"
    assert imported_clip["speaker_id"] == ""
    assert imported_clip["speaker_ids"] == []


def test_waveform_peaks_and_playback_session_controls(tmp_path):
    import soundfile as sf

    sr = 48000
    # First half silent, second half loud
    first_half = np.zeros((sr, 2), dtype=np.float32)
    second_half = np.ones((sr, 2), dtype=np.float32) * 0.8
    audio = np.vstack([first_half, second_half])

    peaks = compute_waveform_peaks(audio, num_bins=40)
    assert len(peaks) == 40
    assert max(peaks[:18]) == 0.0
    assert min(peaks[22:]) > 0.9

    session = PlaybackSession(play_id="p1", clip_id="c1", data=audio, sr=sr)
    assert session.paused is False
    session.toggle_pause()
    assert session.paused is True
    session.seek(1.0)
    assert session.pos_idx == sr
    session.set_loop(True)
    assert session.loop is True
    session.set_live_volume(1.35)
    assert np.isclose(session.live_volume, 1.35)
    session.restart()
    assert session.pos_idx == 0

    wav_path = tmp_path / "wave_test.wav"
    sf.write(str(wav_path), audio, sr)
    api = SoundboardAPI(data_dir=str(tmp_path / "api_wave"), start_background_services=False)
    clip = api._store.add_or_update_clip({"title": "Wave", "file_path": str(wav_path), "duration": 2.0})
    wf = api.get_clip_waveform(clip["id"], num_bins=60)
    assert wf["ok"] is True
    assert len(wf["peaks"]) == 60


def test_toggle_mini_overlay_never_touches_winforms_properties(tmp_path):
    class PoisonWindow:
        @property
        def on_top(self):
            raise RuntimeError("WinForms TopMost must never be accessed from background thread")

        @on_top.setter
        def on_top(self, value):
            raise RuntimeError("WinForms TopMost must never be set from background thread")

        def resize(self, width, height):
            raise RuntimeError("WinForms resize must never be called directly from background thread")

        def evaluate_js(self, script):
            return None

    api = SoundboardAPI(data_dir=str(tmp_path / "api_overlay"), start_background_services=False)
    api._set_window(PoisonWindow())
    res1 = api.toggle_mini_overlay()
    assert res1["ok"] is True
    assert res1["is_mini_overlay"] is True
    res2 = api.toggle_mini_overlay()
    assert res2["ok"] is True
    assert res2["is_mini_overlay"] is False









