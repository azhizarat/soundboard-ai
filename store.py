import os
import copy
import json
import uuid
import shutil
import time
import threading
from functools import wraps
from pathlib import Path
from sound_sequences import SequenceStore
from sound_collections import CollectionStore

DEFAULT_CATEGORIES = [
    {"id": "Rage", "name": "Rage", "emoji": "🔥", "color": "#ef4444"},
    {"id": "Risada", "name": "Risadas & Pérolas", "emoji": "😂", "color": "#f59e0b"},
    {"id": "Meme", "name": "Bordões & Memes", "emoji": "⚡", "color": "#8b5cf6"},
    {"id": "Vinhetas", "name": "Vinhetas & Trilhas", "emoji": "🎵", "color": "#06b6d4"},
    {"id": "Call", "name": "Papo & Resenha", "emoji": "💬", "color": "#3b82f6"},
]

SPEAKER_PALETTE = [
    "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#06b6d4", "#84cc16"
]

DEFAULT_SETTINGS = {
    "cable_device_id": None,
    "cable_device_name": "",
    "monitor_device_id": None,
    "monitor_device_name": "",
    "mic_device_id": None,
    "mic_device_name": "",
    "cable_volume": 1.0,
    "monitor_volume": 0.75,
    "mic_passthrough": False,
    "mic_gain": 1.0,
    "duck_enabled": False,
    "duck_amount_db": -12.0,
    "duck_threshold_dbfs": -40.0,
    "overlap_audio": False,
    "auto_split_speakers": False,
    "normalize_on_import": True,
    "replay_seconds": 15,
    "stop_all_hotkey": "ctrl+shift+s",
    "replay_hotkey": "alt+f10",
    "overlay_hotkey": "ctrl+shift+o",
    "use_local_llm": True,
    "learn_voice_corrections": False,
    "voice_match_threshold": 0.55,
    "voice_match_margin": 0.08,
    "active_collection_id": None,
}


CURRENT_SCHEMA_VERSION = 6
SQLITE_TRIGGER_CLIPS = 2000
SQLITE_TRIGGER_SAVE_MS = 150.0
SQLITE_TRIGGER_JSON_MB = 10.0


def _atomic_analysis(method):
    """Rollback apenas do clipe/rascunho envolvidos; preserva trabalho concorrente."""
    @wraps(method)
    def wrapped(self, target, *args, **kwargs):
        clip_id = target['clip_id'] if isinstance(target, dict) else target
        with self._lock:
            clip = self.get_clip(clip_id)
            previous = copy.deepcopy(clip)
            draft = copy.deepcopy(self.state['edit_drafts'].get(clip_id))
            try:
                return method(self, target, *args, **kwargs)
            except Exception:
                if clip is not None:
                    clip.clear(); clip.update(previous)
                if draft is not None:
                    self.state['edit_drafts'][clip_id] = draft
                raise
    return wrapped


