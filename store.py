import os
import copy
import json
import uuid
import shutil
import time
import threading
from pathlib import Path

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
    # "" = record whatever the Windows default output is playing.
    "replay_source_name": "",
    "stop_all_hotkey": "ctrl+shift+s",
    "replay_hotkey": "alt+f10",
    "overlay_hotkey": "ctrl+shift+o",
    "use_local_llm": True,
}


CURRENT_SCHEMA_VERSION = 1
SQLITE_TRIGGER_CLIPS = 2000
SQLITE_TRIGGER_SAVE_MS = 150.0
SQLITE_TRIGGER_JSON_MB = 10.0


class SoundboardStore:
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
        }
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

            schema_version = loaded.get("schema_version")
            if schema_version is None:
                self._migrate_v0_to_v1(raw_text, loaded)
            else:
                self._apply_loaded(loaded)

    def _migrate_v0_to_v1(self, raw_text: str, loaded: dict):
        bak_path = self.db_path.with_suffix(".json.bak")
        try:
            bak_path.write_text(raw_text, encoding="utf-8")
        except Exception:
            pass

        self._apply_loaded(loaded)
        self.state["schema_version"] = CURRENT_SCHEMA_VERSION
        try:
            self._perform_save()
        except Exception:
            self._dirty = True

    def _apply_loaded(self, loaded: dict):
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
                if norm_fp and c.get("file_path"):
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
                for k, v in clip_data.items():
                    if v is not None:
                        existing[k] = v
                existing["speaker_id"] = spk_id or ""
                existing["speaker_ids"] = spk_ids
                self.save()
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
            self.state["clips"].insert(0, new_clip)
            self.save()
            return new_clip

    def delete_clip(self, clip_id: str, delete_file: bool = False) -> bool:
        with self._lock:
            for idx, c in enumerate(self.state["clips"]):
                if c["id"] == clip_id:
                    removed = self.state["clips"].pop(idx)
                    if delete_file and removed.get("file_path"):
                        try:
                            fp = Path(removed["file_path"]).resolve()
                            if fp.exists() and self.clips_dir in fp.parents:
                                fp.unlink(missing_ok=True)
                        except Exception:
                            pass
                    self.save()
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
            pending = [c for c in self.state["clips"] if c.get("pending")]
            if pending:
                self.state["clips"] = [c for c in self.state["clips"] if not c.get("pending")]
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
