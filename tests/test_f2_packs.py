"""F2 packs: pure zip format/validation plus export/import through the API."""

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import packs
from main import SoundboardAPI, _as_float


# --- Task 3: packs.py unit tests ---------------------------------------------


def _zip(path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def _manifest(**over):
    base = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [{"file": "audio/a.wav", "title": "A"}],
        "speakers": [],
    }
    base.update(over)
    return base


def _wav_bytes(tmp_path, seconds=1.0, name="src.wav") -> bytes:
    wav = tmp_path / name
    sf.write(str(wav), np.zeros((int(48000 * seconds), 2), dtype=np.float32), 48000)
    return wav.read_bytes()


def test_validate_rejects_zip_slip(tmp_path):
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": b'{"format":"soundboard-pack","version":1,"clips":[{"file":"../evil.wav"}]}',
        "audio/a.wav": b"x",
    })
    with pytest.raises(packs.PackError):
        packs.validate_pack(path)


def test_validate_rejects_absolute_entry(tmp_path):
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": b'{"format":"soundboard-pack","version":1,"clips":[{"file":"audio/a.wav"}]}',
        "/etc/evil.wav": b"x",
    })
    with pytest.raises(packs.PackError):
        packs.validate_pack(path)


def test_validate_rejects_drive_letter_entry(tmp_path):
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": b'{"format":"soundboard-pack","version":1,"clips":[{"file":"audio/a.wav"}]}',
        "C:/Windows/evil.wav": b"x",
    })
    with pytest.raises(packs.PackError):
        packs.validate_pack(path)


def test_validate_rejects_entries_outside_audio(tmp_path):
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(_manifest()).encode(),
        "audio/a.wav": b"x",
        "docs/readme.txt": b"x",
    })
    with pytest.raises(packs.PackError):
        packs.validate_pack(path)


def test_validate_rejects_unsupported_audio_extension(tmp_path):
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(_manifest()).encode(),
        "audio/a.txt": b"x",
    })
    with pytest.raises(packs.PackError):
        packs.validate_pack(path)


def test_validate_rejects_bad_manifest_and_future_version(tmp_path):
    bad_format = _zip(tmp_path / "a.zip", {"manifest.json": b'{"format":"outro"}', "audio/a.wav": b"x"})
    with pytest.raises(packs.PackError):
        packs.validate_pack(bad_format)

    future = _zip(tmp_path / "b.zip", {
        "manifest.json": b'{"format":"soundboard-pack","version":9,"clips":[{"file":"audio/a.wav"}]}',
        "audio/a.wav": b"x",
    })
    with pytest.raises(packs.PackError):
        packs.validate_pack(future)

    non_int = _zip(tmp_path / "c.zip", {
        "manifest.json": b'{"format":"soundboard-pack","version":"1","clips":[{"file":"audio/a.wav"}]}',
        "audio/a.wav": b"x",
    })
    with pytest.raises(packs.PackError):
        packs.validate_pack(non_int)


def test_validate_rejects_missing_or_corrupt_manifest(tmp_path):
    missing = _zip(tmp_path / "a.zip", {"audio/a.wav": b"x"})
    with pytest.raises(packs.PackError):
        packs.validate_pack(missing)

    corrupt = _zip(tmp_path / "b.zip", {"manifest.json": b"{nao e json", "audio/a.wav": b"x"})
    with pytest.raises(packs.PackError):
        packs.validate_pack(corrupt)

    not_a_zip = tmp_path / "c.zip"
    not_a_zip.write_bytes(b"isto nao e um zip")
    with pytest.raises(packs.PackError):
        packs.validate_pack(not_a_zip)

    with pytest.raises(packs.PackError):
        packs.validate_pack(tmp_path / "nao_existe.zip")


def test_validate_rejects_empty_or_broken_clip_list(tmp_path):
    empty = _zip(tmp_path / "a.zip", {"manifest.json": json.dumps(_manifest(clips=[])).encode()})
    with pytest.raises(packs.PackError):
        packs.validate_pack(empty)

    broken = _zip(tmp_path / "b.zip", {
        "manifest.json": b'{"format":"soundboard-pack","version":1,"clips":["x"]}',
    })
    with pytest.raises(packs.PackError):
        packs.validate_pack(broken)


