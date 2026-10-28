# F2 — Preview do replay e packs (.zip)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O replay deixa de ser salvo direto na biblioteca: vira rascunho revisável (trim, título, preview no fone) com Salvar/Descartar. A biblioteca passa a ser exportável/importável como pacote `.zip` portátil.

**Architecture:** O rascunho é um clipe normal no store com `pending: True` e arquivo em `data/tmp/`; por isso play, trim e waveform já funcionam sem código novo. Salvar move o WAV para `clips_dir`; descartar (ou reiniciar o app) apaga clipe e arquivo. Packs são um módulo puro (`packs.py`) que valida e lê/escreve o zip, com o `SoundboardAPI` cuidando de diálogos, dedupe e store.

**Tech Stack:** Python 3.11+ (pytest, zipfile, hashlib, soundfile), JavaScript vanilla (pywebview).

**Spec:** `docs/superpowers/specs/2026-10-28-soundboard-roadmap-design.md` §7.

**Baseline:** `336591a` (F1 merged).

---

## Contratos (fonte da verdade entre backend e UI)

**Rascunho**
- `capture_replay_draft(last_seconds=None) -> {ok, clip, clips, speakers, error?}`
  — `clip.pending == True`; `clips` **não** contém pendentes.
- `commit_replay_draft(clip_id) -> {ok, clip, clips, speakers, split_count?, error?}`
- `discard_replay_draft(clip_id) -> {ok, clips, speakers, error?}`

**Packs**
- `export_pack(clip_ids=None, path=None) -> {ok, path?, count, skipped, error?}`
- `import_pack(path=None) -> {ok, imported, skipped, warnings, clips, speakers, error?}`

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `packs.py` (novo) | validação/escrita/leitura do zip, manifesto, hash — sem store, sem UI |
| `store.py` | `tmp_dir`, `purge_pending_clips()` |
| `main.py` | drafts (capture/commit/discard), purge no boot, filtro de pendentes, `export_pack`/`import_pack`, diálogos |
| `tests/test_f2_replay.py` (novo) | drafts, purge, filtro |
| `tests/test_f2_packs.py` (novo) | round-trip, zip-slip, dedupe, hotkey, interlocutor |
| `ui/index.html` | chrome de draft no modal, menu 📦 Pack, modal de avisos |
| `ui/app.js` | fluxo de draft, menu de packs, avisos |
| `ui/styles.css` | `.draft-footer`, `.pack-popover`, `.pack-warnings` |

---

### Task 1: Store — `tmp_dir` e purga de pendentes

**Files:** `store.py`, `tests/test_f2_replay.py`

- [ ] **Step 1: teste que falha**

```python
# tests/test_f2_replay.py
import numpy as np
import soundfile as sf

from store import SoundboardStore


def _wav(path, seconds=0.1):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.zeros((int(48000 * seconds), 2), dtype=np.float32), 48000)
    return path


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
```

- [ ] **Step 2: rodar e ver falhar** — `uv run pytest -q tests/test_f2_replay.py` → `AttributeError: tmp_dir`.

- [ ] **Step 3: implementar**

Em `__init__`, junto de `clips_dir`/`models_dir`:

```python
        self.tmp_dir = self.data_dir / "tmp"
```

e no bloco de `mkdir`: `self.tmp_dir.mkdir(parents=True, exist_ok=True)`.

Métodos novos:

```python
    def _unlink_inside(self, root: Path, raw_path: str) -> bool:
        """Deletes a file only when it resolves under ``root``."""
        raw = str(raw_path or "").strip()
        if not raw:
            return False
        try:
            resolved = Path(raw).resolve()
            base = root.resolve()
        except Exception:
            return False
        if not resolved.is_relative_to(base) or resolved == base or not resolved.is_file():
            return False
        try:
            resolved.unlink()
            return True
        except OSError:
            return False

    def purge_pending_clips(self) -> int:
        """Drops replay drafts: they never survive a restart."""
        with self._lock:
            pending = [c for c in self.state["clips"] if c.get("pending")]
            if not pending:
                return 0
            self.state["clips"] = [c for c in self.state["clips"] if not c.get("pending")]
            for clip in pending:
                self._unlink_inside(self.tmp_dir, clip.get("file_path"))
            self.save()
            return len(pending)
```

- [ ] **Step 4: rodar** — 1 passed.

---

### Task 2: Backend — drafts de replay

**Files:** `main.py`, `tests/test_f2_replay.py`

- [ ] **Step 1: testes que falham**

