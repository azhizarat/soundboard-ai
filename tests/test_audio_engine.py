import os
import threading

import numpy as np
import soundfile as sf

from audio_engine import AudioEngine, PlaybackSession, trim_and_normalize


def _write_wav(path, frames=4000, sr=48000, freq=440.0):
    t = np.linspace(0.0, frames / sr, frames, endpoint=False)
    wave = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    stereo = np.column_stack([wave, wave])
    sf.write(str(path), stereo, sr)
    return path


def _key(path):
    return os.path.normcase(os.path.abspath(str(path)))


def test_cache_evicts_oldest_and_counts_bytes(tmp_path):
    engine = AudioEngine(start_loopback=False)
    p1 = _write_wav(tmp_path / "a.wav", freq=220.0)
    p2 = _write_wav(tmp_path / "b.wav", freq=330.0)
    p3 = _write_wav(tmp_path / "c.wav", freq=440.0)

    data, duration = engine.load_clip_stereo_48k(str(p1))
    entry_bytes = data.nbytes
    assert entry_bytes > 0
    assert duration > 0
    # Room for two clips only: loading the third must evict the oldest.
    engine._cache_budget_bytes = entry_bytes * 2 + entry_bytes // 2

    engine.load_clip_stereo_48k(str(p2))
    engine.load_clip_stereo_48k(str(p3))

    k1, k2, k3 = _key(p1), _key(p2), _key(p3)
    assert k1 not in engine._cache
    assert k2 in engine._cache
    assert k3 in engine._cache
    assert engine._cache_bytes <= engine._cache_budget_bytes
    assert engine._cache_bytes == (
        engine._cache[k2]["data"].nbytes + engine._cache[k3]["data"].nbytes
    )

    # A still-cached file is served from cache (same ndarray object).
    cached_data, cached_duration = engine.load_clip_stereo_48k(str(p2))
    assert cached_data is engine._cache[k2]["data"]
    assert cached_duration == engine._cache[k2]["duration"]
    assert k2 in engine._cache and k1 not in engine._cache


def test_invalidate_cache_single_and_all(tmp_path):
    engine = AudioEngine(start_loopback=False)
    p1 = _write_wav(tmp_path / "a.wav", freq=220.0)
    p2 = _write_wav(tmp_path / "b.wav", freq=330.0)

    data1, _ = engine.load_clip_stereo_48k(str(p1))
    data2, _ = engine.load_clip_stereo_48k(str(p2))
    k1, k2 = _key(p1), _key(p2)
    assert engine._cache_bytes == data1.nbytes + data2.nbytes

    engine.invalidate_cache(str(p1))
    assert k1 not in engine._cache
    assert k2 in engine._cache
    assert engine._cache_bytes == data2.nbytes

    engine.invalidate_cache()
    assert len(engine._cache) == 0
    assert engine._cache_bytes == 0


def test_get_session_is_strict_about_empty_play_id(tmp_path):
    engine = AudioEngine(start_loopback=False)
    s1 = PlaybackSession(play_id="id1", clip_id="c1", data=np.zeros((10, 2), dtype=np.float32))
    s2 = PlaybackSession(play_id="id2", clip_id="c2", data=np.zeros((10, 2), dtype=np.float32))
    with engine._lock:
        engine._sessions["id1"] = s1
        engine._sessions["id2"] = s2
        engine._latest_play_id = "id2"

    assert engine.get_session("") is None
    assert engine.get_session(None) is s2
    assert engine.get_session("id1") is s1
    assert engine.get_session("missing") is None


def test_play_clip_missing_file_returns_empty(tmp_path):
    engine = AudioEngine(start_loopback=False)
    result = engine.play_clip(
        {"id": "c1", "file_path": str(tmp_path / "nope.wav")},
        settings={"cable_device_id": 1, "monitor_device_id": 2},
    )
    assert result == ""
    assert engine._sessions == {}


def test_play_clip_without_active_route_returns_empty(tmp_path):
    engine = AudioEngine(start_loopback=False)
    wav = _write_wav(tmp_path / "clip.wav")

    result = engine.play_clip(
        {"id": "c1", "file_path": str(wav)},
        settings={
            "cable_device_id": 1,
            "monitor_device_id": 2,
            "cable_volume": 0.0,
            "monitor_volume": 0.0,
        },
    )

    # No session, no threads and no play count side effects.
    assert result == ""
    assert engine._sessions == {}
    assert engine._active_events == {}
    assert engine._latest_play_id is None