def test_validate_rejects_declared_audio_missing_from_zip(tmp_path):
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(_manifest()).encode(),
        "audio/b.wav": b"x",
    })
    with pytest.raises(packs.PackError):
        packs.validate_pack(path)


def test_validate_rejects_oversized_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(packs, "MAX_UNCOMPRESSED_BYTES", 8)
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": b'{"format":"soundboard-pack","version":1,"clips":[{"file":"audio/a.wav"}]}',
        "audio/a.wav": b"0123456789",
    })
    with pytest.raises(packs.PackError):
        packs.validate_pack(path)


def test_write_pack_skips_missing_audio(tmp_path):
    wav = tmp_path / "a.wav"
    sf.write(str(wav), np.zeros((480, 2), dtype=np.float32), 48000)
    clips = [
        {"id": "a", "file_path": str(wav), "title": "A", "file": "x"},
        {"id": "b", "file_path": str(tmp_path / "missing.wav"), "title": "B"},
    ]
    out = tmp_path / "out.zip"
    res = packs.write_pack(out, clips, {})
    assert res == {"count": 1, "skipped": 1}
    manifest = packs.validate_pack(out)["manifest"]
    assert [c["title"] for c in manifest["clips"]] == ["A"]
    assert manifest["clips"][0]["file"].startswith("audio/a")
    assert "id" not in manifest["clips"][0]


def test_write_pack_raises_when_nothing_to_write(tmp_path):
    out = tmp_path / "out.zip"
    with pytest.raises(packs.PackError):
        packs.write_pack(out, [{"id": "a", "file_path": str(tmp_path / "missing.wav")}], {})
    assert not out.exists()


# --- Task 4: export/import through SoundboardAPI ------------------------------