```python
import pytest

from main import SoundboardAPI


@pytest.fixture
def api(tmp_path, monkeypatch):
    api = SoundboardAPI(data_dir=str(tmp_path / "data"), start_background_services=False)
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


def test_discard_deletes_clip_and_file(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    res = api.discard_replay_draft(clip["id"])
    assert res["ok"] is True
    assert api._store.get_clip(clip["id"]) is None
    assert not Path(clip["file_path"]).exists()
    assert all(c["id"] != clip["id"] for c in res["clips"])


def test_commit_requires_pending(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    api.commit_replay_draft(clip["id"])
    res = api.commit_replay_draft(clip["id"])
    assert res["ok"] is False
    assert "rascunho" in res["error"].lower()


def test_boot_purges_old_drafts(api, monkeypatch):
    _fake_capture(api, monkeypatch)
    clip = api.capture_replay_draft(5)["clip"]
    data_dir = str(api._store.data_dir)
    api._store.flush()

    fresh = SoundboardAPI(data_dir=data_dir, start_background_services=False)
    assert fresh._store.get_clip(clip["id"]) is None
    assert not Path(clip["file_path"]).exists()
```

Importe `Path` no topo do arquivo de teste.

- [ ] **Step 2: rodar e ver falhar** — métodos não existem.

- [ ] **Step 3: implementar em `main.py`**

No `__init__`, logo depois de criar o store: `self._store.purge_pending_clips()`.

Helpers de visibilidade (perto de `_get_speaker_names_map`):

```python
    @staticmethod
    def _public_clips(clips: list[dict]) -> list[dict]:
        """Drafts are internal: the UI and hotkeys never see them."""
        return [c for c in clips if not c.get("pending")]
```

Trocar **todos** os `self._hotkeys.refresh(snap["clips"], ...)` / equivalentes por `self._public_clips(snap["clips"])`, e em `get_initial_state` usar `"clips": self._public_clips(snap["clips"])`.

Extrair o miolo de `capture_replay_clip` para um helper que recebe a pasta de destino e devolve o clipe montado (sem salvar no store):

```python
    def _capture_replay_analysis(self, out_dir: Path, last_seconds: float = None) -> dict:
        """Captures the replay buffer, runs AI and returns a clip payload (not persisted)."""
        secs = float(last_seconds or self._store.state["settings"].get("replay_seconds", 15))
        out_name = f"replay_{time.strftime('%Y%m%d_%H%M%S')}.wav"
        out_path = str(out_dir / out_name)

        self._emit_progress(True, 1, 3, f"Capturando últimos {int(secs)}s da call...", out_name)
        saved = self._audio.save_replay_wav(out_path, last_seconds=secs)
        if not saved:
            return {"ok": False, "error": "Nenhum áudio recente tocado no seu fone/sistema nos últimos 15s"}

        self._emit_progress(True, 2, 3, "Identificando vozes e transcrevendo com IA...", out_name)
        res = self._ai.analyze_file(
            saved,
            speaker_names=self._get_speaker_names_map(),
            use_llm=bool(self._store.state["settings"].get("use_local_llm", True)),
        )
        spk_id = res.get("speaker_id", "")
        spk_ids = res.get("speaker_ids") or ([spk_id] if spk_id else [])
        for sid in spk_ids:
            self._store.ensure_speaker(sid)

        return {
            "ok": True,
            "clip": {
                "title": res.get("title") or f"Replay {time.strftime('%H:%M:%S')}",
                "file_path": saved,
                "category": res.get("category") or "Call",
                "speaker_id": spk_id,
                "speaker_ids": spk_ids,
                "segments": res.get("segments") or [],
                "transcript": res.get("transcript") or "",
                "acoustic_mood": res.get("acoustic_mood") or "Normal",
                "emoji": res.get("emoji") or "⚡",
                "color": res.get("color") or "#f59e0b",
                "tags": res.get("tags") or ["replay"],
                "duration": res.get("duration") or secs,
                "created_at": time.time(),
            },
        }
```

`capture_replay_clip` passa a ser wrapper (mantém o comportamento atual do hotkey Alt+F10: salva direto, com auto-split e `replay_captured`):

```python
    def capture_replay_clip(self, last_seconds: float = None) -> dict:
        try:
            captured = self._capture_replay_analysis(self._store.clips_dir, last_seconds)
            if not captured.get("ok"):
                self._notify_ui("toast", {"type": "warning", "message": captured["error"]})
                return captured
            clip = self._store.add_or_update_clip(captured["clip"])
            snap = self._store.snapshot()
            self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
            self._notify_ui("replay_captured", {
                "clip": copy.deepcopy(clip),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            })
            return {
                "ok": True,
                "clip": copy.deepcopy(clip),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
        finally:
            self._emit_progress(False)
```

Novos métodos:

```python
    def capture_replay_draft(self, last_seconds: float = None) -> dict:
        """Captures a replay into data/tmp and keeps it out of the library until saved."""
        try:
            captured = self._capture_replay_analysis(self._store.tmp_dir, last_seconds)
            if not captured.get("ok"):
                return captured
            payload = dict(captured["clip"])
            payload["pending"] = True
            clip = self._store.add_or_update_clip(payload)
            snap = self._store.snapshot()
            return {
                "ok": True,
                "clip": copy.deepcopy(clip),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
        finally:
            self._emit_progress(False)

    def commit_replay_draft(self, clip_id: str) -> dict:
        clip = self._store.get_clip(clip_id)
        snap = self._store.snapshot()
        base = {
            "ok": False,
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }
        if not clip or not clip.get("pending"):
            return {**base, "error": "Este rascunho não existe mais."}

        src = Path(str(clip.get("file_path") or ""))
        try:
            src_ok = src.is_file() and src.resolve().is_relative_to(self._store.tmp_dir.resolve())
        except Exception:
            src_ok = False
        if not src_ok:
            return {**base, "error": "Arquivo do rascunho não encontrado."}

        dst = self._store.clips_dir / f"replay_{clip_id}{src.suffix or '.wav'}"
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:
            return {**base, "error": f"Falha ao salvar o replay: {exc}"}

        saved = self._store.add_or_update_clip({
            "id": clip_id,
            "file_path": str(dst),
            "pending": False,
            "created_at": time.time(),
        })
        split_count = 0
        settings = self._store.state["settings"]
        if settings.get("auto_split_speakers") and len(saved.get("segments") or []) > 1:
            split_res = self.split_clip_by_speakers(saved["id"])
            if split_res.get("ok"):
                split_count = int(split_res.get("created_count") or 0)
        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        return {
            "ok": True,
            "clip": copy.deepcopy(self._store.get_clip(clip_id) or saved),
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
            "split_count": split_count,
        }

    def discard_replay_draft(self, clip_id: str) -> dict:
        clip = self._store.get_clip(clip_id)
        ok = bool(clip and clip.get("pending"))
        if ok:
            self._store.delete_clip(clip_id, delete_file=False)
            self._store._unlink_inside(self._store.tmp_dir, clip.get("file_path"))
            self._audio.invalidate_cache(clip.get("file_path") or "")
        snap = self._store.snapshot()
        return {
            "ok": ok,
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }
```

Remova a linha inválida `self._store.invalidate?` — ela é só um lembrete: **não existe** invalidação de cache no store.

- [ ] **Step 4: rodar** — `uv run pytest -q tests/test_f2_replay.py` → 6 passed; `uv run pytest -q` → tudo verde (os testes F1 que contam chamadas de `refresh` continuam válidos).

---

### Task 3: `packs.py` — formato e validação

**Files:** `packs.py` (novo), `tests/test_f2_packs.py`

- [ ] **Step 1: implementar o módulo**