def test_monitor_only_without_monitor_device_has_no_route(tmp_path):
    engine = AudioEngine(start_loopback=False)
    wav = _write_wav(tmp_path / "clip.wav")

    result = engine.play_clip(
        {"id": "c1", "file_path": str(wav)},
        settings={
            "cable_device_id": 1,
            "monitor_device_id": None,
            "cable_volume": 1.0,
            "monitor_volume": 1.0,
        },
        monitor_only=True,
    )

    assert result == ""
    assert engine._sessions == {}


def test_monitor_only_on_cable_device_has_no_route(tmp_path):
    engine = AudioEngine(start_loopback=False)
    wav = _write_wav(tmp_path / "clip.wav")

    result = engine.play_clip(
        {"id": "c1", "file_path": str(wav)},
        settings={
            "cable_device_id": 1,
            "monitor_device_id": 1,
            "cable_volume": 1.0,
            "monitor_volume": 1.0,
        },
        monitor_only=True,
    )

    assert result == ""
    assert engine._sessions == {}


def test_invalid_route_volume_returns_empty_without_raising(tmp_path):
    engine = AudioEngine(start_loopback=False)
    wav = _write_wav(tmp_path / "clip.wav")

    result = engine.play_clip(
        {"id": "c1", "file_path": str(wav)},
        settings={
            "cable_device_id": 1,
            "monitor_device_id": 2,
            "cable_volume": "abc",
            "monitor_volume": 1.0,
        },
    )

    assert result == ""
    assert engine._sessions == {}


def test_stale_devices_fail_preflight_before_creating_a_session(tmp_path, monkeypatch):
    import sounddevice as sd

    def _raise_for_every_device(**kwargs):
        raise RuntimeError("device index is stale")

    monkeypatch.setattr(sd, "check_output_settings", _raise_for_every_device)
    engine = AudioEngine(start_loopback=False)
    wav = _write_wav(tmp_path / "clip.wav")

    result = engine.play_clip(
        {"id": "c1", "file_path": str(wav)},
        settings={
            "cable_device_id": 1,
            "monitor_device_id": 2,
            "cable_volume": 1.0,
            "monitor_volume": 1.0,
        },
    )

    assert result == ""
    assert engine._sessions == {}


def test_play_clip_passes_concrete_device_ids_to_threads(tmp_path, monkeypatch):
    engine = AudioEngine(start_loopback=False, mixer_mode="legacy")
    wav = _write_wav(tmp_path / "clip.wav")
    monkeypatch.setattr(engine, "_output_route_usable", lambda device_id: True)

    calls = []
    both_started = threading.Event()

    def _record(session, device_id, base_gain, is_driver):
        calls.append((device_id, base_gain, is_driver))
        if len(calls) >= 2:
            both_started.set()

    monkeypatch.setattr(engine, "_stream_session_to_device", _record)

    play_id = engine.play_clip(
        {"id": "c1", "file_path": str(wav)},
        settings={
            "cable_device_id": 1,
            "monitor_device_id": 2,
            "cable_volume": 1.0,
            "monitor_volume": 0.5,
        },
    )

    assert play_id
    assert both_started.wait(2.0)
    assert sorted(c[0] for c in calls) == [1, 2]
    assert all(isinstance(c[0], int) for c in calls)
    by_device = {c[0]: c for c in calls}
    # The cable is the driver; the monitor only follows while the cable plays.
    assert by_device[1][2] is True
    assert by_device[2][2] is False
    engine.stop_all()


def test_cached_audio_is_read_only(tmp_path):
    engine = AudioEngine(start_loopback=False)
    wav = _write_wav(tmp_path / "clip.wav")

    data, _ = engine.load_clip_stereo_48k(str(wav))

    assert data.flags.writeable is False
    assert engine._cache[_key(wav)]["data"].flags.writeable is False
    # Processing never mutates the shared cache: it copies before writing.
    processed = trim_and_normalize(data, engine.TARGET_SR)
    assert processed.flags.writeable is True
    assert data.flags.writeable is False
