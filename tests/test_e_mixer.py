"""Pacote E: AudioMixer, DeviceMixer, MixerVoice and Limiter tests."""

from __future__ import annotations

import numpy as np
import pytest

from mixer import (
    AudioMixer,
    DeviceMixer,
    MixerVoice,
    apply_soft_limiter,
    CEILING_LINEAR,
    CROSSFADE_SAMPLES_10MS,
)


def test_soft_limiter_preserves_linear_signals():
    # Linear range below 90% ceiling (0.80)
    data = np.array([0.0, 0.2, -0.4, 0.7, -0.75], dtype=np.float32)
    limited = apply_soft_limiter(data)
    np.testing.assert_allclose(limited, data, atol=1e-5)


def test_soft_limiter_caps_extreme_signals():
    # Extreme signals above ceiling (+10 dB, +20 dB)
    extreme = np.array([1.5, 3.0, -10.0, 50.0], dtype=np.float32)
    limited = apply_soft_limiter(extreme)
    assert np.all(np.abs(limited) <= CEILING_LINEAR)
    assert np.all(limited[extreme > 0] > 0)
    assert np.all(limited[extreme < 0] < 0)


def test_device_mixer_sums_two_voices():
    dev = DeviceMixer(device_id=None, sr=48000, blocksize=512)
    sr = 48000
    tone1 = np.ones((1024, 2), dtype=np.float32) * 0.2
    tone2 = np.ones((1024, 2), dtype=np.float32) * 0.3

    v1 = MixerVoice("v1", "c1", tone1, base_gain=1.0)
    v2 = MixerVoice("v2", "c2", tone2, base_gain=1.0)

    dev.add_voice(v1)
    dev.add_voice(v2)

    block = dev.render_block(512, duck_gain=1.0)
    # 0.2 + 0.3 = 0.5
    np.testing.assert_allclose(block, 0.5, atol=1e-3)
    assert v1.pos_idx == 512
    assert v2.pos_idx == 512


def test_device_mixer_applies_ducking():
    dev = DeviceMixer(device_id=None, sr=48000, blocksize=512)
    tone = np.ones((1024, 2), dtype=np.float32) * 0.5
    v = MixerVoice("v", "c", tone, base_gain=1.0)
    dev.add_voice(v)

    # Duck to 0.25 (-12 dB is approx 0.25)
    dev._prev_duck = 0.25
    block = dev.render_block(512, duck_gain=0.25)
    np.testing.assert_allclose(block, 0.5 * 0.25, atol=1e-3)


def test_stop_with_crossfade():
    dev = DeviceMixer(device_id=None, sr=48000, blocksize=CROSSFADE_SAMPLES_10MS)
    tone = np.ones((2000, 2), dtype=np.float32) * 0.6
    v = MixerVoice("v_stop", "c", tone, base_gain=1.0)
    dev.add_voice(v)

    # First block before stop: full volume
    b1 = dev.render_block(100, duck_gain=1.0)
    np.testing.assert_allclose(b1, 0.6, atol=1e-3)

    # Trigger stop (10 ms crossfade = 480 samples)
    v.stop(fade_ms=10.0)
    assert v.stopping is True

    # Render through the crossfade
    fade_block = dev.render_block(CROSSFADE_SAMPLES_10MS, duck_gain=1.0)
    # Beginning of fade block should be near 0.6, ending near 0.0
    assert fade_block[0, 0] > 0.5
    assert fade_block[-1, 0] < 0.05
    assert v.stop_event.is_set()


def test_voice_pause_and_resume():
    dev = DeviceMixer(device_id=None, sr=48000, blocksize=512)
    tone = np.ones((2000, 2), dtype=np.float32) * 0.4
    v = MixerVoice("v_pause", "c", tone, base_gain=1.0)
    dev.add_voice(v)

    b1 = dev.render_block(512, duck_gain=1.0)
    assert v.pos_idx == 512
    np.testing.assert_allclose(b1, 0.4, atol=1e-3)

    v.pause()
    b2 = dev.render_block(512, duck_gain=1.0)
    # Paused output must be silence and pos_idx must not advance
    np.testing.assert_allclose(b2, 0.0, atol=1e-6)
    assert v.pos_idx == 512

    v.resume()
    b3 = dev.render_block(512, duck_gain=1.0)
    assert v.pos_idx == 1024
    np.testing.assert_allclose(b3, 0.4, atol=1e-3)