class SoundboardStore(SequenceStore, CollectionStore):
    """Storage manager with schema versioning and coalesced write-behind.

    SQLite Migration Trigger:
    When any of the following conditions is met, persistence should be migrated from JSON to SQLite:
      1. Number of clips > SQLITE_TRIGGER_CLIPS (2,000 clips)
      2. Save duration > SQLITE_TRIGGER_SAVE_MS (150 ms)
      3. soundboard.json size > SQLITE_TRIGGER_JSON_MB (10 MB)
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir).resolve()
        self.clips_dir = self.data_dir / "clips"
        self.models_dir = self.data_dir / "models"
        self.tmp_dir = self.data_dir / "tmp"
        self.sources_dir = self.clips_dir / "sources"
        self._active_sources = {}
        self.db_path = self.data_dir / "soundboard.json"
        self._lock = threading.RLock()
        self._save_condition = threading.Condition(self._lock)
        self._dirty = False
        self._save_deadline = 0.0
        self._stop_worker = False
        self._last_save_duration_ms = 0.0

        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.state = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "settings": dict(DEFAULT_SETTINGS),
            "categories": list(DEFAULT_CATEGORIES),
            "speakers": {},
            "clips": [],
            "sources": {},
            "revisions": {},
            "edit_drafts": {},
            "source_references": {},
            "sequences": {},
            "collections": {},
        }
        from voice_profiles import empty_bank
        self.state['voice_bank'] = empty_bank()
        self._load()

        self._worker_thread = threading.Thread(
            target=self._write_behind_loop, daemon=True, name="StoreWriteBehind"
        )
        self._worker_thread.start()

    def _load(self):
        with self._lock:
            if not self.db_path.exists():
                return
            try:
                raw_text = self.db_path.read_text(encoding="utf-8")
                loaded = json.loads(raw_text)
            except Exception:
                return
            if not isinstance(loaded, dict):
                return

            schema_version = int(loaded.get("schema_version") or 0)
            if schema_version > CURRENT_SCHEMA_VERSION or schema_version < 0:
                raise ValueError('Banco criado por uma versão não suportada')
            if schema_version < CURRENT_SCHEMA_VERSION:
                # Sem backup confirmado nenhuma gravação/migração é permitida.
                backup = self.db_path.with_suffix('.json.bak')
                if not backup.exists():
                    backup.write_text(raw_text, encoding='utf-8')
                if schema_version == 0:
                    loaded = self._migrate_v0_to_v1(loaded)
                if int(loaded.get('schema_version', 0)) < 2:
                    loaded = self._migrate_v1_to_v2(loaded)
                if int(loaded.get('schema_version', 0)) < 3:
                    migration_backup = self.data_dir / f'soundboard.v{schema_version}.bak.json'
                    if not migration_backup.exists():
                        migration_backup.write_text(raw_text, encoding='utf-8')
                    if migration_backup.read_text(encoding='utf-8') != raw_text:
                        raise ValueError('Backup da migração vocal diverge do banco atual')
                    from voice_profiles import empty_bank
                    loaded.setdefault('voice_bank', empty_bank())
                    loaded['schema_version'] = 3
                if int(loaded.get('schema_version', 0)) < 4:
                    sequence_backup = self.data_dir / f'soundboard.v{schema_version}.sequences.bak.json'
                    if not sequence_backup.exists():
                        sequence_backup.write_text(raw_text, encoding='utf-8')
                    if sequence_backup.read_text(encoding='utf-8') != raw_text:
                        raise ValueError('Backup da migração de sequências diverge do banco atual')
                    loaded.setdefault('sequences', {})
                    loaded['schema_version'] = 4
                if int(loaded.get('schema_version', 0)) < 5:
                    collection_backup = self.data_dir / f'soundboard.v{schema_version}.collections.bak.json'
                    if not collection_backup.exists():
                        collection_backup.write_text(raw_text, encoding='utf-8')
                    if collection_backup.read_text(encoding='utf-8') != raw_text:
                        raise ValueError('Backup da migração de coleções diverge do banco atual')
                    loaded.setdefault('collections', {})
                    loaded['schema_version'] = 5
                if int(loaded.get('schema_version', 0)) < 6:
                    variant_backup = self.data_dir / f'soundboard.v{schema_version}.variants.bak.json'
                    if not variant_backup.exists():
                        variant_backup.write_text(raw_text, encoding='utf-8')
                    if variant_backup.read_text(encoding='utf-8') != raw_text:
                        raise ValueError('Backup da migração de variantes diverge do banco atual')
                    loaded['schema_version'] = 6
                self._apply_loaded(loaded)
                self._perform_save()
            else:
                self._apply_loaded(loaded)

    def _migrate_v0_to_v1(self, loaded: dict):
        loaded['schema_version'] = 1
        return loaded

    def _migrate_v1_to_v2(self, loaded: dict):
        from clip_renderer import preserve_source
        sources = loaded.setdefault('sources', {})
        loaded.setdefault('revisions', {})
        loaded.setdefault('edit_drafts', {})
        loaded.setdefault('source_references', {})
        for clip in loaded.get('clips', []):
            clip.setdefault('field_provenance', {'transcript': 'unknown', 'speaker_id': 'unknown'})
            if clip.get('pending'):
                continue
            try:
                source = preserve_source(clip.get('file_path', ''), self.sources_dir)
            except (OSError, ValueError, RuntimeError):
                source = {'id': uuid.uuid4().hex, 'file_path': clip.get('file_path', ''),
                          'available': False, 'total_samples': round(float(clip.get('duration', 0)) * 48000)}
            sources.setdefault(source['id'], source)
            clip['source_id'] = source['id']
        loaded['schema_version'] = 2
        return loaded

    def _apply_loaded(self, loaded: dict):
        # Conserva registros e metadados futuros dentro do schema conhecido.
        self.state.update(copy.deepcopy(loaded))
        self.state["schema_version"] = int(loaded.get("schema_version", CURRENT_SCHEMA_VERSION))
        self.state["settings"] = {**DEFAULT_SETTINGS, **loaded.get("settings", {})}

        cat_map = {c["id"]: dict(c) for c in DEFAULT_CATEGORIES}

        # Migrate clips with legacy categories
        clips = loaded.get("clips") or []
        for c in clips:
            cat = c.get("category")
            if cat == "Efeito":
                c["category"] = "Vinhetas"
            elif cat == "Clássico":
                c["category"] = "Meme"
            elif cat not in cat_map:
                c["category"] = "Call"
        self.state["clips"] = clips

        # Migrate loaded category objects
        raw_cats = loaded.get("categories") or []
        migrated_cats = []
        for c in raw_cats:
            cid = c.get("id")
            if cid in ("Efeito", "Clássico"):
                continue
            if cid in cat_map:
                migrated_cats.append({**c, **cat_map[cid]})
            else:
                migrated_cats.append(c)

        existing_ids = {c["id"] for c in migrated_cats if isinstance(c, dict)}
        for def_cat in DEFAULT_CATEGORIES:
            if def_cat["id"] not in existing_ids:
                migrated_cats.append(dict(def_cat))

        self.state["categories"] = migrated_cats or list(DEFAULT_CATEGORIES)
        self.state["speakers"] = loaded.get("speakers") or {}

    def _perform_save(self):
        """Must be called with self._lock held."""
        t0 = time.perf_counter()
        tmp_path = self.db_path.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, self.db_path)
            self._dirty = False
            self._save_deadline = 0.0
            self._last_save_duration_ms = (time.perf_counter() - t0) * 1000.0
        except Exception:
            self._dirty = True
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            raise

    def save(self):
        with self._lock:
            self._perform_save()
            self._save_condition.notify_all()

    def _write_behind_loop(self):
        """Dedicated worker thread coalescing debounced disk writes."""
        while True:
            with self._save_condition:
                while not self._stop_worker and not self._dirty:
                    self._save_condition.wait()
                if self._stop_worker:
                    if self._dirty:
                        try:
                            self._perform_save()
                        except Exception:
                            pass
                    break

                now = time.time()
                if now < self._save_deadline:
                    timeout = max(0.01, self._save_deadline - now)
                    self._save_condition.wait(timeout=timeout)
                    continue

                try:
                    self._perform_save()
                except Exception:
                    self._dirty = True
                    self._save_deadline = time.time() + 1.0

    def mark_dirty(self, delay: float = 0.75):
        """Marks state dirty and schedules coalesced write-behind persistence."""
        with self._save_condition:
            self._dirty = True
            deadline = time.time() + max(0.0, float(delay))
            if not self._save_deadline or deadline < self._save_deadline:
                self._save_deadline = deadline
            self._save_condition.notify_all()

    def save_debounced(self, delay: float = 0.75):
        self.mark_dirty(delay)

    def flush(self):
        """Cancels any pending debounced save and persists immediately when dirty."""
        with self._lock:
            if self._dirty:
                try:
                    self._perform_save()
                except Exception:
                    self._dirty = True
            self._save_condition.notify_all()

    def close(self):
        """Drains dirty state and stops the write-behind thread."""
        with self._save_condition:
            self._stop_worker = True
            self._save_condition.notify_all()
        if hasattr(self, "_worker_thread") and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

    def snapshot(self) -> dict:
        """Deep copy of the whole state, safe to serialize or mutate by callers."""
        with self._lock:
            return copy.deepcopy(self.state)

    def ensure_speaker(self, spk_id: str, default_name: str = None) -> dict:
        if not spk_id:
            return {}
        with self._lock:
            if spk_id not in self.state["speakers"]:
                idx = len(self.state["speakers"])
                num = spk_id.split("_")[-1] if "_" in spk_id else str(idx + 1)
                self.state["speakers"][spk_id] = {
                    "id": spk_id,
                    "name": default_name or f"Amigo {num}",
                    "color": SPEAKER_PALETTE[idx % len(SPEAKER_PALETTE)],
                    "avatar": "🎧",
                }
            return self.state["speakers"][spk_id]

    def update_speaker(self, spk_id: str, name: str, color: str = None, avatar: str = None) -> dict:
        with self._lock:
            spk = self.ensure_speaker(spk_id)
            if name and name.strip():
                spk["name"] = name.strip()
            if color:
                spk["color"] = color
            if avatar:
                spk["avatar"] = avatar
            self.save()
            return spk

    def delete_speaker(self, spk_id: str) -> bool:
        if not spk_id:
            return False
        with self._lock:
            existed = spk_id in self.state["speakers"]
            self.state["speakers"].pop(spk_id, None)
            for c in self.state["clips"]:
                spks = [s for s in (c.get("speaker_ids") or []) if s != spk_id]
                c["speaker_ids"] = spks
                if c.get("speaker_id") == spk_id:
                    c["speaker_id"] = spks[0] if spks else ""
            self.save()
            return existed

    def update_settings(self, updates: dict) -> dict:
        with self._lock:
            self.validate_settings_bindings(updates or {})
            self.state["settings"].update(updates or {})
            self.save()
            return dict(self.state["settings"])

    def get_clip(self, clip_id: str) -> dict | None:
        with self._lock:
            for c in self.state["clips"]:
                if c["id"] == clip_id:
                    return c
            return None

    def add_or_update_clip(self, clip_data: dict) -> dict:
        with self._lock:
            clip_id = clip_data.get("id")
            file_path = clip_data.get("file_path", "")
            norm_fp = os.path.normcase(os.path.abspath(file_path)) if file_path else ""

            existing = None
            for c in self.state["clips"]:
                if clip_id and c["id"] == clip_id:
                    existing = c
                    break
                if not clip_id and norm_fp and c.get("file_path"):
                    c_fp = os.path.normcase(os.path.abspath(c["file_path"]))
                    if c_fp == norm_fp:
                        existing = c
                        break

            if "speaker_id" in clip_data:
                spk_id = clip_data.get("speaker_id") or ""
            else:
                spk_id = existing.get("speaker_id", "") if existing else ""

            if "speaker_ids" in clip_data:
                spk_ids = [s for s in (clip_data.get("speaker_ids") or []) if s]
            elif "speaker_id" in clip_data:
                spk_ids = [spk_id] if spk_id else []
            else:
                spk_ids = list((existing.get("speaker_ids") if existing else []) or [])

            if spk_id and spk_id not in spk_ids:
                spk_ids.insert(0, spk_id)
            if not spk_id and spk_ids:
                spk_id = spk_ids[0]

            for sid in spk_ids:
                if sid:
                    self.ensure_speaker(sid)

            if existing is not None:
                previous = copy.deepcopy(existing)
                for key in ('title', 'transcript', 'speaker_id', 'speaker_ids', 'segments', 'acoustic_mood', 'category', 'emoji', 'color', 'tags'):
                    if key in clip_data and clip_data[key] is not None and clip_data[key] != existing.get(key):
                        existing.setdefault('field_versions', {})[key] = existing.get('field_versions', {}).get(key, 0) + 1
                        existing.setdefault('field_provenance', {})[key] = 'human'
                for k, v in clip_data.items():
                    if v is not None:
                        existing[k] = v
                existing["speaker_id"] = spk_id or ""
                existing["speaker_ids"] = spk_ids
                try:
                    self.save()
                except Exception:
                    existing.clear()
                    existing.update(previous)
                    raise
                return existing

            new_clip = {
                "id": clip_id or uuid.uuid4().hex[:10],
                "title": clip_data.get("title") or "Áudio Sem Nome",
                "file_path": file_path,
                "category": clip_data.get("category") or "Call",
                "speaker_id": spk_id or "",
                "speaker_ids": spk_ids,
                "segments": clip_data.get("segments") or [],
                "transcript": clip_data.get("transcript") or "",
                "acoustic_mood": clip_data.get("acoustic_mood") or "Normal",
                "emoji": clip_data.get("emoji") or "🔊",
                "color": clip_data.get("color") or "#3b82f6",
                "tags": clip_data.get("tags") or [],
                "hotkey": clip_data.get("hotkey") or "",
                "volume": float(clip_data.get("volume", 1.0)),
                "trim_start": float(clip_data.get("trim_start", 0.0)),
                "trim_end": float(clip_data.get("trim_end", 0.0)),
                "duration": float(clip_data.get("duration", 0.0)),
                "favorite": bool(clip_data.get("favorite", False)),
                "pending": bool(clip_data.get("pending", False)),
                "play_count": int(clip_data.get("play_count", 0)),
                "lufs": clip_data.get("lufs"),
                "loudness_gain_db": float(clip_data.get("loudness_gain_db", 0.0) or 0.0),
                "lufs_estimated": bool(clip_data.get("lufs_estimated", False)),
                "created_at": clip_data.get("created_at") or time.time(),
            }
            for key in ('source_id', 'revision_id', 'field_provenance', 'pack_identity', 'montage_identity', 'capture_analysis'):
                if key in clip_data:
                    new_clip[key] = copy.deepcopy(clip_data[key])
            self.state["clips"].insert(0, new_clip)
            try:
                self.save()
            except Exception:
                # O mesmo lock protege inserção, persistência e rollback específico.
                # Nenhum snapshot global pode descartar alterações de outros clipes.
                self.state["clips"][:] = [clip for clip in self.state["clips"] if clip is not new_clip]
                raise
            return new_clip

    def delete_clip(self, clip_id: str, delete_file: bool = False) -> bool:
        with self._lock:
            affected = self.sequences_using_clip(clip_id) + self.collections_using('clip', clip_id)
            if affected:
                raise ValueError('Clipe usado em sequências ou coleções: ' + ', '.join(affected))
            for idx, c in enumerate(self.state["clips"]):
                if c["id"] == clip_id:
                    removed = self.state["clips"].pop(idx)
                    try:
                        self.save()
                    except Exception:
                        self.state['clips'].insert(idx, removed)
                        raise
                    if delete_file and removed.get("file_path") and not removed.get('source_id'):
                        try:
                            fp = Path(removed["file_path"]).resolve()
                            shared = any(Path(other.get('file_path', '')).resolve() == fp for other in self.state['clips'])
                            if not shared and fp.exists() and self.clips_dir in fp.parents:
                                fp.unlink(missing_ok=True)
                        except Exception:
                            pass
                    return True
            return False

    def delete_file_inside(self, root: Path, raw_path: str) -> bool:
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

    def _sweep_temp_files(self):
        """Removes leftovers from a crash: tmp drafts and half-imported .part files."""
        try:
            for entry in self.tmp_dir.iterdir():
                if not entry.is_file():
                    continue
                try:
                    entry.unlink()
                except OSError:
                    continue
        except OSError:
            pass
        try:
            for entry in self.clips_dir.glob("*.part"):
                if not entry.is_file():
                    continue
                try:
                    entry.unlink()
                except OSError:
                    continue
        except OSError:
            pass

    def purge_pending_clips(self) -> int:
        """Drops replay drafts (they never survive a restart) and sweeps temp leftovers."""
        with self._lock:
            pending = [c for c in self.state["clips"] if c.get("pending") and c['id'] not in self.state['edit_drafts']]
            if pending:
                pending_ids = {c['id'] for c in pending}
                self.state["clips"] = [c for c in self.state["clips"] if c['id'] not in pending_ids]
                for clip in pending:
                    self.delete_file_inside(self.tmp_dir, clip.get("file_path"))
                self.save()
            self._sweep_temp_files()
            return len(pending)
    def increment_play_count(self, clip_id: str) -> dict | None:
        with self._lock:
            c = self.get_clip(clip_id)
            if c:
                c["play_count"] = int(c.get("play_count", 0)) + 1
                self.mark_dirty(delay=0.5)
            return c

    def begin_edit(self, clip_id: str) -> dict:
        from clip_renderer import preserve_source, render_revision
        import numpy as np
        with self._lock:
            if clip_id in self.state['edit_drafts']:
                return copy.deepcopy(self.state['edit_drafts'][clip_id])
            clip = copy.deepcopy(self.get_clip(clip_id))
            if not clip:
                raise ValueError('Clipe não encontrado')
            source = copy.deepcopy(self.state['sources'].get(clip.get('source_id')))
            revision = copy.deepcopy(self.state['revisions'].get(clip.get('revision_id')))
            normalize = bool(self.state['settings'].get('normalize_on_import', True))
        # Decodificação e promoção fora do lock; o arquivo original permanece disponível.
        if source is None:
            source = preserve_source(clip['file_path'], self.sources_dir)
        if not source.get('available'):
            raise ValueError('Fonte de áudio indisponível')
        start = round(float(clip.get('trim_start', 0)) * 48000)
        end = round(float(clip.get('trim_end') or source['duration']) * 48000)
        legacy_bounds = None
        if revision is None:
            # Somente a entrada legada usava duração arredondada em segundos.
            # Ajusta essa migração uma vez; spans/trim explícitos E2 seguem estritos.
            start = max(0, min(source['total_samples'] - 1, start))
            end = min(source['total_samples'], end)
            if end <= start:
                end = source['total_samples']
            legacy_bounds = {'trim_start': start / 48000, 'trim_end': end / 48000,
                             'duration': source['duration']}
            revision = {'source_id': source['id'], 'source_hash': source['sha256'],
                        'spans': [[start, end]], 'gain_db': float(clip.get('loudness_gain_db', 0)),
                        'normalization_gain': 1.0, 'fade_samples': 48}
            if normalize:
                probe = render_revision(source, {**revision, 'gain_db': 0})
                peak = float(np.max(np.abs(probe)))
                revision['normalization_gain'] = .92 / peak if peak > 1e-4 else 1.0
        draft = {'id': uuid.uuid4().hex, 'clip_id': clip_id, 'source_id': source['id'],
                 'base_revision_id': clip.get('revision_id'), 'revision': revision,
                 'fields': {}, 'generation': 0, 'field_versions': copy.deepcopy(clip.get('field_versions', {})),
                 'field_provenance': copy.deepcopy(clip.get('field_provenance', {})), 'created_at': time.time()}
        with self._lock:
            if clip_id in self.state['edit_drafts']:
                return copy.deepcopy(self.state['edit_drafts'][clip_id])
            current = self.get_clip(clip_id)
            if current != clip:
                raise ValueError('O clipe mudou durante a preparação; tente novamente')
            previous = copy.deepcopy(current)
            old_source = self.state['sources'].get(source['id'])
            self.state['sources'][source['id']] = source
            self.state['edit_drafts'][clip_id] = draft
            current['source_id'] = source['id']
            current['file_path'] = source['file_path']
            if legacy_bounds is not None:
                current.update(legacy_bounds)
            try:
                self.save()
            except Exception:
                current.clear(); current.update(previous)
                self.state['edit_drafts'].pop(clip_id, None)
                if old_source is None:
                    self.state['sources'].pop(source['id'], None)
                raise
            return copy.deepcopy(draft)

    def add_imported_clip(self, clip_data: dict, rendered: bool = False) -> dict:
        if not rendered:
            return self.add_or_update_clip(clip_data)
        from clip_renderer import preserve_source, RENDER_VERSION
        source = preserve_source(clip_data['file_path'], self.sources_dir)
        revision = {'id': uuid.uuid4().hex, 'source_id': source['id'], 'source_hash': source['sha256'],
                    'conversion_version': source['conversion_version'], 'render_version': RENDER_VERSION,
                    'spans': [[0, source['total_samples']]], 'gain_db': 0.0, 'normalization_gain': 1.0,
                    'fade_samples': 0, 'duration': source['duration'], 'created_at': time.time()}
        with self._lock:
            had_source = source['id'] in self.state['sources']
            self.state['sources'].setdefault(source['id'], source)
            self.state['revisions'][revision['id']] = revision
            try:
                return self.add_or_update_clip({**clip_data, 'source_id': source['id'], 'revision_id': revision['id'],
                                               'file_path': source['file_path'], 'trim_start': 0,
                                               'trim_end': source['duration'], 'duration': source['duration']})
            except Exception:
                self.state['revisions'].pop(revision['id'], None)
                if not had_source:
                    self.state['sources'].pop(source['id'], None)
                raise

    def update_edit(self, clip_id: str, fields: dict) -> dict:
        from clip_edits import validate_spans
        self.begin_edit(clip_id)
        with self._lock:
            previous = self.state['edit_drafts'][clip_id]
            draft = copy.deepcopy(previous)
            previous_fields = {**self.get_clip(clip_id), **draft['fields']}
            # Identidade, fonte, hash e normalização congelada são controlados pelo servidor.
            allowed = ('title', 'emoji', 'color', 'category', 'speaker_id', 'speaker_ids',
                       'hotkey', 'transcript', 'trim_start', 'trim_end', 'volume', 'acoustic_mood')
            draft['fields'].update({k: copy.deepcopy(v) for k, v in fields.items() if k in allowed})
            for key in allowed:
                if key in fields and fields[key] != previous_fields.get(key):
                    draft.setdefault('field_versions', {})[key] = draft.get('field_versions', {}).get(key, 0) + 1
                    draft.setdefault('field_provenance', {})[key] = 'human'
                    if key == 'transcript':
                        from transcript_model import legacy
                        draft['fields']['transcript_analysis'] = legacy(fields[key])
            source = self.state['sources'][draft['source_id']]
            # O editor legado envia ambos os controles até em autosave de metadados.
            # Somente uma alteração real na régua pode substituir uma montagem.
            merged = {**previous_fields, **draft['fields']}
            old_bounds = (round(float(previous_fields.get('trim_start', 0)) * 48000),
                          round(float(previous_fields.get('trim_end') or source['duration']) * 48000))
            new_bounds = (round(float(merged.get('trim_start', 0)) * 48000),
                          round(float(merged.get('trim_end') or source['duration']) * 48000))
            if 'spans' in fields:
                spans = fields['spans']
            elif ('trim_start' in fields or 'trim_end' in fields) and new_bounds != old_bounds:
                spans = [list(new_bounds)]
            else:
                spans = draft['revision']['spans']
            draft['revision']['spans'] = [list(pair) for pair in validate_spans(spans, source['total_samples'])]
            temporal = draft['fields'].get('transcript_analysis') or previous_fields.get('transcript_analysis')
            if temporal and temporal.get('alignment') != 'unaligned':
                from transcript_model import project
                draft['fields']['transcript'] = project(temporal, draft['revision']['spans'])['text']
            if draft != previous:
                draft['generation'] = previous.get('generation', 0) + 1
            self.state['edit_drafts'][clip_id] = draft
            try:
                self.save()
            except Exception:
                self.state['edit_drafts'][clip_id] = previous
                raise
            return copy.deepcopy(draft)

    def commit_edit(self, clip_id: str) -> dict:
        from clip_renderer import render_revision, RENDER_VERSION
        draft = self.begin_edit(clip_id)
        with self._lock:
            source = copy.deepcopy(self.state['sources'][draft['source_id']])
        revision = copy.deepcopy(draft['revision'])
        rendered = render_revision(source, revision, getattr(self, 'ffmpeg_path', None))
        from loudness import analyze_audio_loudness
        context = self.render_context(clip_id, draft=True)
        measured = analyze_audio_loudness(rendered * float(context.get('volume', 1)), sr=48000)
        revision.update({'id': uuid.uuid4().hex, 'duration': len(rendered) / 48000,
                         'conversion_version': source['conversion_version'], 'render_version': RENDER_VERSION,
                         'created_at': time.time()})
        with self._lock:
            if self.state['edit_drafts'].get(clip_id) != draft:
                raise ValueError('A edição mudou durante o salvamento; tente novamente')
            current = self.get_clip(clip_id)
            if not current:
                raise ValueError('Clipe não encontrado')
            previous = copy.deepcopy(current)
            current.update(draft['fields'])
            current['lufs'] = measured['lufs']
            current['lufs_estimated'] = measured['lufs_estimated']
            current['effective_duration'] = len(rendered) / 48000
            speaker_id = current.get('speaker_id') or ''
            if 'speaker_id' in draft['fields'] and 'speaker_ids' not in draft['fields']:
                speaker_ids = [speaker_id] if speaker_id else []
            else:
                speaker_ids = list(dict.fromkeys(s for s in (current.get('speaker_ids') or []) if s))
            if 'speaker_ids' in draft['fields'] and 'speaker_id' not in draft['fields'] and speaker_id not in speaker_ids:
                speaker_id = speaker_ids[0] if speaker_ids else ''
            if speaker_id and speaker_id not in speaker_ids:
                speaker_ids.insert(0, speaker_id)
            if not speaker_id and speaker_ids:
                speaker_id = speaker_ids[0]
            current['speaker_id'], current['speaker_ids'] = speaker_id, speaker_ids
            created_speakers = [sid for sid in speaker_ids if sid not in self.state['speakers']]
            for sid in speaker_ids:
                self.ensure_speaker(sid)
            current.pop('montage_identity', None)
            current.update({'source_id': source['id'], 'revision_id': revision['id'],
                            'file_path': source['file_path'], 'duration': source['duration'], 'pending': False})
            provenance = current.setdefault('field_provenance', {'transcript': 'unknown', 'speaker_id': 'unknown'})
            provenance.update(draft.get('field_provenance', {}))
            current['field_versions'] = copy.deepcopy(draft.get('field_versions', {}))
            for key in draft['fields']:
                if draft['fields'][key] != previous.get(key) and key not in draft.get('field_provenance', {}):
                    provenance[key] = 'human'
            self.state['revisions'][revision['id']] = revision
            self.state['edit_drafts'].pop(clip_id)
            try:
                self.save()
            except Exception:
                current.clear(); current.update(previous)
                self.state['revisions'].pop(revision['id'], None)
                self.state['edit_drafts'][clip_id] = draft
                for sid in created_speakers:
                    self.state['speakers'].pop(sid, None)
                raise
            return copy.deepcopy(current)

    def discard_edit(self, clip_id: str) -> bool:
        with self._lock:
            draft = self.state['edit_drafts'].pop(clip_id, None)
            pending = self.get_clip(clip_id)
            pending_index = None
            if draft and pending and pending.get('pending'):
                pending_index = self.state['clips'].index(pending)
                self.state['clips'].pop(pending_index)
            try:
                self.save()
            except Exception:
                if draft:
                    self.state['edit_drafts'][clip_id] = draft
                if pending_index is not None:
                    self.state['clips'].insert(pending_index, pending)
                raise
            return draft is not None

    def render_context(self, clip_id: str, draft: bool = False) -> dict:
        with self._lock:
            clip = copy.deepcopy(self.get_clip(clip_id))
            edit = self.state['edit_drafts'].get(clip_id) if draft else None
            revision = edit['revision'] if edit else self.state['revisions'].get((clip or {}).get('revision_id'))
            if revision:
                clip['_revision'] = copy.deepcopy(revision)
                clip['_source'] = copy.deepcopy(self.state['sources'][revision['source_id']])
                clip['_ffmpeg_path'] = getattr(self, 'ffmpeg_path', None)
                if edit:
                    clip.update(copy.deepcopy(edit['fields']))
            return clip

    def acquire_source(self, source_id: str) -> str:
        with self._lock:
            if source_id not in self.state['sources']:
                raise ValueError('Fonte não encontrada')
            handle = uuid.uuid4().hex
            self._active_sources[handle] = source_id
            return handle

    def prepare_analysis(self, clip_id, stage, interval=None):
        from clip_edits import validate_spans
        if stage not in ('transcription', 'speakers', 'acoustics', 'metadata'):
            raise ValueError('Etapa de análise inválida')
        self.begin_edit(clip_id)
        with self._lock:
            clip = self.get_clip(clip_id)
            draft = self.state['edit_drafts'][clip_id]
            source = self.state['sources'][draft['source_id']]
            selected = interval is not None
            interval = list(interval) if selected else [0, source['total_samples']]
            validate_spans([interval], source['total_samples'])
            if selected:
                transcript = self.get_transcript(clip_id)
                if transcript.get('alignment') == 'unaligned' and transcript.get('text'):
                    raise ValueError('Texto não alinhado: transcreva integralmente antes de substituir um intervalo')
            had_generations = 'analysis_generations' in clip
            generations = clip.setdefault('analysis_generations', {})
            had_stage = stage in generations
            previous_generation = generations.get(stage)
            generations[stage] = generations.get(stage, 0) + 1
            token = {'analysis_id': uuid.uuid4().hex, 'clip_id': clip_id, 'stage': stage,
                     'generation': generations[stage], 'draft_id': draft['id'],
                     'draft_generation': draft.get('generation', 0), 'source_id': draft['source_id'],
                     'revision_id': clip.get('revision_id'), 'interval': interval, 'selected': selected,
                     'field_versions': copy.deepcopy(draft.get('field_versions', {})),
                     'published_versions': copy.deepcopy(clip.get('field_versions', {})),
                     'published_provenance': copy.deepcopy(clip.get('field_provenance', {})),
                     'field_provenance': copy.deepcopy(draft.get('field_provenance', {}))}
            try:
                self.save()
            except Exception:
                # Só a geração deste pedido foi modificada nesta transação.
                if had_stage:
                    generations[stage] = previous_generation
                else:
                    generations.pop(stage, None)
                if not had_generations and not generations:
                    clip.pop('analysis_generations', None)
                raise
            return token

    def _analysis_eligible(self, token):
        clip = self.get_clip(token['clip_id'])
        draft = self.state['edit_drafts'].get(token['clip_id'])
        return bool(clip and draft and clip.get('analysis_generations', {}).get(token['stage']) == token['generation']
                    and draft['id'] == token['draft_id'] and draft.get('generation', 0) == token['draft_generation']
                    and draft['source_id'] == token['source_id'] and clip.get('revision_id') == token['revision_id']
                    and clip.get('source_id') == token['source_id']
                    and draft.get('field_versions', {}) == token['field_versions']
                    and clip.get('field_versions', {}) == token['published_versions']
                    and clip.get('field_provenance', {}) == token['published_provenance']
                    and draft.get('field_provenance', {}) == token['field_provenance'])

    @_atomic_analysis
    def invalidate_analysis(self, token):
        with self._lock:
            clip = self.get_clip(token['clip_id'])
            if clip and clip.get('analysis_generations', {}).get(token['stage']) == token['generation']:
                clip['analysis_generations'][token['stage']] += 1
                self.save()

    def get_transcript(self, clip_id):
        from transcript_model import legacy
        from audio_variants import project
        with self._lock:
            context = self.render_context(clip_id, draft=True)
            if not context:
                raise ValueError('Clipe não encontrado')
            result = context.get('transcript_analysis') or legacy(context.get('transcript', ''))
            spans = context.get('_revision', {}).get('spans', [])
            return {**copy.deepcopy(result), **project(result, spans, context.get('_revision', {}).get('variant', {}))}

    @_atomic_analysis
    def apply_analysis(self, token, result, accept=False):
        from transcript_model import legacy, project, replace_interval
        with self._lock:
            clip = self.get_clip(token['clip_id'])
            if not clip:
                return {'state': 'obsolete'}
            proposals = clip.setdefault('analysis_proposals', {})
            if not self._analysis_eligible(token):
                state = {'state': 'obsolete'}
            else:
                draft = self.state['edit_drafts'][token['clip_id']]
                allowed = {'metadata': ('title', 'category', 'emoji', 'color', 'tags'),
                           'speakers': ('speaker_id', 'speaker_ids', 'segments'),
                           'acoustics': ('acoustic_mood',), 'transcription': ('transcript', 'transcript_analysis')}
                fields = {k: copy.deepcopy(v) for k, v in result.get('fields', {}).items() if k in allowed[token['stage']]}
                if token['stage'] == 'transcription':
                    current = self.render_context(token['clip_id'], draft=True)
                    previous = current.get('transcript_analysis') or legacy(current.get('transcript', ''))
                    # Uma seleção sempre produz proposta: o operador confirma seu alvo.
                    needs_consent = token.get('selected') or previous.get('corrections') or (
                        previous.get('alignment') == 'unaligned' and previous.get('text'))
                    if needs_consent and not accept:
                        fields = {'transcript_analysis': copy.deepcopy(result), 'transcript': result.get('text', '')}
                    else:
                        merged = replace_interval(previous, result, token['interval'], accept_corrections=accept)
                        fields = {'transcript_analysis': merged, 'transcript': project(merged, draft['revision']['spans'])['text']}
                protected = any(draft.get('field_provenance', {}).get(k) == 'human' for k in fields)
                if not accept and (protected or token.get('selected') or (token['stage'] == 'transcription' and needs_consent)):
                    state = {'state': 'proposal', 'accept_token': uuid.uuid4().hex}
                else:
                    for key, value in fields.items():
                        draft['fields'][key] = value
                        draft.setdefault('field_versions', {})[key] = draft.get('field_versions', {}).get(key, 0) + 1
                        draft.setdefault('field_provenance', {})[key] = 'human' if accept else 'analysis:' + token['analysis_id']
                    draft['generation'] = draft.get('generation', 0) + 1
                    state = {'state': 'applied'}
            proposals[token['analysis_id']] = {'token': copy.deepcopy(token), 'result': copy.deepcopy(result), **state}
            while len(proposals) > 16:
                del proposals[next(iter(proposals))]
            self.save()
            return copy.deepcopy(state)

    def accept_analysis(self, clip_id, analysis_id, accept_token):
        with self._lock:
            clip = self.get_clip(clip_id)
            proposal = (clip or {}).get('analysis_proposals', {}).get(analysis_id)
            if not proposal or proposal.get('state') != 'proposal' or proposal.get('accept_token') != accept_token:
                return {'state': 'obsolete'}
            return self.apply_analysis(proposal['token'], proposal['result'], accept=True)

    @_atomic_analysis
    def correct_transcript(self, clip_id, word_ids, text):
        from transcript_model import correct, project
        with self._lock:
            draft = self.state['edit_drafts'].get(clip_id)
            context = self.render_context(clip_id, draft=True)
            if not draft or not context.get('transcript_analysis'):
                raise ValueError('Abra uma transcrição alinhada antes de corrigir palavras')
            updated = correct(context['transcript_analysis'], word_ids, text, spans=draft['revision']['spans'])
            draft['fields']['transcript_analysis'] = updated
            draft['fields']['transcript'] = project(updated, draft['revision']['spans'])['text']
            for key in ('transcript', 'transcript_analysis'):
                draft.setdefault('field_versions', {})[key] = draft.get('field_versions', {}).get(key, 0) + 1
                draft.setdefault('field_provenance', {})[key] = 'human'
            draft['generation'] = draft.get('generation', 0) + 1
            self.save()
            return self.get_transcript(clip_id)

    @_atomic_analysis
    def reopen_edit(self, clip_id):
        with self._lock:
            draft = self.state['edit_drafts'][clip_id]
            draft['generation'] = draft.get('generation', 0) + 1
            self.save()
            return copy.deepcopy(draft)

    def release_source(self, handle: str):
        with self._lock:
            self._active_sources.pop(handle, None)

    def set_source_reference(self, kind: str, owner_id: str, source_id: str | None):
        with self._lock:
            key = f'{kind}:{owner_id}'
            previous = self.state['source_references'].get(key)
            if source_id is not None:
                if source_id not in self.state['sources']:
                    raise ValueError('Fonte não encontrada')
                self.state['source_references'][key] = source_id
            else:
                self.state['source_references'].pop(key, None)
            try:
                self.save()
            except Exception:
                if previous is None:
                    self.state['source_references'].pop(key, None)
                else:
                    self.state['source_references'][key] = previous
                raise

    def source_references(self, source_id: str) -> list[str]:
        with self._lock:
            refs = [key for key, value in self.state['source_references'].items() if value == source_id]
            for collection in ('clips', 'revisions', 'edit_drafts'):
                values = self.state[collection]
                for item in values if isinstance(values, list) else values.values():
                    if item.get('source_id') == source_id:
                        refs.append(f"{collection}:{item['id']}")
            if source_id in self._active_sources.values():
                refs.append('active')
            return refs

    def collect_source(self, source_id: str) -> bool:
        with self._lock:
            if self.source_references(source_id):
                return False
            source = self.state['sources'].pop(source_id, None)
            if not source:
                return False
            try:
                self.save()
            except Exception:
                self.state['sources'][source_id] = source
                raise
            self.delete_file_inside(self.sources_dir, source.get('file_path'))
            return True

    def set_clip_volume(self, clip_id: str, volume: float) -> dict | None:
        """Persists a clip's volume under the store lock.

        Works even when legacy clips lack the "volume" key, and clamps to the
        engine's supported 0..3 gain range.
        """
        with self._lock:
            c = self.get_clip(clip_id)
            if c is None:
                return None
            try:
                vol = float(volume)
            except (TypeError, ValueError):
                vol = float(c.get("volume", 1.0))
            c["volume"] = round(max(0.0, min(3.0, vol)), 2)
            self.save_debounced()
            return c

    def search_clips(
        self,
        query: str = "",
        category: str = "Todos",
        speaker_id: str = "Todos",
        favorites_only: bool = False,
    ) -> list[dict]:
        with self._lock:
            q = (query or "").strip().lower()
            results = []
            for c in self.state["clips"]:
                if favorites_only and not c.get("favorite"):
                    continue
                if category and category != "Todos" and c.get("category") != category:
                    continue
                if speaker_id and speaker_id != "Todos":
                    c_spks = c.get("speaker_ids") or ([c["speaker_id"]] if c.get("speaker_id") else [])
                    if speaker_id == "__none__":
                        if c_spks or c.get("speaker_id"):
                            continue
                    elif speaker_id != c.get("speaker_id") and speaker_id not in c_spks:
                        continue
                if q:
                    c_spks = c.get("speaker_ids") or ([c["speaker_id"]] if c.get("speaker_id") else [])
                    spk_names = " ".join(self.state["speakers"].get(sid, {}).get("name", "") for sid in c_spks)
                    haystack = " ".join([
                        c.get("title", ""),
                        c.get("transcript", ""),
                        c.get("category", ""),
                        c.get("acoustic_mood", ""),
                        spk_names,
                        " ".join(c.get("tags", [])),
                        c.get("hotkey", ""),
                    ]).lower()
                    if q not in haystack:
                        continue
                results.append(c)
            return results

    def import_separados_manifest(self, separados_dir: str, analyzer_fn=None, progress_fn=None) -> list[dict]:
        sep_path = Path(separados_dir).resolve()
        manifest_path = sep_path / "mapa_de_falas.json"
        if not manifest_path.exists():
            return []

        try:
            items = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        imported = []
        total_items = len(items)
        for idx, entry in enumerate(items, 1):
            rel_file = entry.get("arquivo_trecho", "")
            if not rel_file:
                continue
            src_path = (sep_path / rel_file).resolve()
            if not src_path.exists() or not src_path.is_file():
                continue

            if callable(progress_fn):
                try:
                    progress_fn(idx, total_items, src_path.name)
                except Exception:
                    pass

            spk_num = entry.get("interlocutor", 1)
            spk_id = f"interlocutor_{spk_num}"
            spk_obj = self.ensure_speaker(spk_id)

            transcript = (entry.get("transcricao") or "").strip()
            dur = float(entry.get("duracao_s") or 0.0)

            dst_name = f"sep_spk{spk_num}_{src_path.name}"
            dst_path = self.clips_dir / dst_name
            if not dst_path.exists():
                shutil.copy2(src_path, dst_path)

            meta = {
                "title": transcript[:28] if transcript else src_path.stem,
                "category": "Call",
                "emoji": "🎙️",
                "color": spk_obj.get("color", "#3b82f6"),
                "acoustic_mood": "Normal",
                "tags": [],
            }
            if callable(analyzer_fn):
                try:
                    analyzed = analyzer_fn(
                        str(dst_path),
                        existing_transcript=transcript,
                        existing_speaker_id=spk_id,
                        speaker_name=spk_obj.get("name", ""),
                    )
                    if isinstance(analyzed, dict):
                        meta.update(analyzed)
                except Exception:
                    pass

            clip = self.add_or_update_clip({
                "title": meta.get("title") or "Trecho da Call",
                "file_path": str(dst_path),
                "category": meta.get("category") or "Call",
                "speaker_id": spk_id,
                "transcript": meta.get("transcript") or transcript,
                "acoustic_mood": meta.get("acoustic_mood") or "Normal",
                "emoji": meta.get("emoji") or "🎙️",
                "color": meta.get("color") or spk_obj.get("color", "#3b82f6"),
                "tags": meta.get("tags") or [],
                "duration": float(meta.get("duration") or dur),
            })
            imported.append(clip)

        return imported