```python
"""Soundboard pack (.zip with manifest.json + audio/). Pure I/O: no store, no UI."""

from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile
from pathlib import Path, PurePosixPath

PACK_FORMAT = "soundboard-pack"
PACK_VERSION = 1
MANIFEST_NAME = "manifest.json"
MAX_UNCOMPRESSED_BYTES = 2 * 1024 ** 3
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".webm")


class PackError(Exception):
    """User-facing pack validation/IO error."""


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_entry_name(name: str) -> str:
    """Rejects absolute paths, drive letters and any '..' segment."""
    raw = str(name or "").replace("\\", "/")
    if not raw or "\x00" in raw or raw.startswith("/"):
        raise PackError("Pacote inválido: caminho de arquivo suspeito.")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise PackError("Pacote inválido: caminho de arquivo suspeito.")
    if raw[1:2] == ":":
        raise PackError("Pacote inválido: caminho de arquivo suspeito.")
    return raw


def build_manifest(clips: list[dict], speakers: dict, created_at: float = None) -> dict:
    entries = []
    used_speakers: dict[str, dict] = {}
    for clip in clips:
        clip_id = str(clip.get("id") or "")
        file_path = str(clip.get("file_path") or "")
        ext = Path(file_path).suffix.lower()
        spk_ids = clip.get("speaker_ids") or ([clip.get("speaker_id")] if clip.get("speaker_id") else [])
        names = []
        for sid in spk_ids:
            spk = speakers.get(sid) or {}
            name = str(spk.get("name") or "").strip()
            if not name:
                continue
            names.append(name)
            used_speakers.setdefault(name.casefold(), {"name": name, "color": spk.get("color") or ""})
        entries.append({
            "id": clip_id,
            "file": f"audio/{clip_id}{ext}",
            "title": clip.get("title") or "",
            "emoji": clip.get("emoji") or "🔊",
            "category": clip.get("category") or "Call",
            "color": clip.get("color") or "#3b82f6",
            "hotkey": clip.get("hotkey") or "",
            "trim_start": float(clip.get("trim_start") or 0.0),
            "trim_end": float(clip.get("trim_end") or 0.0),
            "volume": float(clip.get("volume") if clip.get("volume") is not None else 1.0),
            "transcript": clip.get("transcript") or "",
            "acoustic_mood": clip.get("acoustic_mood") or "Normal",
            "tags": [str(t) for t in (clip.get("tags") or [])][:32],
            "duration": float(clip.get("duration") or 0.0),
            "favorite": bool(clip.get("favorite")),
            "speaker_names": names,
        })
    return {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "created_at": float(created_at or time.time()),
        "clips": entries,
        "speakers": list(used_speakers.values()),
    }


def write_pack(path: str | Path, clips: list[dict], speakers: dict) -> dict:
    """Writes the zip at ``path`` (caller passes a .tmp path). Missing audio is skipped."""
    existentes = [c for c in clips if Path(str(c.get("file_path") or "")).is_file()]
    skipped = len(clips) - len(existentes)
    manifest = build_manifest(existentes, speakers)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
        for entry in manifest["clips"]:
            src = Path(str(next(c.get("file_path") for c in existentes if c.get("id") == entry["id"])))
            archive.write(src, entry["file"])
    if not manifest["clips"]:
        raise PackError("Nenhum áudio encontrado para exportar.")
    return {"count": len(manifest["clips"]), "skipped": skipped}


def validate_pack(path: str | Path) -> dict:
    """Validates the zip end-to-end and returns {manifest, entries: {name: size}}."""
    try:
        archive_path = Path(path)
        if not archive_path.is_file():
            raise PackError("Pacote não encontrado.")
        with zipfile.ZipFile(archive_path) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
            total = 0
            entries: dict[str, int] = {}
            for info in infos:
                name = _safe_entry_name(info.filename)
                if name != MANIFEST_NAME and not name.startswith("audio/"):
                    raise PackError("Pacote inválido: arquivo fora de audio/.")
                if name.startswith("audio/") and PurePosixPath(name).suffix.lower() not in AUDIO_EXTS:
                    raise PackError("Pacote inválido: formato de áudio não suportado.")
                total += int(info.file_size or 0)
                entries[name] = int(info.file_size or 0)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise PackError("Pacote grande demais (limite de 2 GB).")
            if MANIFEST_NAME not in entries:
                raise PackError("Pacote inválido: manifest.json ausente.")
            try:
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            except Exception as exc:
                raise PackError("Pacote inválido: manifest.json ilegível.") from exc
    except zipfile.BadZipFile as exc:
        raise PackError("Arquivo não é um pacote .zip válido.") from exc

    if not isinstance(manifest, dict) or manifest.get("format") != PACK_FORMAT:
        raise PackError("Pacote inválido: formato desconhecido.")
    version = manifest.get("version")
    if not isinstance(version, int) or version > PACK_VERSION:
        raise PackError("Pacote criado por uma versão mais nova do Soundboard.")
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not clips:
        raise PackError("Pacote vazio.")
    for entry in clips:
        if not isinstance(entry, dict):
            raise PackError("Pacote inválido: manifesto corrompido.")
        name = _safe_entry_name(entry.get("file") or "")
        if name not in entries:
            raise PackError("Pacote inválido: áudio declarado não existe no zip.")
    return {"manifest": manifest, "entries": entries}


def copy_entry(archive: zipfile.ZipFile, name: str, dst_path: Path) -> tuple[str, int]:
    """Streams one entry to ``dst_path``, hashing as it goes. Returns (sha256, bytes)."""
    digest = hashlib.sha256()
    total = 0
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with archive.open(name) as src, open(dst_path, "wb") as out:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                total += len(chunk)
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise PackError("Pacote grande demais (limite de 2 GB).")
                digest.update(chunk)
                out.write(chunk)
    except KeyError as exc:
        raise PackError(f"Pacote inválido: {name} ausente.") from exc
    except zipfile.BadZipFile as exc:
        raise PackError("Pacote corrompido.") from exc
    return digest.hexdigest(), total
```

`write_pack` ignora áudios ausentes **antes** de montar o manifesto (o zip nunca declara um arquivo que não foi escrito) e conta esses casos em `skipped`.

- [ ] **Step 2: testes**

```python
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import packs


def _zip(path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def _manifest(**over):
    base = {"format": "soundboard-pack", "version": 1, "clips": [
        {"file": "audio/a.wav", "title": "A"},
    ], "speakers": []}
    base.update(over)
    return base


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
    assert [c["id"] for c in manifest["clips"]] == ["a"]
```

- [ ] **Step 3: rodar** — `uv run pytest -q tests/test_f2_packs.py` → 5 passed (só Task 3).

---

### Task 4: Backend — `export_pack` e `import_pack`

**Files:** `main.py`, `tests/test_f2_packs.py`

- [ ] **Step 1: implementar em `main.py`**

Imports no topo: `import uuid`, `import zipfile`, `import packs`, `import soundfile as sf` **lazy** dentro da função (o módulo já é dependência do engine, mas evite custo no boot: use `import soundfile as sf` no início de `import_pack`).