def _api(tmp_path, monkeypatch, sub="a"):
    api = SoundboardAPI(data_dir=str(tmp_path / sub), start_background_services=False)
    api._hotkeys.enabled = False
    monkeypatch.setattr(api._hotkeys, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(api._audio, "set_mic_passthrough", lambda *a, **k: None)
    return api


def _clip(api, title, seconds=1.0, **extra):
    wav = api._store.clips_dir / f"{title}.wav"
    # Distinct amplitude per title: identical bytes would trip the SHA-256 dedupe.
    level = (sum(ord(ch) for ch in title) % 20 + 1) / 100.0
    sf.write(str(wav), np.full((int(48000 * seconds), 2), level, dtype=np.float32), 48000)
    data = {"title": title, "file_path": str(wav), "duration": seconds, "category": "Call"}
    data.update(extra)
    return api._store.add_or_update_clip(data)


def test_export_import_round_trip_preserves_content(tmp_path, monkeypatch):
    src = _api(tmp_path, monkeypatch, "src")
    src._store.ensure_speaker("interlocutor_1", default_name="Lucas")
    a = _clip(src, "A", trim_start=0.1, trim_end=0.6, volume=1.3, hotkey="ctrl+1", speaker_id="interlocutor_1")
    b = _clip(src, "B", tags=["meme"], favorite=True)
    out = tmp_path / "pack.zip"
    res = src.export_pack([a["id"], b["id"]], str(out))
    assert res["ok"] is True and res["count"] == 2

    dst = _api(tmp_path, monkeypatch, "dst")
    imported = dst.import_pack(str(out))
    assert imported["ok"] is True
    assert len(imported["imported"]) == 2
    titles = {c["title"] for c in imported["clips"]}
    assert titles == {"A", "B"}
    new_a = next(c for c in imported["clips"] if c["title"] == "A")
    assert new_a["id"] != a["id"]
    assert new_a["trim_start"] == pytest.approx(0.1)
    assert new_a["trim_end"] == pytest.approx(0.6)
    assert new_a["volume"] == pytest.approx(1.3)
    assert new_a["hotkey"] == "ctrl+1"
    names = {dst._store.state["speakers"][sid]["name"] for sid in new_a["speaker_ids"]}
    assert names == {"Lucas"}
    assert packs.sha256_file(new_a["file_path"]) == packs.sha256_file(a["file_path"])
    assert Path(new_a["file_path"]).is_relative_to(dst._store.clips_dir)


def test_import_twice_skips_duplicates(tmp_path, monkeypatch):
    src = _api(tmp_path, monkeypatch, "src")
    _clip(src, "A")
    out = tmp_path / "p.zip"
    src.export_pack(None, str(out))

    dst = _api(tmp_path, monkeypatch, "dst")
    first = dst.import_pack(str(out))
    second = dst.import_pack(str(out))
    assert len(first["imported"]) == 1
    assert second["ok"] is True
    assert second["imported"] == []
    assert second["skipped"] == 1
    assert not list(dst._store.clips_dir.glob("*.part"))


def test_import_conflicting_hotkey_drops_it(tmp_path, monkeypatch):
    src = _api(tmp_path, monkeypatch, "src")
    _clip(src, "A", hotkey="ctrl+1")
    out = tmp_path / "p.zip"
    src.export_pack(None, str(out))

    dst = _api(tmp_path, monkeypatch, "dst")
    _clip(dst, "Local", hotkey="ctrl+1")
    res = dst.import_pack(str(out))
    got = next(c for c in res["clips"] if c["title"] == "A")
    assert got["hotkey"] == ""
    assert any("atalho" in w.lower() for w in res["warnings"])


def test_import_rejects_zip_slip_without_writing(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", '{"format":"soundboard-pack","version":1,"clips":[{"file":"../evil.wav"}]}')
        archive.writestr("audio/a.wav", b"x")
    res = dst.import_pack(str(path))
    assert res["ok"] is False
    assert list(dst._store.clips_dir.iterdir()) == []


def test_import_rejects_invalid_zip_without_writing(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    path = tmp_path / "lixo.zip"
    path.write_bytes(b"isto nao e um zip")
    res = dst.import_pack(str(path))
    assert res["ok"] is False
    assert "clips" in res and "speakers" in res
    assert list(dst._store.clips_dir.iterdir()) == []


def test_export_and_import_cancel_without_dialog(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, "a")
    assert api._window is None
    _clip(api, "A")

    exported = api.export_pack(None, None)
    assert exported == {"ok": False, "error": "Exportação cancelada.", "count": 0, "skipped": 0}

    imported = api.import_pack(None)
    assert imported["ok"] is False
    assert imported["error"] == "Importação cancelada."
    assert [c["title"] for c in imported["clips"]] == ["A"]


def test_export_ignores_pending_drafts(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, "src")
    _clip(api, "A")
    draft_file = api._store.tmp_dir / "draft.wav"
    sf.write(str(draft_file), np.zeros((4800, 2), dtype=np.float32), 48000)
    api._store.add_or_update_clip({"title": "Draft", "file_path": str(draft_file), "pending": True})

    out = tmp_path / "p.zip"
    res = api.export_pack(None, str(out))
    assert res["ok"] is True and res["count"] == 1
    manifest = packs.validate_pack(out)["manifest"]
    assert [c["title"] for c in manifest["clips"]] == ["A"]


def test_export_without_audio_returns_error(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, "a")
    assert api.export_pack([], str(tmp_path / "p.zip"))["error"] == "Nenhum áudio para exportar."

    api._store.add_or_update_clip({"title": "Sumido", "file_path": str(tmp_path / "sumido.wav")})
    out = tmp_path / "p.zip"
    res = api.export_pack(None, str(out))
    assert res["ok"] is False
    assert "nenhum áudio" in res["error"].lower()
    assert not out.exists()
    assert not (tmp_path / "p.zip.tmp").exists()


def test_import_clamps_values_and_keeps_category(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    manifest = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [{
            "file": "audio/x.wav",
            "title": "Estourado",
            "category": "Categoria Nova",
            "volume": 99,
            "trim_start": -5,
            "trim_end": 999,
            "duration": 30,
            "speaker_names": ["Lucas"],
        }],
        "speakers": [{"name": "Lucas", "color": "#123456"}],
    }
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("audio/x.wav", _wav_bytes(tmp_path))

    res = dst.import_pack(str(path))
    assert res["ok"] is True
    clip = res["clips"][0]
    assert clip["volume"] == pytest.approx(3.0)
    assert clip["trim_start"] == pytest.approx(0.0)
    assert clip["trim_end"] == pytest.approx(1.0, abs=0.05)
    assert clip["category"] == "Categoria Nova"
    speaker = dst._store.state["speakers"][clip["speaker_ids"][0]]
    assert speaker["name"] == "Lucas"
    assert speaker["color"] == "#123456"


def test_import_reuses_existing_speaker_by_name(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    existing = dst._store.ensure_speaker("interlocutor_1", default_name="lucas")
    manifest = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [{"file": "audio/x.wav", "title": "A", "speaker_names": ["Lucas"]}],
        "speakers": [],
    }
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("audio/x.wav", _wav_bytes(tmp_path))

    res = dst.import_pack(str(path))
    assert res["imported"][0]["speaker_ids"] == [existing["id"]]
    assert len(dst._store.state["speakers"]) == 1


def test_import_dedupes_entries_inside_same_pack(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    data = _wav_bytes(tmp_path)
    manifest = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [
            {"file": "audio/a.wav", "title": "A"},
            {"file": "audio/b.wav", "title": "B"},
        ],
        "speakers": [],
    }
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("audio/a.wav", data)
        archive.writestr("audio/b.wav", data)

    res = dst.import_pack(str(path))
    assert res["ok"] is True
    assert len(res["imported"]) == 1
    assert res["skipped"] == 1
    assert any("já existe" in w for w in res["warnings"])
    assert not list(dst._store.clips_dir.glob("*.part"))


def test_import_survives_broken_field_types(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    manifest = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [{
            "file": "audio/x.wav",
            "title": "Zoado",
            "volume": "abc",
            "trim_start": "x",
            "trim_end": None,
            "tags": "meme",
            "speaker_names": "Lucas",
        }],
        "speakers": [],
    }
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("audio/x.wav", _wav_bytes(tmp_path))

    res = dst.import_pack(str(path))
    assert res["ok"] is True
    clip = res["imported"][0]
    assert clip["volume"] == pytest.approx(1.0)
    assert clip["trim_start"] == pytest.approx(0.0)
    assert clip["trim_end"] == pytest.approx(1.0, abs=0.05)
    assert clip["tags"] == []
    assert clip["speaker_ids"] == []


def test_import_creates_speaker_when_name_is_new(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    dst._store.ensure_speaker("interlocutor_1", default_name="Ana")
    manifest = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [{"file": "audio/x.wav", "title": "A", "speaker_names": ["Lucas"]}],
        "speakers": [],
    }
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("audio/x.wav", _wav_bytes(tmp_path))

    res = dst.import_pack(str(path))
    sid = res["imported"][0]["speaker_ids"][0]
    assert sid != "interlocutor_1"
    assert dst._store.state["speakers"][sid]["name"] == "Lucas"
    assert dst._store.state["speakers"]["interlocutor_1"]["name"] == "Ana"


# --- Review fixes: H2, H3, M4, M7, L2/L3, L4, L9/L12 --------------------------


def test_validate_wraps_bad_zip_with_user_facing_message(tmp_path):
    garbage = tmp_path / "lixo.zip"
    garbage.write_bytes(b"isto nao e um zip")
    with pytest.raises(packs.PackError, match="não é um pacote .zip válido"):
        packs.validate_pack(garbage)


def test_validate_wraps_os_and_unexpected_errors(tmp_path, monkeypatch):
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(_manifest()).encode(),
        "audio/a.wav": b"x",
    })

    def _raise(exc):
        def boom(*args, **kwargs):
            raise exc
        return boom

    # PermissionError/OSError must not escape as OSError.
    monkeypatch.setattr(packs.zipfile, "ZipFile", _raise(PermissionError("negado")))
    with pytest.raises(packs.PackError, match="não pôde ser aberto"):
        packs.validate_pack(path)

    # Encrypted/unsupported archives raise RuntimeError/NotImplementedError.
    monkeypatch.setattr(packs.zipfile, "ZipFile", _raise(RuntimeError("senha necessária")))
    with pytest.raises(packs.PackError, match="não pôde ser aberto"):
        packs.validate_pack(path)


def test_validate_rejects_backslash_entry(tmp_path):
    # CPython's ZipInfo normalizes os.sep when writing, but a manifest (or a
    # third-party writer) can still carry a raw backslash. Both must be refused.
    with pytest.raises(packs.PackError, match="suspeito"):
        packs._safe_entry_name("audio\\a.wav")

    manifest = _manifest(clips=[{"file": "audio\\a.wav", "title": "A"}])
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(manifest).encode(),
        "audio/a.wav": b"x",
    })
    with pytest.raises(packs.PackError, match="suspeito"):
        packs.validate_pack(path)


