import json
from pathlib import Path
import numpy as np
from store import SoundboardStore
from ai_analyzer import analyze_acoustics, generate_smart_metadata


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
    assert meta["category"] in ("Rage", "Risada", "Meme", "Call", "Efeito", "Clássico")
    assert meta["emoji"]
    assert meta["color"].startswith("#")