```python
    def _ensure_imported_speaker(self, name: str, color: str = "") -> str:
        clean = (name or "").strip()
        if not clean:
            return ""
        speakers = self._store.snapshot()["speakers"]
        for spk in speakers.values():
            if str(spk.get("name") or "").strip().casefold() == clean.casefold():
                return spk["id"]
        idx = len(speakers)
        spk_id = f"interlocutor_{idx + 1}"
        while spk_id in speakers:
            idx += 1
            spk_id = f"interlocutor_{idx + 1}"
        self._store.ensure_speaker(spk_id, default_name=clean)
        if color:
            self._store.update_speaker(spk_id, color=color)
        return spk_id

    def export_pack(self, clip_ids: list[str] = None, path: str = None) -> dict:
        snap = self._store.snapshot()
        clips = self._public_clips(snap["clips"])
        if clip_ids is not None:
            wanted = {str(i) for i in clip_ids}
            clips = [c for c in clips if c["id"] in wanted]
        if not clips:
            return {"ok": False, "error": "Nenhum áudio para exportar.", "count": 0, "skipped": 0}

        if not path and self._window is not None:
            try:
                import webview
                chosen = self._window.create_file_dialog(
                    webview.SAVE_DIALOG,
                    save_filename=f"soundboard-pack-{time.strftime('%Y%m%d-%H%M')}.zip",
                    file_types=("Pacote do Soundboard (*.zip)",),
                )
                path = chosen if isinstance(chosen, str) else (chosen[0] if chosen else None)
            except Exception:
                path = None
        if not path:
            return {"ok": False, "error": "Exportação cancelada.", "count": 0, "skipped": 0}

        out = Path(path)
        if out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        tmp = out.with_suffix(out.suffix + ".tmp")
        try:
            res = packs.write_pack(tmp, clips, snap["speakers"])
            os.replace(tmp, out)
        except packs.PackError as exc:
            tmp.unlink(missing_ok=True)
            return {"ok": False, "error": str(exc), "count": 0, "skipped": 0}
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            return {"ok": False, "error": f"Falha ao exportar: {exc}", "count": 0, "skipped": 0}
        return {"ok": True, "path": str(out), "count": res["count"], "skipped": res["skipped"]}

    def import_pack(self, path: str = None) -> dict:
        if not path and self._window is not None:
            try:
                import webview
                chosen = self._window.create_file_dialog(
                    webview.OPEN_DIALOG,
                    allow_multiple=False,
                    file_types=("Pacote do Soundboard (*.zip)", "Todos os arquivos (*.*)"),
                )
                path = chosen if isinstance(chosen, str) else (chosen[0] if chosen else None)
            except Exception:
                path = None
        if not path:
            snap = self._store.snapshot()
            return {"ok": False, "error": "Importação cancelada.", "imported": [], "skipped": 0,
                    "warnings": [], "clips": self._public_clips(snap["clips"]), "speakers": snap["speakers"]}

        try:
            pack = packs.validate_pack(path)
        except packs.PackError as exc:
            snap = self._store.snapshot()
            return {"ok": False, "error": str(exc), "imported": [], "skipped": 0,
                    "warnings": [], "clips": self._public_clips(snap["clips"]), "speakers": snap["speakers"]}

        import soundfile as sf
        manifest = pack["manifest"]
        entries = manifest["clips"]
        speakers_meta = {str(s.get("name") or "").casefold(): s for s in (manifest.get("speakers") or []) if isinstance(s, dict)}
        existing = self._store.snapshot()
        used_hotkeys = {str(c.get("hotkey") or "").lower() for c in self._public_clips(existing["clips"]) if c.get("hotkey")}
        known_hashes = {}
        for clip in self._public_clips(existing["clips"]):
            fp = str(clip.get("file_path") or "")
            try:
                known_hashes[packs.sha256_file(fp)] = clip["id"]
            except OSError:
                continue

        imported, warnings = [], []
        skipped = 0
        total = len(entries)
        try:
            with zipfile.ZipFile(path) as archive:
                for idx, entry in enumerate(entries, 1):
                    self._emit_progress(True, idx, total, f"Importando pacote ({idx}/{total})", entry.get("title") or "")
                    name = str(entry.get("file") or "")
                    ext = Path(name).suffix.lower()
                    if ext not in packs.AUDIO_EXTS:
                        skipped += 1
                        warnings.append(f"{entry.get('title') or name}: formato não suportado.")
                        continue

                    new_id = uuid.uuid4().hex[:10]
                    part = self._store.clips_dir / f"{new_id}{ext}.part"
                    try:
                        digest, _size = packs.copy_entry(archive, name, part)
                    except packs.PackError as exc:
                        part.unlink(missing_ok=True)
                        skipped += 1
                        warnings.append(f"{entry.get('title') or name}: {exc}")
                        continue

                    if digest in known_hashes:
                        part.unlink(missing_ok=True)
                        skipped += 1
                        warnings.append(f"{entry.get('title') or name}: já existe na biblioteca.")
                        continue

                    final = self._store.clips_dir / f"{new_id}{ext}"
                    os.replace(part, final)
                    known_hashes[digest] = new_id

                    try:
                        duration = float(sf.info(str(final)).duration or 0.0)
                    except Exception:
                        duration = float(entry.get("duration") or 0.0)
                    duration = max(0.2, duration or float(entry.get("duration") or 0.2))

                    spk_ids = []
                    for spk_name in entry.get("speaker_names") or []:
                        meta = speakers_meta.get(str(spk_name).strip().casefold(), {})
                        sid = self._ensure_imported_speaker(str(spk_name), str(meta.get("color") or ""))
                        if sid and sid not in spk_ids:
                            spk_ids.append(sid)

                    hotkey = str(entry.get("hotkey") or "").strip()
                    if hotkey and hotkey.lower() in used_hotkeys:
                        hotkey = ""
                        warnings.append(f"{entry.get('title') or name}: atalho já em uso, importado sem atalho.")
                    if hotkey:
                        used_hotkeys.add(hotkey.lower())

                    trim_start = max(0.0, min(duration, float(entry.get("trim_start") or 0.0)))
                    trim_end = float(entry.get("trim_end") or 0.0)
                    trim_end = max(trim_start, min(duration, trim_end)) if trim_end > trim_start else duration
                    volume = max(0.0, min(3.0, float(entry.get("volume") if entry.get("volume") is not None else 1.0)))

                    clip = self._store.add_or_update_clip({
                        "id": new_id,
                        "title": str(entry.get("title") or "Áudio importado")[:60],
                        "file_path": str(final),
                        "category": str(entry.get("category") or "Call"),
                        "speaker_id": spk_ids[0] if spk_ids else "",
                        "speaker_ids": spk_ids,
                        "segments": [],
                        "transcript": str(entry.get("transcript") or ""),
                        "acoustic_mood": str(entry.get("acoustic_mood") or "Normal"),
                        "emoji": str(entry.get("emoji") or "🔊")[:4],
                        "color": str(entry.get("color") or "#3b82f6"),
                        "tags": [str(t) for t in (entry.get("tags") or [])][:32],
                        "hotkey": hotkey,
                        "trim_start": trim_start,
                        "trim_end": trim_end,
                        "volume": volume,
                        "favorite": bool(entry.get("favorite")),
                        "duration": duration,
                        "created_at": time.time(),
                        "pending": False,
                    })
                    imported.append(clip)
        finally:
            self._emit_progress(False)

        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        self._notify_ui("clips_updated", {"clips": self._public_clips(snap["clips"]), "speakers": snap["speakers"]})
        return {
            "ok": True,
            "imported": copy.deepcopy(imported),
            "skipped": skipped,
            "warnings": warnings,
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }
```