def test_validate_rejects_oversized_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(packs, "MAX_MANIFEST_BYTES", 64)
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(_manifest()).encode(),
        "audio/a.wav": b"x",
    })
    with pytest.raises(packs.PackError, match="manifest.json grande demais"):
        packs.validate_pack(path)


def test_validate_rejects_more_clips_than_audio_entries(tmp_path):
    manifest = _manifest(clips=[
        {"file": "audio/a.wav", "title": "A"},
        {"file": "audio/a.wav", "title": "B"},
    ])
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(manifest).encode(),
        "audio/a.wav": b"x",
    })
    with pytest.raises(packs.PackError, match="corrompido"):
        packs.validate_pack(path)


def test_build_manifest_sanitizes_names_and_never_exports_ids():
    clips = [
        {"id": "a/b c?", "file_path": "x.wav", "title": "Um"},
        {"id": "", "file_path": "y.wav", "title": "Dois"},
        {"id": "a/b c?", "file_path": "z.wav", "title": "Tres"},
        {"id": "nao_suportado", "file_path": "w.aac", "title": "Quatro"},
    ]
    manifest = packs.build_manifest(clips, {})
    assert all("id" not in clip for clip in manifest["clips"])
    names = [clip["file"] for clip in manifest["clips"]]
    assert names == ["audio/abc.wav", "audio/clip0002.wav", "audio/abc_2.wav"]
    assert all(name.startswith("audio/") for name in names)


