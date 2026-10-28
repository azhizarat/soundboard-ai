"""Pacote C: Schema versioning, backup and coalesced write-behind tests."""

from __future__ import annotations

import json
from pathlib import Path
import time
import pytest

from store import SoundboardStore, CURRENT_SCHEMA_VERSION


def test_fresh_store_initializes_with_schema_version_1(tmp_path):
    store = SoundboardStore(data_dir=str(tmp_path))
    try:
        assert store.state["schema_version"] == CURRENT_SCHEMA_VERSION
        store.save()
        saved = json.loads((tmp_path / "soundboard.json").read_text(encoding="utf-8"))
        assert saved["schema_version"] == 1
    finally:
        store.close()


def test_migration_v0_to_v1_creates_backup_and_persists_version(tmp_path):
    db_file = tmp_path / "soundboard.json"
    legacy_data = {
        "clips": [{"id": "c1", "title": "Clip Antigo"}],
        "categories": [{"id": "Call", "name": "Call"}],
    }
    raw_legacy = json.dumps(legacy_data, indent=2)
    db_file.write_text(raw_legacy, encoding="utf-8")

    store = SoundboardStore(data_dir=str(tmp_path))
    try:
        assert store.state["schema_version"] == 1
        assert len(store.state["clips"]) == 1
        assert store.state["clips"][0]["title"] == "Clip Antigo"

        # Verify .bak was created
        bak_file = tmp_path / "soundboard.json.bak"
        assert bak_file.is_file()
        assert bak_file.read_text(encoding="utf-8") == raw_legacy

        # Verify db_file was updated with schema_version = 1
        updated = json.loads(db_file.read_text(encoding="utf-8"))
        assert updated["schema_version"] == 1
    finally:
        store.close()


def test_write_behind_coalescing(tmp_path, monkeypatch):
    store = SoundboardStore(data_dir=str(tmp_path))
    try:
        save_calls = []
        original_perform_save = store._perform_save

        def tracked_save():
            save_calls.append(time.time())
            return original_perform_save()

        monkeypatch.setattr(store, "_perform_save", tracked_save)

        # Mark dirty 20 times quickly
        for i in range(20):
            store.mark_dirty(delay=0.15)
            time.sleep(0.005)

        # Wait for deadline to elapse
        time.sleep(0.3)

        # All 20 calls coalesced into 1 disk write
        assert len(save_calls) == 1
        assert store._dirty is False
    finally:
        store.close()


def test_flush_persists_immediately(tmp_path):
    store = SoundboardStore(data_dir=str(tmp_path))
    try:
        store.state["settings"]["cable_volume"] = 0.42
        store.mark_dirty(delay=5.0)  # Large delay

        assert store._dirty is True

        store.flush()

        assert store._dirty is False
        saved = json.loads((tmp_path / "soundboard.json").read_text(encoding="utf-8"))
        assert saved["settings"]["cable_volume"] == 0.42
    finally:
        store.close()


def test_failed_save_keeps_dirty_and_cleans_tmp(tmp_path, monkeypatch):
    store = SoundboardStore(data_dir=str(tmp_path))
    try:
        store.state["settings"]["cable_volume"] = 0.99

        def failing_replace(src, dst):
            raise OSError("Disco cheio ou bloqueado")

        monkeypatch.setattr("os.replace", failing_replace)

        with pytest.raises(OSError):
            store.save()

        assert store._dirty is True
        tmp_file = tmp_path / "soundboard.json.tmp"
        assert not tmp_file.exists()
    finally:
        store.close()


def test_increment_play_count_uses_write_behind(tmp_path, monkeypatch):
    store = SoundboardStore(data_dir=str(tmp_path))
    try:
        store.add_or_update_clip({"id": "c10", "title": "Teste Play"})
        store.save()

        sync_saves = []
        original_save = store.save

        def tracked_sync_save():
            sync_saves.append(1)
            return original_save()

        monkeypatch.setattr(store, "save", tracked_sync_save)

        c = store.increment_play_count("c10")
        assert c["play_count"] == 1
        # Synchronous save was NOT called
        assert len(sync_saves) == 0
        assert store._dirty is True

        store.flush()
        saved = json.loads((tmp_path / "soundboard.json").read_text(encoding="utf-8"))
        assert saved["clips"][0]["play_count"] == 1
    finally:
        store.close()