- [ ] **Step 2: testes de integração**

```python
from main import SoundboardAPI


def _api(tmp_path, monkeypatch, sub="a"):
    api = SoundboardAPI(data_dir=str(tmp_path / sub), start_background_services=False)
    api._hotkeys.enabled = False
    monkeypatch.setattr(api._hotkeys, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(api._audio, "set_mic_passthrough", lambda *a, **k: None)
    return api


def _clip(api, title, seconds=1.0, **extra):
    wav = api._store.clips_dir / f"{title}.wav"
    sf.write(str(wav), np.zeros((int(48000 * seconds), 2), dtype=np.float32), 48000)
    data = {"title": title, "file_path": str(wav), "duration": seconds, "category": "Call"}
    data.update(extra)
    return api._store.add_or_update_clip(data)


def test_export_import_round_trip_preserves_content(tmp_path, monkeypatch):
    src = _api(tmp_path, monkeypatch, "src")
    speaker = src._store.ensure_speaker("interlocutor_1", default_name="Lucas")
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
```

- [ ] **Step 3: rodar** — `uv run pytest -q tests/test_f2_packs.py` e `uv run pytest -q`.

---

### Task 5: UI — modo draft no modal de edição

**Files:** `ui/index.html`, `ui/app.js`, `ui/styles.css`

- [ ] **Step 1: markup**

No `modal-footer` (linha ~349), adicionar antes de `#btnSaveClip`:

```html
            <button type="button" id="btnDiscardDraft" class="btn btn-secondary hidden" title="Jogar este replay fora sem salvar">
              🗑️ Descartar
            </button>
```

- [ ] **Step 2: modo draft**

Em `openEditModal(clip)`, no início:

```js
  state.draftClipId = clip.pending ? clip.id : null;
  applyDraftChrome(Boolean(clip.pending));
```

Novas funções (perto de `openEditModal`):