def test_copy_entry_respects_remaining_budget(tmp_path):
    archive_path = _zip(tmp_path / "p.zip", {"audio/a.wav": b"0" * 100})
    with zipfile.ZipFile(archive_path) as archive:
        sha, size = packs.copy_entry(archive, "audio/a.wav", tmp_path / "ok.wav", remaining=100)
        assert size == 100 and len(sha) == 64

        with pytest.raises(packs.PackError, match="2 GB"):
            packs.copy_entry(archive, "audio/a.wav", tmp_path / "too_big.wav", remaining=50)


def test_copy_entry_wraps_unexpected_archive_errors(tmp_path):
    class EncryptedArchive:
        def open(self, name):
            raise RuntimeError(f"{name} is encrypted, password required for extraction")

    with pytest.raises(packs.PackError, match="corrompido ou não suportado"):
        packs.copy_entry(EncryptedArchive(), "audio/a.wav", tmp_path / "x.wav")


def test_import_encrypted_entry_does_not_escape(tmp_path, monkeypatch):
    """A non-PackError from the extractor must become {ok: False}, never raise."""
    dst = _api(tmp_path, monkeypatch, "dst")
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(_manifest()).encode(),
        "audio/a.wav": _wav_bytes(tmp_path),
    })

    def boom(*args, **kwargs):
        raise RuntimeError("entry is encrypted")

    monkeypatch.setattr(packs, "copy_entry", boom)
    res = dst.import_pack(str(path))
    assert res["ok"] is False
    assert res["error"].startswith("Falha ao importar:")
    assert res["imported"] == []
    assert list(dst._store.clips_dir.iterdir()) == []