def test_voice_loop():
    dev = DeviceMixer(device_id=None, sr=48000, blocksize=512)
    # Short clip of 400 samples
    tone = np.ones((400, 2), dtype=np.float32) * 0.3
    v = MixerVoice("v_loop", "c", tone, base_gain=1.0, loop=True)
    dev.add_voice(v)

    # Rendering 512 frames from a 400 sample clip wraps around and continues playing
    block = dev.render_block(512, duck_gain=1.0)
    assert not v.stop_event.is_set()
    assert v.pos_idx == 112  # 512 - 400 = 112
    np.testing.assert_allclose(block, 0.3, atol=1e-3)


def test_voice_seek():
    tone = np.ones((48000 * 2, 2), dtype=np.float32)  # 2 seconds
    v = MixerVoice("v_seek", "c", tone, sr=48000)
    assert v.pos_idx == 0

    v.seek_to_seconds(1.5)
    assert v.pos_idx == int(1.5 * 48000)

    v.seek_relative(-0.5)
    assert v.pos_idx == int(1.0 * 48000)


def _write_test_wav(path, frames=48000, sr=48000, freq=440.0):
    import soundfile as sf
    t = np.linspace(0.0, frames / sr, frames, endpoint=False)
    wave = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    stereo = np.column_stack([wave, wave])
    sf.write(str(path), stereo, sr)
    return str(path)


def test_audio_engine_with_mixer_mode_registers_and_controls_voice(tmp_path, monkeypatch):
    from audio_engine import AudioEngine

    engine = AudioEngine(start_loopback=False, mixer_mode="mixer")
    assert engine.mixer_mode == "mixer"
    assert engine._mixer is not None

    # Stub hardware audio preflight and stream start
    monkeypatch.setattr(engine, "_output_route_usable", lambda dev: True)
    monkeypatch.setattr(engine._mixer.cable_mixer, "start_stream", lambda: True)
    monkeypatch.setattr(engine._mixer.monitor_mixer, "start_stream", lambda: True)

    wav = _write_test_wav(tmp_path / "sound.wav")
    clip = {"id": "c_mix", "title": "Buzzer", "file_path": wav, "volume": 1.0}
    settings = {"cable_device_id": 1, "monitor_device_id": 2, "cable_volume": 1.0, "monitor_volume": 0.8}

    play_id = engine.play_clip(clip, settings)
    assert play_id
    assert play_id in engine._sessions

    sess = engine.get_session(play_id)
    assert sess is not None
    assert sess.clip_id == "c_mix"
    assert sess.title == "Buzzer"

    st = sess.status()
    assert st["playing"] is True
    assert st["paused"] is False

    # Test control methods
    assert sess.toggle_pause() is True
    assert sess.status()["paused"] is True
    assert sess.toggle_pause() is False

    sess.seek(0.5)
    assert np.isclose(sess.status()["position"], 0.5, atol=0.05)

    sess.set_live_volume(1.75)
    assert np.isclose(sess.status()["volume"], 1.75)

    engine.stop_all()
    assert sess.stopping is True


def test_mixer_duck_envelope_sync(tmp_path):
    from audio_engine import AudioEngine

    engine = AudioEngine(start_loopback=False, mixer_mode="mixer")
    assert engine._mixer._get_duck_gain() == 1.0

    engine._duck_gain = 0.25
    assert engine._mixer._get_duck_gain() == 0.25


def test_api_playback_controls_with_mixer(tmp_path, monkeypatch):
    from main import SoundboardAPI

    api = SoundboardAPI(data_dir=str(tmp_path / "data_api_mix"), start_background_services=False)
    assert api._audio.mixer_mode == "mixer"

    monkeypatch.setattr(api._audio, "_output_route_usable", lambda dev: True)
    monkeypatch.setattr(api._audio._mixer.cable_mixer, "start_stream", lambda: True)
    monkeypatch.setattr(api._audio._mixer.monitor_mixer, "start_stream", lambda: True)

    wav = _write_test_wav(tmp_path / "clip_api.wav")
    clip = api._store.add_or_update_clip({"title": "API Clip", "file_path": wav, "volume": 1.0})
    api._store.update_settings({"cable_device_id": 1, "monitor_device_id": 2})

    res = api.play_clip(clip["id"])
    assert res["ok"] is True
    pid = res["play_id"]

    p_res = api.pause_resume_playback(pid)
    assert p_res["ok"] is True
    assert p_res["paused"] is True

    s_res = api.seek_playback(0.3, pid)
    assert s_res["ok"] is True

    v_res = api.set_live_clip_volume(1.5, pid)
    assert v_res["ok"] is True
    assert np.isclose(v_res["volume"], 1.5)

    stop_res = api.stop_all()
    assert stop_res["ok"] is True

