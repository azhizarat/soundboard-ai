"""Regression tests for API state isolation, device rebinding and persistence."""

import json
import os
import time

import numpy as np
import pytest
import soundfile as sf

from audio_engine import PlaybackSession
from main import SoundboardAPI
from store import SoundboardStore


def _write_wav(path, frames=4800, sr=48000, freq=440.0):
    t = np.linspace(0.0, frames / sr, frames, endpoint=False)
    wave = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sf.write(str(path), np.column_stack([wave, wave]), sr)
    return str(path)


def _key(path):
    return os.path.normcase(os.path.abspath(str(path)))


@pytest.fixture()
def api(tmp_path):
    api = SoundboardAPI(data_dir=str(tmp_path / "data"), start_background_services=False)
    # Never install real global hotkeys from tests.
    api._hotkeys.enabled = False
    return api


def _devices():
    return {
        "outputs": [
            {"id": 7, "name": "CABLE Input (VB-Audio Virtual Cable)", "hostapi": "Windows WASAPI"}
        ],
        "inputs": [{"id": 9, "name": "Mic", "hostapi": "Windows WASAPI"}],
        "detected_cable_id": None,
        "default_output_id": 7,
        "default_input_id": 9,
    }


def _patch_devices(monkeypatch, api, payload=None):
    monkeypatch.setattr(api._audio, "list_devices", lambda: payload or _devices())


def test_play_clip_missing_file_does_not_fake_success(api, tmp_path):
    clip = api._store.add_or_update_clip({
        "title": "Arquivo sumido",
        "file_path": str(tmp_path / "does_not_exist.wav"),
        "duration": 1.0,
    })
    assert clip["play_count"] == 0

    res = api.play_clip(clip["id"])

    assert res["ok"] is False
    assert res["error"] == "Arquivo de áudio não encontrado."
    assert api._store.get_clip(clip["id"])["play_count"] == 0


def test_play_clip_without_route_reports_inactive_output(api, tmp_path):
    wav = _write_wav(tmp_path / "muted.wav")
    clip = api._store.add_or_update_clip({"title": "Mudo", "file_path": wav, "duration": 0.1})
    api._store.update_settings({"cable_volume": 0.0, "monitor_volume": 0.0})

    res = api.play_clip(clip["id"])

    assert res["ok"] is False
    assert res["error"] == "Nenhuma saída de áudio ativa (cabo/fone mudos ou dispositivo indisponível)."
    assert api._store.get_clip(clip["id"])["play_count"] == 0


def test_play_clip_from_hotkey_emits_warning_toast(api, tmp_path, monkeypatch):
    clip = api._store.add_or_update_clip({
        "title": "Arquivo sumido",
        "file_path": str(tmp_path / "does_not_exist.wav"),
        "duration": 1.0,
    })
    calls = []
    monkeypatch.setattr(api, "_notify_ui", lambda name, payload=None: calls.append((name, payload)))

    res = api.play_clip(clip["id"], from_hotkey=True)

    assert res["ok"] is False
    assert res["error"] == "Arquivo de áudio não encontrado."
    assert api._store.get_clip(clip["id"])["play_count"] == 0
    toasts = [payload for name, payload in calls if name == "toast"]
    assert toasts
    assert toasts[-1] == {"type": "warning", "message": "Arquivo de áudio não encontrado."}


def test_live_volume_is_persisted_after_flush(api, tmp_path):
    wav = _write_wav(tmp_path / "live.wav")
    clip = api._store.add_or_update_clip({"title": "Live", "file_path": wav, "duration": 0.1})

    session = PlaybackSession(
        play_id="live1",
        clip_id=clip["id"],
        data=np.zeros((480, 2), dtype=np.float32),
        sr=48000,
        live_volume=1.0,
    )
    with api._audio._lock:
        api._audio._sessions["live1"] = session
        api._audio._latest_play_id = "live1"

    res = api.set_live_clip_volume(0.42)
    assert res["ok"] is True
    assert api._store.get_clip(clip["id"])["volume"] == 0.42

    # The debounced save must be flusheable without waiting for the timer.
    api._store.flush()

    fresh = SoundboardStore(data_dir=str(tmp_path / "data"))
    persisted = next(c for c in fresh.state["clips"] if c["id"] == clip["id"])
    assert persisted["volume"] == 0.42