def test_import_failure_after_extraction_leaves_no_orphan(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(_manifest()).encode(),
        "audio/a.wav": _wav_bytes(tmp_path),
    })

    def boom(data):
        raise RuntimeError("biblioteca indisponível")

    monkeypatch.setattr(dst._store, "add_or_update_clip", boom)
    res = dst.import_pack(str(path))
    assert res["ok"] is False
    assert res["error"].startswith("Falha ao importar:")
    assert list(dst._store.clips_dir.iterdir()) == []


def test_import_skips_unsupported_audio_entry(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(_manifest()).encode(),
        "audio/a.wav": _wav_bytes(tmp_path),
    })

    def unsupported(*args, **kwargs):
        raise packs.PackError("Pacote corrompido ou não suportado.")

    monkeypatch.setattr(packs, "copy_entry", unsupported)
    res = dst.import_pack(str(path))
    assert res["ok"] is True
    assert res["imported"] == []
    assert res["skipped"] == 1
    assert any("não suportado" in w for w in res["warnings"])
    assert list(dst._store.clips_dir.iterdir()) == []


def test_import_extracts_repeated_manifest_file_once(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    data = _wav_bytes(tmp_path)
    manifest = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [
            {"file": "audio/a.wav", "title": title, "duration": 1.0}
            for title in ("A", "B", "C")
        ],
        "speakers": [],
    }
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(manifest).encode(),
        "audio/a.wav": data,
    })
    # Real validate_pack rejects clips > audio entries, which is exactly what we
    # want for hand-made packs. Stub it to exercise the importer's own dedupe.
    monkeypatch.setattr(
        packs, "validate_pack",
        lambda p: {"manifest": manifest, "entries": {"audio/a.wav": len(data)}},
    )

    res = dst.import_pack(str(path))
    assert res["ok"] is True
    assert len(res["imported"]) == 1
    assert res["skipped"] == 2
    assert sum("repetido" in w for w in res["warnings"]) == 2
    assert len(list(dst._store.clips_dir.glob("*.wav"))) == 1
    assert not list(dst._store.clips_dir.glob("*.part"))


def test_import_enforces_cumulative_byte_budget(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    manifest = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [
            {"file": f"audio/{ch}.wav", "title": ch.upper(), "duration": 1.0}
            for ch in "abc"
        ],
        "speakers": [],
    }
    path = tmp_path / "p.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for ch in "abc":
            archive.writestr(f"audio/{ch}.wav", ch.encode() * 400)
    monkeypatch.setattr(
        packs, "validate_pack",
        lambda p: {"manifest": manifest, "entries": {f"audio/{ch}.wav": 400 for ch in "abc"}},
    )
    # 600 bytes total: the first 400-byte entry fits, the other two do not.
    monkeypatch.setattr(packs, "MAX_UNCOMPRESSED_BYTES", 600)

    res = dst.import_pack(str(path))
    assert res["ok"] is True
    assert len(res["imported"]) == 1
    assert res["skipped"] == 2
    assert sum("grande demais" in w for w in res["warnings"]) == 2
    assert not list(dst._store.clips_dir.glob("*.part"))


def test_import_sanitizes_non_finite_numbers(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    manifest = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [{
            "file": "audio/x.wav",
            "title": "Nao finito",
            "duration": 1.0,
            "volume": float("nan"),
            "trim_start": float("inf"),
            "trim_end": float("-inf"),
        }],
        "speakers": [],
    }
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(manifest).encode(),
        "audio/x.wav": _wav_bytes(tmp_path),
    })

    res = dst.import_pack(str(path))
    assert res["ok"] is True
    clip = res["imported"][0]
    assert 0.0 <= clip["volume"] <= 3.0
    audible = clip["trim_end"] - clip["trim_start"]
    assert 0.05 <= audible <= 1.05


def test_as_float_rejects_non_finite_values():
    assert _as_float(float("nan"), 1.0, 0.0, 3.0) == 1.0
    assert _as_float(float("inf"), 1.0, 0.0, 3.0) == 1.0
    assert _as_float(float("-inf"), 1.0, 0.0, 3.0) == 1.0
    assert _as_float("x", 2.0, 0.0, 3.0) == 2.0
    assert _as_float(None, 2.0, 0.0, 3.0) == 2.0
    assert _as_float(9, 1.0, 0.0, 3.0) == 3.0


