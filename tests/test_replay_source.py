"""Replay source selection: loopback device choice, status/level and API wiring."""

import time

import numpy as np
import pytest

from audio_engine import AudioEngine, pick_loopback_device


LOOPBACKS = [
    {"index": 19, "name": "CABLE Input (VB-Audio Virtual Cable) [Loopback]"},
    {"index": 20, "name": "Fones de ouvido (EDIFIER W820NB Plus) [Loopback]"},
    {"index": 21, "name": "Alto-falantes (Realtek(R) Audio) [Loopback]"},
]


# --- device choice -------------------------------------------------------------


def test_auto_mode_follows_windows_default_output():
    dev = pick_loopback_device(LOOPBACKS, "Alto-falantes (Realtek(R) Audio)", "")
    assert dev["index"] == 21


def test_auto_mode_never_falls_back_to_the_cable():
    dev = pick_loopback_device(LOOPBACKS, "Saida que nao existe", "")
    assert dev["index"] == 20


def test_fixed_mode_matches_saved_name_without_loopback_suffix():
    dev = pick_loopback_device(
        LOOPBACKS, "Alto-falantes (Realtek(R) Audio)", "Fones de ouvido (EDIFIER W820NB Plus)"
    )
    assert dev["index"] == 20


def test_fixed_mode_matches_truncated_name():
    dev = pick_loopback_device(LOOPBACKS, "", "Fones de ouvido (EDIFIER W820NB")
    assert dev["index"] == 20


def test_fixed_mode_missing_device_returns_none_instead_of_rerouting():
    assert pick_loopback_device(LOOPBACKS, "Alto-falantes (Realtek(R) Audio)", "Headset Sumido") is None


def test_no_loopbacks_returns_none():
    assert pick_loopback_device([], "x", "") is None


# --- engine state ------------------------------------------------------------------


def test_status_reports_mode_and_idle_state():
    engine = AudioEngine(start_loopback=False)
    st = engine.loopback_status()
    assert st["mode"] == "auto"
    assert st["active"] is False
    assert st["receiving"] is False
    assert st["level_db"] <= -90

    engine.set_loopback_source("Fones de ouvido (EDIFIER W820NB Plus)")
    st = engine.loopback_status()
    assert st["mode"] == "fixed"
    assert st["source_name"] == "Fones de ouvido (EDIFIER W820NB Plus)"


def test_set_source_does_not_start_capture_when_loopback_disabled():
    engine = AudioEngine(start_loopback=False)
    engine.set_loopback_source("Qualquer")
    assert engine._loopback_thread is None


def test_set_source_restarts_running_capture(monkeypatch):
    engine = AudioEngine(start_loopback=False)
    starts = []
    monkeypatch.setattr(engine, "start_loopback_capture", lambda: starts.append(engine._loopback_source))
    engine._loopback_enabled = True

    engine.set_loopback_source("Fones de ouvido (EDIFIER W820NB Plus)")
    engine.set_loopback_source("Fones de ouvido (EDIFIER W820NB Plus)")  # unchanged: no restart

    assert starts == ["Fones de ouvido (EDIFIER W820NB Plus)"]


def test_level_meter_tracks_recent_chunks():
    engine = AudioEngine(start_loopback=False)
    chunk = np.full((480, 2), 0.5, dtype=np.float32)
    engine._note_loopback_chunk(chunk)

    st = engine.loopback_status()
    assert st["receiving"] is True
    assert -7.0 < st["level_db"] < -5.0  # 0.5 peak ~= -6 dBFS


def test_level_meter_decays_to_silence(monkeypatch):
    engine = AudioEngine(start_loopback=False)
    engine._note_loopback_chunk(np.full((480, 2), 0.5, dtype=np.float32))
    real = time.monotonic()
    monkeypatch.setattr("audio_engine.time.monotonic", lambda: real + 5.0)

    st = engine.loopback_status()
    assert st["receiving"] is False
    assert st["level_db"] <= -90


def test_capture_stops_promptly_when_device_is_silent(monkeypatch):
    """A silent WASAPI loopback must never block shutdown/restart."""
    import sys
    import types

    class _Stream:
        def get_read_available(self):
            return 0  # silent device: no frames ever

        def read(self, n, exception_on_overflow=False):  # pragma: no cover - must not be called
            raise AssertionError("blocking read on silent device")

        def stop_stream(self):
            pass

        def close(self):
            pass

    class _PA:
        paWASAPI = 13
        paFloat32 = 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_host_api_info_by_type(self, _):
            return {"defaultOutputDevice": 0}

        def get_device_info_by_index(self, _):
            return {"name": "Fones de ouvido (EDIFIER W820NB Plus)"}

        def get_loopback_device_info_generator(self):
            return iter([{"index": 20, "name": "Fones de ouvido (EDIFIER W820NB Plus) [Loopback]",
                          "defaultSampleRate": 44100.0, "maxInputChannels": 2}])

        def open(self, **kw):
            return _Stream()

    fake = types.SimpleNamespace(PyAudio=_PA, paWASAPI=13, paFloat32=1)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake)

    engine = AudioEngine(start_loopback=False)
    engine.set_loopback_source("Fones de ouvido (EDIFIER W820NB Plus)")
    engine.start_loopback_capture()
    deadline = time.time() + 2.0
    while time.time() < deadline and not engine.loopback_status()["active"]:
        time.sleep(0.01)
    assert engine.loopback_status()["active"] is True
    assert engine.loopback_status()["device_name"] == "Fones de ouvido (EDIFIER W820NB Plus)"
    assert engine.loopback_status()["samplerate"] == 44100

    started = time.time()
    engine.stop_loopback_capture()
    assert time.time() - started < 1.0
    assert engine.loopback_status()["active"] is False


# --- API wiring ------------------------------------------------------------------


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from main import SoundboardAPI

    api = SoundboardAPI(data_dir=str(tmp_path / "data"), start_background_services=False)
    api._hotkeys.enabled = False
    monkeypatch.setattr(api._audio, "set_mic_passthrough", lambda *a, **k: None)
    return api


def test_default_setting_is_auto(api):
    assert api._store.state["settings"]["replay_source_name"] == ""


def test_update_settings_switches_engine_source(api, monkeypatch):
    calls = []
    monkeypatch.setattr(api._audio, "set_loopback_source", lambda name: calls.append(name))

    s = api.update_settings({"replay_source_name": "  Fones de ouvido (EDIFIER W820NB Plus)  "})

    assert s["replay_source_name"] == "Fones de ouvido (EDIFIER W820NB Plus)"
    assert calls == ["Fones de ouvido (EDIFIER W820NB Plus)"]


def test_saved_source_is_applied_on_startup(tmp_path):
    from main import SoundboardAPI
    from store import SoundboardStore

    store = SoundboardStore(data_dir=str(tmp_path / "data"))
    store.update_settings({"replay_source_name": "Alto-falantes (Realtek(R) Audio)"})
    store.flush()

    api = SoundboardAPI(data_dir=str(tmp_path / "data"), start_background_services=False)
    assert api._audio.loopback_status()["source_name"] == "Alto-falantes (Realtek(R) Audio)"


def test_api_exposes_status_and_sources(api, monkeypatch):
    monkeypatch.setattr(api._audio, "list_loopback_sources", lambda: {"sources": [{"name": "X"}], "default_name": "X"})
    assert api.list_replay_sources()["sources"] == [{"name": "X"}]
    assert api.get_replay_status()["mode"] == "auto"