```js
function applyDraftChrome(isDraft) {
  document.getElementById("modalTitle").textContent = isDraft
    ? "⚡ Replay — revise antes de salvar"
    : "⚙️ Configurar Botão do Soundboard";
  document.getElementById("btnSaveClip").textContent = isDraft ? "✅ Salvar no Soundboard" : "💾 Salvar";
  document.getElementById("btnDiscardDraft").classList.toggle("hidden", !isDraft);
  document.getElementById("btnDeleteClip").classList.toggle("hidden", isDraft);
  document.getElementById("btnSplitSpeakers").classList.toggle("hidden", isDraft);
}

function closeEditModal() {
  document.getElementById("editModal").classList.add("hidden");
  updateModalPreviewButtons(false);
  state.draftClipId = null;
  applyDraftChrome(false);
}

async function discardDraftAndClose({ silent = false } = {}) {
  const id = state.draftClipId;
  state.draftClipId = null;
  if (id) {
    const res = await api().discard_replay_draft(id);
    if (res && res.ok) {
      state.clips = res.clips || state.clips;
      state.speakers = res.speakers || state.speakers;
    }
  }
  closeEditModal();
  renderSidebar();
  renderGrid();
  if (!silent && id) showToast("Replay descartado.");
}

async function commitReplayDraft() {
  const id = state.draftClipId;
  if (!id) return null;
  await saveCurrentClipEdits(false);
  const res = await api().commit_replay_draft(id);
  if (!res || !res.ok) {
    showToast((res && res.error) || "Não foi possível salvar o replay.", "#ef4444");
    return null;
  }
  state.draftClipId = null;
  state.clips = res.clips || state.clips;
  state.speakers = res.speakers || state.speakers;
  closeEditModal();
  renderSidebar();
  renderGrid();
  if (res.split_count) {
    showToast(`⚡ Replay separado em ${res.split_count} botões!`, "#10b981");
  } else {
    showToast(`⚡ Replay salvo: "${res.clip.title}"`, "#10b981");
  }
  return res.clip;
}
```

Adicionar `draftClipId: null` ao objeto `state` (topo do arquivo).

- [ ] **Step 3: handlers**

Substituir `btnSaveClip`:

```js
  document.getElementById("btnSaveClip").onclick = async () => {
    if (state.draftClipId) {
      await commitReplayDraft();
      return;
    }
    await saveCurrentClipEdits(true);
  };

  document.getElementById("btnDiscardDraft").onclick = () => discardDraftAndClose();
```

Substituir o fechamento do X (linha ~1256) por `discardDraftAndClose()` quando houver draft, senão fechar normal:

```js
  document.getElementById("btnCloseModal").onclick = () => {
    if (state.draftClipId) {
      discardDraftAndClose();
      return;
    }
    closeEditModal();
  };
```

Trocar os `document.getElementById("editModal").classList.add("hidden")` restantes (linhas ~1377, 1443, 1472) por `closeEditModal()`.

- [ ] **Step 4: fluxo do botão Clipar**

Substituir o handler de `btnReplayClip`:

```js
  document.getElementById("btnReplayClip").onclick = async () => {
    const secs = Math.max(1, Math.min(60, Number(replaySecsInput.value) || 15));
    showToast(`⚡ Capturando os últimos ${secs}s da call...`, "#f59e0b");
    const res = await api().capture_replay_draft(secs);
    if (!res || !res.ok) {
      showToast((res && res.error) || "Sem áudio recente no buffer da call.", "#ef4444");
      return;
    }
    state.clips = res.clips || state.clips;
    state.speakers = res.speakers || state.speakers;
    renderSidebar();
    renderGrid();
    openEditModal(res.clip);
    showToast("✂️ Revise o corte e clique em Salvar no Soundboard.", "#3b82f6");
  };
```

- [ ] **Step 5: CSS**

```css
.draft-footer { display: flex; gap: 8px; }
#editModal .modal-footer .btn.hidden { display: none; }
```

- [ ] **Step 6: verificar** — `node --check ui/app.js`.

---

### Task 6: UI — menu de packs e avisos

**Files:** `ui/index.html`, `ui/app.js`, `ui/styles.css`

- [ ] **Step 1: markup** (depois do `#btnImportFolder` em `deck-actions`)

```html
        <div class="routing-pill-wrapper pack-wrapper">
          <button type="button" id="btnPackMenu" class="btn btn-secondary" title="Exportar ou importar a biblioteca como pacote .zip">
            📦 Pack
          </button>
          <div id="packPopover" class="routing-popover hidden pack-popover">
            <div class="popover-header">
              <h4>📦 Pacotes</h4>
              <button type="button" id="btnClosePackMenu" class="btn-clear" title="Fechar">✕</button>
            </div>
            <div class="popover-body">
              <button type="button" id="btnExportPackAll" class="btn btn-secondary">📤 Exportar biblioteca</button>
              <button type="button" id="btnExportPackFiltered" class="btn btn-secondary">🔎 Exportar filtro atual</button>
              <button type="button" id="btnImportPack" class="btn btn-secondary">📥 Importar pacote…</button>
            </div>
          </div>
        </div>
```

E o modal de avisos antes de `</body>`:

```html
    <div id="packWarningsModal" class="modal-backdrop hidden">
      <div class="modal-card modal-sm">
        <div class="modal-header">
          <h2>⚠️ Importação concluída com avisos</h2>
          <button id="btnClosePackWarnings" class="btn-clear">✕</button>
        </div>
        <div class="modal-body">
          <ul id="packWarningsList" class="pack-warnings"></ul>
        </div>
      </div>
    </div>
```

