"""F2 replay drafts: store purge, hidden drafts, commit and discard."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from main import SoundboardAPI
from store import SoundboardStore


def _wav(path, seconds=0.1):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.zeros((int(48000 * seconds), 2), dtype=np.float32), 48000)
    return path


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


def _fake_capture(api, monkeypatch, seconds=1.0):
    """Replaces mic-buffer capture + AI so tests never touch hardware."""
    def _save(out_path, last_seconds=None):
        _wav(Path(out_path), seconds)
        return str(out_path)

    monkeypatch.setattr(api._audio, "save_replay_wav", _save)
    monkeypatch.setattr(api._ai, "analyze_file", lambda *a, **k: {
        "title": "Replay teste", "duration": seconds, "transcript": "oi",
        "category": "Call", "speaker_ids": [], "tags": ["replay"],
    })


# --- Task 1: store ------------------------------------------------------------


def test_purge_pending_removes_clip_and_tmp_file(tmp_path):
    store = SoundboardStore(data_dir=str(tmp_path / "data"))
    draft_file = _wav(store.tmp_dir / "replay_draft.wav")
    keep_file = _wav(store.clips_dir / "keep.wav")
    store.add_or_update_clip({"id": "draft", "title": "Draft", "file_path": str(draft_file), "pending": True})
    store.add_or_update_clip({"id": "keep", "title": "Keep", "file_path": str(keep_file)})
    store.save()

    assert store.purge_pending_clips() == 1
    assert store.get_clip("draft") is None
    assert not draft_file.exists()
    assert keep_file.exists()
    assert store.get_clip("keep") is not None


def test_purge_pending_is_noop_when_empty(tmp_path):
    store = SoundboardStore(data_dir=str(tmp_path / "data"))
    assert store.tmp_dir.is_dir()
    assert store.purge_pending_clips() == 0


def test_purge_never_unlinks_outside_tmp(tmp_path):
    store = SoundboardStore(data_dir=str(tmp_path / "data"))
    outside = _wav(tmp_path / "outside.wav")
    store.add_or_update_clip({"id": "ext", "file_path": str(outside), "pending": True})

    assert store.purge_pending_clips() == 1
    assert store.get_clip("ext") is None
    assert outside.exists()


# --- Task 2: drafts -----------------------------------------------------------


def test_draft_is_hidden_and_file_lives_in_tmp(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    res = api.capture_replay_draft(5)
    assert res["ok"] is True
    clip = res["clip"]
    assert clip["pending"] is True
    assert Path(clip["file_path"]).is_file()
    assert Path(clip["file_path"]).is_relative_to(api._store.tmp_dir)
    assert all(not c.get("pending") for c in res["clips"])
    assert all(not c.get("pending") for c in api.get_initial_state()["clips"])
    assert len(api.refresh_calls) == 0


def test_commit_moves_file_and_publishes(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    res = api.commit_replay_draft(clip["id"])
    assert res["ok"] is True
    saved = api._store.get_clip(clip["id"])
    assert not saved.get("pending")
    assert Path(saved["file_path"]).is_file()
    assert Path(saved["file_path"]).is_relative_to(api._store.clips_dir)
    assert not Path(clip["file_path"]).exists()
    assert any(c["id"] == clip["id"] for c in res["clips"])
    assert len(api.refresh_calls) == 1
    assert res["split_count"] == 0


def test_discard_deletes_clip_and_file(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    invalidated = []
    monkeypatch.setattr(api._audio, "invalidate_cache", lambda *a: invalidated.append(a))

    res = api.discard_replay_draft(clip["id"])
    assert res["ok"] is True
    assert api._store.get_clip(clip["id"]) is None
    assert not Path(clip["file_path"]).exists()
    assert all(c["id"] != clip["id"] for c in res["clips"])
    assert invalidated and invalidated[0][0] == clip["file_path"]


def test_commit_requires_pending(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    api.commit_replay_draft(clip["id"])
    res = api.commit_replay_draft(clip["id"])
    assert res["ok"] is False
    assert "rascunho" in res["error"].lower()


def test_commit_rejects_missing_draft(api):
    res = api.commit_replay_draft("nao_existe")
    assert res["ok"] is False
    assert "rascunho" in res["error"].lower()


def test_commit_rejects_file_outside_tmp(api):
    outside = _wav(api._store.clips_dir / "fora.wav")
    clip = api._store.add_or_update_clip({
        "title": "Fora", "file_path": str(outside), "pending": True, "duration": 0.1,
    })
    res = api.commit_replay_draft(clip["id"])
    assert res["ok"] is False
    assert "não encontrado" in res["error"].lower()
    assert outside.exists()
    assert api._store.get_clip(clip["id"])["pending"] is True


def test_commit_rejects_draft_whose_file_vanished(api):
    clip = api._store.add_or_update_clip({
        "title": "Sumiu",
        "file_path": str(api._store.tmp_dir / "nunca_existiu.wav"),
        "pending": True,
    })
    res = api.commit_replay_draft(clip["id"])
    assert res["ok"] is False
    assert "não encontrado" in res["error"].lower()


def test_discard_requires_pending(api):
    path = _wav(api._store.clips_dir / "normal.wav")
    clip = api._store.add_or_update_clip({"title": "Normal", "file_path": str(path), "duration": 0.1})

    res = api.discard_replay_draft(clip["id"])
    assert res["ok"] is False
    assert api._store.get_clip(clip["id"]) is not None
    assert path.exists()
    assert api.discard_replay_draft("nao_existe")["ok"] is False


def test_discard_never_unlinks_outside_tmp(api):
    path = _wav(api._store.clips_dir / "fora_discard.wav")
    clip = api._store.add_or_update_clip({"title": "Fora", "file_path": str(path), "pending": True})

    res = api.discard_replay_draft(clip["id"])
    assert res["ok"] is True
    assert api._store.get_clip(clip["id"]) is None
    assert path.exists()


def test_edit_draft_stays_hidden(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]

    res = api.save_clip_edits({"id": clip["id"], "title": "Titulo revisado"})
    assert res["clip"]["title"] == "Titulo revisado"
    assert api._store.get_clip(clip["id"])["pending"] is True
    assert api.refresh_calls, "save_clip_edits must refresh hotkeys"
    assert all(not c.get("pending") for c in api.refresh_calls[-1][0])


def test_draft_capture_emits_no_replay_event(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    events = []
    monkeypatch.setattr(api, "_notify_ui", lambda name, payload=None: events.append(name))

    assert api.capture_replay_draft(5)["ok"] is True
    assert "replay_captured" not in events
    assert "clips_updated" not in events


def test_hotkey_capture_still_saves_and_notifies(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    events = []
    monkeypatch.setattr(api, "_notify_ui", lambda name, payload=None: events.append((name, payload)))

    res = api.capture_replay_clip(5)
    assert res["ok"] is True
    assert "replay_captured" in [e[0] for e in events]
    assert api._store.get_clip(res["clip"]["id"]) is not None
    assert not api._store.get_clip(res["clip"]["id"]).get("pending")
    assert Path(res["clip"]["file_path"]).is_relative_to(api._store.clips_dir)
    assert len(api.refresh_calls) == 1


def test_draft_capture_without_audio_returns_error(api, monkeypatch):
    monkeypatch.setattr(api._audio, "save_replay_wav", lambda *a, **k: "")

    res = api.capture_replay_draft(5)
    assert res["ok"] is False
    assert "nenhum áudio" in res["error"].lower()
    assert api._store.state["clips"] == []


def test_draft_commit_refreshes_hotkeys_once(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    api.commit_replay_draft(clip["id"])
    assert len(api.refresh_calls) == 1
    assert any(c["id"] == clip["id"] for c in api.refresh_calls[0][0])


def test_boot_purges_old_drafts(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    data_dir = str(api._store.data_dir)
    api._store.flush()

    fresh = SoundboardAPI(data_dir=data_dir, start_background_services=False)
    assert fresh._store.get_clip(clip["id"]) is None
    assert not Path(clip["file_path"]).exists()


# --- Review fixes: M1, M2, M5 -------------------------------------------------


def test_save_clip_edits_cannot_publish_a_draft(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    published_path = api._store.clips_dir / "publicado.wav"

    res = api.save_clip_edits({
        "id": clip["id"],
        "title": "Tentando publicar",
        "pending": False,
        "file_path": str(published_path),
        "duration": 99.0,
        "play_count": 7,
        "created_at": 0,
        "segments": [{"start": 0.0, "end": 1.0}],
    })

    assert res["ok"] is True
    saved = api._store.get_clip(clip["id"])
    assert saved["pending"] is True
    assert saved["title"] == "Tentando publicar"
    assert saved["file_path"] == clip["file_path"]
    assert Path(saved["file_path"]).is_relative_to(api._store.tmp_dir)
    assert saved["play_count"] == 0
    assert saved["created_at"] != 0
    assert saved["segments"] == []
    assert saved["duration"] != pytest.approx(99.0)
    assert not published_path.exists()
    assert not any(c["id"] == clip["id"] for c in api.get_initial_state()["clips"])


def test_save_clip_edits_on_draft_keeps_pending_and_updates_trim(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]

    res = api.save_clip_edits({
        "id": clip["id"],
        "trim_start": 0.2,
        "trim_end": 0.7,
        "volume": 1.4,
    })

    saved = api._store.get_clip(clip["id"])
    assert res["ok"] is True
    assert saved["pending"] is True
    assert saved["trim_start"] == pytest.approx(0.2)
    assert saved["trim_end"] == pytest.approx(0.7)
    assert saved["volume"] == pytest.approx(1.4)
    assert Path(saved["file_path"]).is_relative_to(api._store.tmp_dir)


def test_draft_rejects_destructive_analysis_actions(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    expected = {"ok": False, "error": "Rascunhos de replay não podem ser alterados assim."}

    assert api.split_clip_by_speakers(clip["id"]) == expected
    assert api.reanalyze_clip_ai(clip["id"]) == expected
    assert api.enroll_speaker_from_clip(clip["id"], "interlocutor_1") == expected
    # The draft is untouched and still a draft.
    assert api._store.get_clip(clip["id"])["pending"] is True
    assert Path(api._store.get_clip(clip["id"])["file_path"]).is_file()


def test_commit_failure_rolls_back_file_and_clip(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    tmp_path = Path(clip["file_path"])

    real_add = api._store.add_or_update_clip
    calls = {"n": 0}

    def flaky(data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disco cheio")
        return real_add(data)

    monkeypatch.setattr(api._store, "add_or_update_clip", flaky)

    res = api.commit_replay_draft(clip["id"])
    assert res["ok"] is False
    assert res["error"] == "Falha ao salvar o replay. Tente de novo."
    assert res["clips"] == []
    saved = api._store.get_clip(clip["id"])
    assert saved["pending"] is True
    assert Path(saved["file_path"]) == tmp_path
    assert tmp_path.is_file()
    assert not any(p.name.startswith("replay_") for p in api._store.clips_dir.iterdir())
    assert api.refresh_calls == []

    # The failure is recoverable: a second commit publishes normally.
    retry = api.commit_replay_draft(clip["id"])
    assert retry["ok"] is True
    assert api._store.get_clip(clip["id"])["pending"] is False
    assert Path(api._store.get_clip(clip["id"])["file_path"]).is_relative_to(
        api._store.clips_dir
    )
    assert not any(p.suffix == ".part" for p in api._store.clips_dir.iterdir())


def test_boot_sweeps_stray_temp_and_part_files(api):
    stray = _wav(api._store.tmp_dir / "stray.wav")
    part = api._store.clips_dir / "meio_importado.wav.part"
    part.write_bytes(b"resto")
    keep = _wav(api._store.clips_dir / "keep.wav")
    nested = api._store.tmp_dir / "subdir"
    nested.mkdir()

    data_dir = str(api._store.data_dir)
    api._store.flush()

    SoundboardAPI(data_dir=data_dir, start_background_services=False)
    assert not stray.exists()
    assert not part.exists()
    assert keep.exists()
    assert nested.is_dir()