def test_live_volume_persists_when_clip_has_no_volume_key(api, tmp_path):
    wav = _write_wav(tmp_path / "legacy.wav")
    clip = api._store.add_or_update_clip({"title": "Legacy", "file_path": wav, "duration": 0.1})
    # Simulate a clip imported before the volume field existed.
    api._store.state["clips"][0].pop("volume", None)

    session = PlaybackSession(
        play_id="legacy1",
        clip_id=clip["id"],
        data=np.zeros((480, 2), dtype=np.float32),
        sr=48000,
        live_volume=1.0,
    )
    with api._audio._lock:
        api._audio._sessions["legacy1"] = session
        api._audio._latest_play_id = "legacy1"

    res = api.set_live_clip_volume(0.33)
    assert res["ok"] is True

    api._store.flush()

    fresh = SoundboardStore(data_dir=str(tmp_path / "data"))
    persisted = next(c for c in fresh.state["clips"] if c["id"] == clip["id"])
    assert persisted["volume"] == 0.33


def test_get_initial_state_is_detached_from_live_store(api):
    api._store.add_or_update_clip({"title": "Original", "file_path": "clips/original.wav"})

    state = api.get_initial_state()
    assert state["clips"][0]["title"] == "Original"

    state["clips"][0]["title"] = "Mutado pela UI"
    state["clips"].append({"id": "fantasma", "title": "Fantasma"})
    state["settings"]["cable_volume"] = 0.0

    assert api._store.state["clips"][0]["title"] == "Original"
    assert len(api._store.state["clips"]) == 1
    assert api._store.state["settings"]["cable_volume"] != 0.0

    # A previously captured snapshot must not follow later live mutations.
    before = api.get_initial_state()
    api._store.state["clips"][0]["title"] = "Alterado ao vivo"
    assert before["clips"][0]["title"] == "Original"


def test_stale_device_id_is_rebound_by_saved_name(api, monkeypatch):
    _patch_devices(monkeypatch, api)
    settings = api._store.state["settings"]
    settings["cable_device_id"] = 99
    settings["cable_device_name"] = "CABLE Input (VB-Audio Virtual Cable)"

    api._auto_bind_devices()

    assert settings["cable_device_id"] == 7
    assert settings["cable_device_name"] == "CABLE Input (VB-Audio Virtual Cable)"


def test_unknown_saved_device_is_not_silently_rerouted(api, monkeypatch):
    _patch_devices(monkeypatch, api)
    settings = api._store.state["settings"]
    settings["cable_device_id"] = 99
    settings["cable_device_name"] = "Device That Does Not Exist"

    api._auto_bind_devices()

    assert settings["cable_device_id"] is None
    assert settings["cable_device_name"] == "Device That Does Not Exist"


def test_auto_bind_stores_names_for_default_devices(api, monkeypatch):
    _patch_devices(monkeypatch, api)
    settings = api._store.state["settings"]
    for key in ("cable_device_id", "monitor_device_id", "mic_device_id"):
        settings[key] = None
    for key in ("cable_device_name", "monitor_device_name", "mic_device_name"):
        settings[key] = ""

    api._auto_bind_devices()

    # No cable detected, so nothing may be routed to the generic output.
    assert settings["cable_device_id"] is None
    assert settings["monitor_device_id"] == 7
    assert settings["monitor_device_name"] == "CABLE Input (VB-Audio Virtual Cable)"
    assert settings["mic_device_id"] == 9
    assert settings["mic_device_name"] == "Mic"