- [ ] **Step 2: handlers em `bindEvents()`**

```js
  const packMenu = document.getElementById("packPopover");
  const packButton = document.getElementById("btnPackMenu");
  function hidePackMenu() {
    if (packMenu) packMenu.classList.add("hidden");
  }
  if (packButton && packMenu) {
    packButton.onclick = (e) => {
      e.stopPropagation();
      packMenu.classList.toggle("hidden");
    };
    document.getElementById("btnClosePackMenu").onclick = hidePackMenu;
    document.addEventListener("click", (e) => {
      if (packMenu.classList.contains("hidden")) return;
      if (!packMenu.contains(e.target) && e.target !== packButton) hidePackMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !packMenu.classList.contains("hidden")) hidePackMenu();
    });
  }

  function showPackWarnings(warnings) {
    const list = document.getElementById("packWarningsList");
    if (!list) return;
    list.innerHTML = "";
    warnings.forEach((w) => {
      const li = document.createElement("li");
      li.textContent = w;
      list.appendChild(li);
    });
    document.getElementById("packWarningsModal").classList.remove("hidden");
  }

  const closePackWarnings = document.getElementById("btnClosePackWarnings");
  if (closePackWarnings) {
    closePackWarnings.onclick = () =>
      document.getElementById("packWarningsModal").classList.add("hidden");
  }

  document.getElementById("btnExportPackAll").onclick = async () => {
    hidePackMenu();
    showToast("📦 Empacotando a biblioteca...", "#8b5cf6");
    const res = await api().export_pack(null, null);
    if (!res || !res.ok) {
      if (res?.error !== "Exportação cancelada.") {
        showToast((res && res.error) || "Falha ao exportar.", "#ef4444");
      }
      return;
    }
    const extra = res.skipped ? ` (${res.skipped} sem arquivo)` : "";
    showToast(`📦 ${res.count} áudio(s) exportados${extra}.`, "#10b981");
  };

  document.getElementById("btnExportPackFiltered").onclick = async () => {
    hidePackMenu();
    const ids = getFilteredClips().map((c) => c.id);
    if (!ids.length) {
      showToast("Nenhum áudio no filtro atual.", "#f59e0b");
      return;
    }
    const res = await api().export_pack(ids, null);
    if (!res || !res.ok) {
      if (res?.error !== "Exportação cancelada.") {
        showToast((res && res.error) || "Falha ao exportar.", "#ef4444");
      }
      return;
    }
    showToast(`📦 ${res.count} áudio(s) do filtro exportados.`, "#10b981");
  };

  document.getElementById("btnImportPack").onclick = async () => {
    hidePackMenu();
    const res = await api().import_pack(null);
    if (!res || !res.ok) {
      if (res?.error !== "Importação cancelada.") {
        showToast((res && res.error) || "Falha ao importar.", "#ef4444");
      }
      return;
    }
    state.clips = res.clips || state.clips;
    state.speakers = res.speakers || state.speakers;
    renderSidebar();
    renderGrid();
    showToast(`📦 ${res.imported.length} importado(s), ${res.skipped} ignorado(s).`, "#10b981");
    if (res.warnings && res.warnings.length) showPackWarnings(res.warnings);
  };
```

- [ ] **Step 3: CSS**

```css
.pack-wrapper { position: relative; }
.pack-popover { right: 0; left: auto; min-width: 240px; }
.pack-popover .popover-body { display: flex; flex-direction: column; gap: 8px; padding: 10px; }
.pack-popover .btn { justify-content: flex-start; width: 100%; }
.pack-warnings { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.6; }
```

- [ ] **Step 4: verificar** — `node --check ui/app.js`; conferir que todo `getElementById` novo existe no HTML.

---

### Task 7: Verificação e revisão

- [ ] `uv run pytest -q` — tudo verde.
- [ ] `node --test tests/test_fuzzy.test.cjs` — 11 pass (F1 intacto).
- [ ] `node --check ui/app.js` / `ui/fuzzy.js` — exit 0.
- [ ] `python -m compileall -q main.py store.py packs.py` — exit 0.
- [ ] `git diff --check` — limpo.
- [ ] Revisão independente (read-only) cobrindo: path traversal/limite de tamanho no zip, arquivos `.part` órfãos, dedupe por hash (custo, colisão com o próprio import), hotkey conflitante, draft vazando para `get_initial_state`/hotkeys/undo, commit com arquivo externo, `discard` apagando fora de `tmp`, janela de corrida entre commit e reanálise, e regressão do fluxo de salvar/editar clipes normais.
- [ ] Smoke test manual: Clipar Call → modal de draft → testar no fone → Salvar; repetir e Descartar (arquivo some de `data/tmp`); fechar no X → descarta; exportar biblioteca → importar em `data` limpo; abrir pacote com atalho duplicado → clipe entra sem atalho e aviso aparece.
