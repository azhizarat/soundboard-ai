"""Backend tests for F1: undo of clip deletion, ducking envelope and ducking settings."""

import numpy as np
import pytest
import soundfile as sf

from audio_engine import AudioEngine
from main import SoundboardAPI


@pytest.fixture
def api(tmp_path, monkeypatch):
    api = SoundboardAPI(data_dir=str(tmp_path / "data"), start_background_services=False)
    # Tests never touch real hardware: no global hotkeys and no mic stream.
    api._hotkeys.enabled = False
    refresh_calls = []
    monkeypatch.setattr(api._hotkeys, "refresh", lambda *a, **k: refresh_calls.append(a))
    api.refresh_calls = refresh_calls
    monkeypatch.setattr(api._audio, "set_mic_passthrough", lambda *a, **k: None)
    return api


def _add_clip(api, tmp_path):
    wav = api._store.clips_dir / "clip.wav"
    sf.write(str(wav), np.zeros((4800, 2), dtype=np.float32), 48000)
    clip = api._store.add_or_update_clip({
        "title": "Teste",
        "file_path": str(wav),
        "duration": 0.1,
        "category": "Call",
    })
    return clip


def test_delete_returns_snapshot_and_allows_undo(api, tmp_path):
    clip = _add_clip(api, tmp_path)
    res = api.delete_clip(clip["id"], delete_file=False)
    assert res["ok"] is True
    assert res["can_undo"] is True
    assert res["clip"]["id"] == clip["id"]
    assert api._store.get_clip(clip["id"]) is None
    assert len(api.refresh_calls) == 1

    restored = api.restore_clip(res["clip"])
    assert restored["ok"] is True
    assert api._store.get_clip(clip["id"])["title"] == "Teste"
    assert len(api.refresh_calls) == 2


def test_restore_rejects_missing_file(api):
    res = api.restore_clip({"id": "abc", "file_path": "C:/nao/existe.wav"})
    assert res["ok"] is False
    assert "não encontrado" in res["error"].lower()


def test_restore_rejects_missing_file_inside_clips_dir(api):
    missing = api._store.clips_dir / "sumiu.wav"
    res = api.restore_clip({"id": "abc", "file_path": str(missing)})
    assert res["ok"] is False
    assert "não encontrado" in res["error"].lower()


def test_restore_rejects_directory_inside_clips_dir(api):
    folder = api._store.clips_dir / "pasta"
    folder.mkdir(parents=True, exist_ok=True)
    for target in (folder, api._store.clips_dir):
        res = api.restore_clip({"id": "abc", "file_path": str(target)})
        assert res["ok"] is False
        assert "não encontrado" in res["error"].lower()


def test_restore_rejects_path_outside_clips_dir(api, tmp_path):
    outside = tmp_path / "evil.wav"
    sf.write(str(outside), np.zeros((100, 2), dtype=np.float32), 48000)
    res = api.restore_clip({"id": "abc", "file_path": str(outside)})
    assert res["ok"] is False


def test_restore_rejects_duplicate_id(api, tmp_path):
    clip = _add_clip(api, tmp_path)
    res = api.restore_clip(clip)
    assert res["ok"] is False
    assert "já existe" in res["error"].lower()


def test_restore_rejects_file_used_by_another_clip(api, tmp_path):
    clip = _add_clip(api, tmp_path)
    res = api.restore_clip({"id": "outro_botao", "file_path": clip["file_path"]})
    assert res["ok"] is False
    assert "outro botão" in res["error"].lower()
    assert api._store.get_clip("outro_botao") is None


def test_delete_with_file_cannot_be_undone(api, tmp_path):
    clip = _add_clip(api, tmp_path)
    res = api.delete_clip(clip["id"], delete_file=True)
    assert res["ok"] is True
    assert res["can_undo"] is False
    assert res["clip"] is not None


def test_ducking_disabled_keeps_unity_gain():
    engine = AudioEngine(start_loopback=False)
    engine.set_ducking(False, -12.0, -40.0)
    assert engine._duck_step(0.9, 1024 / 48000) == 1.0


def test_ducking_attack_reaches_amount_and_release_recovers():
    engine = AudioEngine(start_loopback=False)
    engine.set_ducking(True, -12.0, -40.0)
    block = 1024 / 48000

    for _ in range(12):
        gain = engine._duck_step(0.5, block)
    assert 0.23 <= gain <= 0.30, gain

    for _ in range(60):
        gain = engine._duck_step(0.0001, block)
    assert gain > 0.95, gain


def test_ducking_clamps_out_of_range_amount():
    engine = AudioEngine(start_loopback=False)
    engine.set_ducking(True, -999.0, -999.0)
    assert engine._duck_amount_db == -24.0
    assert engine._duck_threshold_dbfs == -60.0


def test_apply_mic_passthrough_pushes_ducking(api, monkeypatch):
    duck_calls = []
    mic_calls = []
    monkeypatch.setattr(api._audio, "set_ducking", lambda *a, **k: duck_calls.append((a, k)))
    monkeypatch.setattr(
        api._audio,
        "set_mic_passthrough",
        lambda *a, **k: mic_calls.append((a, k)),
    )
    api._store.update_settings({
        "duck_enabled": True,
        "duck_amount_db": -6.0,
        "duck_threshold_dbfs": -35.0,
    })

    api._apply_mic_passthrough()

    assert duck_calls[-1][0] == (True, -6.0, -35.0)
    assert len(mic_calls) == 1

    # A ducking-only change must never restart mic capture.
    api.set_ducking({"enabled": False})
    assert len(mic_calls) == 1
    assert duck_calls[-1][0][0] is False
    assert api._store.state["settings"]["duck_enabled"] is False


def test_set_ducking_survives_corrupt_settings(api):
    api._store.update_settings({"duck_amount_db": None, "duck_threshold_dbfs": "abc"})
    api._apply_ducking()
    assert api._audio._duck_amount_db == -12.0
    assert api._audio._duck_threshold_dbfs == -40.0


def test_set_ducking_positional_style(api):
    res = api.set_ducking(True, -6.0, -35.0)
    assert res["ok"] is True
    settings = api._store.state["settings"]
    assert settings["duck_enabled"] is True
    assert settings["duck_amount_db"] == -6.0
    assert settings["duck_threshold_dbfs"] == -35.0


def test_set_ducking_string_false_disables(api):
    api.set_ducking({"enabled": True})
    assert api._store.state["settings"]["duck_enabled"] is True
    res = api.set_ducking({"enabled": "false"})
    assert res["ok"] is True
    assert api._store.state["settings"]["duck_enabled"] is False


def test_set_ducking_persists_and_validates(api):
    res = api.set_ducking({"enabled": True, "amount_db": -6.0, "threshold_dbfs": -35.0})
    assert res["ok"] is True
    settings = api._store.state["settings"]
    assert settings["duck_enabled"] is True
    assert settings["duck_amount_db"] == -6.0
    assert settings["duck_threshold_dbfs"] == -35.0

    bad = api.set_ducking({"amount_db": "abc"})
    assert bad["ok"] is False
    assert api._store.state["settings"]["duck_amount_db"] == -6.0


def test_set_ducking_clamps_ranges(api):
    api.set_ducking({"enabled": True, "amount_db": -99.0})
    assert api._store.state["settings"]["duck_amount_db"] == -24.0