def test_legacy_install_migrates_empty_names_to_role_defaults(api, monkeypatch, tmp_path):
    _patch_devices(monkeypatch, api, {
        "outputs": [
            {"id": 7, "name": "Speakers", "hostapi": "Windows WASAPI"},
            {"id": 12, "name": "CABLE Input (VB-Audio Virtual Cable)", "hostapi": "Windows WASAPI"},
        ],
        "inputs": [{"id": 9, "name": "Mic", "hostapi": "Windows WASAPI"}],
        "detected_cable_id": 12,
        "default_output_id": 7,
        "default_input_id": 9,
    })
    settings = api._store.state["settings"]
    settings.update({
        "cable_device_id": 99,
        "cable_device_name": "",
        "monitor_device_id": 98,
        "monitor_device_name": "",
        "mic_device_id": 97,
        "mic_device_name": "",
    })

    api._auto_bind_devices()

    settings = api._store.state["settings"]
    assert settings["cable_device_id"] == 12
    assert settings["cable_device_name"] == "CABLE Input (VB-Audio Virtual Cable)"
    assert settings["monitor_device_id"] == 7
    assert settings["monitor_device_name"] == "Speakers"
    assert settings["mic_device_id"] == 9
    assert settings["mic_device_name"] == "Mic"
    assert not {99, 98, 97} & {
        settings["cable_device_id"],
        settings["monitor_device_id"],
        settings["mic_device_id"],
    }

    # The migration was persisted, not only applied in memory.
    fresh = SoundboardStore(data_dir=str(tmp_path / "data"))
    assert fresh.state["settings"]["cable_device_id"] == 12
    assert fresh.state["settings"]["cable_device_name"] == "CABLE Input (VB-Audio Virtual Cable)"


def test_update_settings_stores_resolved_device_name(api, monkeypatch):
    _patch_devices(monkeypatch, api)

    api.update_settings({"cable_device_id": 7})

    settings = api._store.state["settings"]
    assert settings["cable_device_id"] == 7
    assert settings["cable_device_name"] == "CABLE Input (VB-Audio Virtual Cable)"


def test_selecting_missing_device_clears_stale_saved_name(api, monkeypatch):
    _patch_devices(monkeypatch, api)
    api._store.update_settings({"cable_device_name": "Old Device"})

    api.update_settings({"cable_device_id": 999})

    settings = api._store.state["settings"]
    assert settings["cable_device_id"] == 999
    assert settings["cable_device_name"] == ""


def test_delete_clip_invalidates_audio_cache(api, tmp_path):
    wav = _write_wav(tmp_path / "cached.wav")
    clip = api._store.add_or_update_clip({"title": "Cache", "file_path": wav, "duration": 0.1})

    api._audio.load_clip_stereo_48k(wav)
    key = _key(wav)
    assert key in api._audio._cache
    bytes_before = api._audio._cache_bytes

    res = api.delete_clip(clip["id"])

    assert res["ok"] is True
    assert key not in api._audio._cache
    assert api._audio._cache_bytes < bytes_before


def test_save_failure_keeps_dirty_and_removes_tmp_file(tmp_path, monkeypatch):
    import store as store_module

    store = SoundboardStore(data_dir=str(tmp_path / "data"))
    store.state["clips"].append({"id": "c1", "title": "Pendente"})
    tmp_path_file = store.db_path.with_suffix(".json.tmp")
    real_replace = os.replace

    def _locked(src, dst):
        raise PermissionError("file is locked by another process")

    monkeypatch.setattr(store_module.os, "replace", _locked)

    with pytest.raises(PermissionError):
        store.save()

    assert store._dirty is True
    assert not tmp_path_file.exists()
    assert not store.db_path.exists()

    # Restoring the filesystem lets the next flush retry successfully.
    monkeypatch.setattr(store_module.os, "replace", real_replace)
    store.flush()

    assert store.db_path.exists()
    assert store._dirty is False
    assert not tmp_path_file.exists()
    persisted = json.loads(store.db_path.read_text(encoding="utf-8"))
    assert persisted["clips"][0]["title"] == "Pendente"


def test_save_debounced_fires_on_its_own(tmp_path):
    store = SoundboardStore(data_dir=str(tmp_path / "data"))
    store.state["clips"].append({"id": "c1", "title": "Debounced"})

    store.save_debounced(delay=0.01)

    deadline = time.time() + 2.0
    while time.time() < deadline and not store.db_path.exists():
        time.sleep(0.01)

    assert store.db_path.exists()
    assert store._dirty is False
    persisted = json.loads(store.db_path.read_text(encoding="utf-8"))
    assert persisted["clips"][0]["title"] == "Debounced"