def test_export_appends_zip_extension(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, "a")
    _clip(api, "A")

    res = api.export_pack(None, str(tmp_path / "pacote"))
    assert res["ok"] is True
    assert res["path"] == str(tmp_path / "pacote.zip")
    assert (tmp_path / "pacote.zip").is_file()
    assert not (tmp_path / "pacote.zip.tmp").exists()


def test_malformed_paths_never_raise(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, "a")
    _clip(api, "A")

    exported = api.export_pack(None, 123)
    assert exported["ok"] is False
    assert "exportar" in exported["error"].lower()

    imported = api.import_pack(123)
    assert imported["ok"] is False
    assert "aberto" in imported["error"].lower() or "importar" in imported["error"].lower()
    assert "clips" in imported and "speakers" in imported


def test_export_skips_unsupported_audio_and_round_trips(tmp_path, monkeypatch):
    src = _api(tmp_path, monkeypatch, "src")
    _clip(src, "A")
    aac = src._store.clips_dir / "B.aac"
    aac.write_bytes(b"nao e aac de verdade")
    src._store.add_or_update_clip({"title": "B", "file_path": str(aac), "duration": 1.0})

    out = tmp_path / "p.zip"
    res = src.export_pack(None, str(out))
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["skipped"] == 1
    manifest = packs.validate_pack(out)["manifest"]
    assert [c["title"] for c in manifest["clips"]] == ["A"]

    dst = _api(tmp_path, monkeypatch, "dst")
    imported = dst.import_pack(str(out))
    assert imported["ok"] is True
    assert [c["title"] for c in imported["clips"]] == ["A"]


def test_import_same_pack_conflicting_hotkey_is_case_insensitive(tmp_path, monkeypatch):
    dst = _api(tmp_path, monkeypatch, "dst")
    data_a = _wav_bytes(tmp_path)
    second = tmp_path / "src_b.wav"
    sf.write(str(second), np.full((48000, 2), 0.25, dtype=np.float32), 48000)

    manifest = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [
            {"file": "audio/a.wav", "title": "A", "hotkey": "Ctrl+1", "duration": 1.0},
            {"file": "audio/b.wav", "title": "B", "hotkey": "ctrl+1", "duration": 1.0},
        ],
        "speakers": [],
    }
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(manifest).encode(),
        "audio/a.wav": data_a,
        "audio/b.wav": second.read_bytes(),
    })

    res = dst.import_pack(str(path))
    assert res["ok"] is True
    by_title = {c["title"]: c for c in res["clips"]}
    assert by_title["A"]["hotkey"] == "Ctrl+1"
    assert by_title["B"]["hotkey"] == ""
    assert any("atalho" in w.lower() for w in res["warnings"])


def test_import_avoids_filename_collision(tmp_path, monkeypatch):
    import uuid as uuid_module

    dst = _api(tmp_path, monkeypatch, "dst")
    taken = dst._store.clips_dir / "aaaaaaaaaa.wav"
    taken.write_bytes(b"ocupado")

    class FixedUUID:
        def __init__(self, value):
            self.hex = value

    values = iter(["aaaaaaaaaa", "bbbbbbbbbb"])
    monkeypatch.setattr(uuid_module, "uuid4", lambda: FixedUUID(next(values)))

    manifest = {
        "format": "soundboard-pack",
        "version": 1,
        "clips": [{"file": "audio/a.wav", "title": "A", "duration": 1.0}],
        "speakers": [],
    }
    path = _zip(tmp_path / "p.zip", {
        "manifest.json": json.dumps(manifest).encode(),
        "audio/a.wav": _wav_bytes(tmp_path),
    })

    res = dst.import_pack(str(path))
    assert res["ok"] is True
    assert Path(res["imported"][0]["file_path"]).name == "bbbbbbbbbb.wav"
    assert taken.read_bytes() == b"ocupado"
