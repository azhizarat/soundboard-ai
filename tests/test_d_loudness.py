"""Pacote D: Loudness (EBU R128 / BS.1770-4) measurement and gain tests."""

from __future__ import annotations

import math
import numpy as np
import pytest

import loudness


def test_sine_1khz_at_minus_23_dbfs():
    sr = 48000
    duration = 5.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    rms_target = 10.0 ** (-23.0 / 20.0)
    amplitude = np.sqrt(2.0) * rms_target
    sine = (amplitude * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)

    lufs, is_estimated = loudness.integrated_lufs(sine, sr=sr)
    assert is_estimated is False
    assert lufs is not None
    # Must be within -23 +/- 0.3 LUFS
    assert abs(lufs - (-23.0)) <= 0.3 + 1e-5


def test_pure_silence_returns_none():
    sr = 48000
    silence = np.zeros(sr * 2, dtype=np.float32)
    lufs, _ = loudness.integrated_lufs(silence, sr=sr)
    assert lufs is None

    res = loudness.analyze_audio_loudness(silence, sr=sr)
    assert res["lufs"] is None
    assert res["loudness_gain_db"] == 0.0


def test_gating_ignores_long_silence_blocks():
    sr = 48000
    # 2 seconds of 1 kHz tone at -20 LUFS + 5 seconds of silence
    t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
    tone = (0.2 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    silence = np.zeros(int(sr * 5.0), dtype=np.float32)
    signal_with_silence = np.concatenate([tone, silence])

    lufs_alone, _ = loudness.integrated_lufs(tone, sr=sr)
    lufs_combined, _ = loudness.integrated_lufs(signal_with_silence, sr=sr)

    assert lufs_alone is not None
    assert lufs_combined is not None
    # Gating should filter out the 5s of silence so lufs_combined matches lufs_alone
    assert abs(lufs_combined - lufs_alone) <= 0.5


def test_short_clip_uses_estimated_flag():
    sr = 48000
    duration = 0.2  # 200 ms < 400 ms
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    short_tone = (0.1 * np.sin(2 * np.pi * 500 * t)).astype(np.float32)

    lufs, is_estimated = loudness.integrated_lufs(short_tone, sr=sr)
    assert is_estimated is True
    assert lufs is not None


def test_gain_clamping_and_ceiling():
    # If audio is very quiet (e.g. -40 LUFS), target is -16 LUFS -> delta = +24 dB, clamped to +12 dB
    gain = loudness.calculate_loudness_gain(-40.0, peak_abs=0.01)
    assert gain == 12.0

    # If audio is very loud (e.g. 0 LUFS), target is -16 LUFS -> delta = -16 dB, clamped to -12 dB
    gain = loudness.calculate_loudness_gain(0.0, peak_abs=0.5)
    assert gain == -12.0

    # If audio is -20 LUFS (needs +4 dB), but peak is 0.95 (-0.44 dBFS):
    # headroom to -1.0 dBFS is -0.56 dB, so gain must not exceed headroom
    peak = 0.95
    gain = loudness.calculate_loudness_gain(-20.0, peak_abs=peak)
    peak_db = 20.0 * math.log10(peak)
    assert peak_db + gain <= -1.0 + 1e-4


def test_api_reanalyze_clip_loudness(tmp_path, monkeypatch):
    import soundfile as sf
    from main import SoundboardAPI

    api = SoundboardAPI(data_dir=str(tmp_path / "data"), start_background_services=False)
    api._hotkeys.enabled = False
    monkeypatch.setattr(api._hotkeys, "refresh", lambda *a, **k: None)

    # Create 1 second audio file
    sr = 48000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    sine = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    audio_path = tmp_path / "data" / "clips" / "teste_loud.wav"
    sf.write(str(audio_path), sine, sr)

    clip = api._store.add_or_update_clip({
        "id": "c_loud",
        "title": "Clip Loudness",
        "file_path": str(audio_path),
        "duration": 1.0,
    })

    res = api.reanalyze_clip_loudness("c_loud")
    assert res["ok"] is True
    assert res["clip"]["lufs"] is not None
    assert isinstance(res["clip"]["loudness_gain_db"], float)

    # Recalculate on trim
    edits = api.save_clip_edits({
        "id": "c_loud",
        "trim_start": 0.2,
        "trim_end": 0.8,
    })
    assert edits["ok"] is True
    assert edits["clip"]["trim_start"] == 0.2
    assert edits["clip"]["lufs"] is not None

