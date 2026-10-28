import os
import json
import uuid
import shutil
import time
import threading
from pathlib import Path

DEFAULT_CATEGORIES = [
    {"id": "Rage", "name": "Rage / Surto", "emoji": "🔥", "color": "#ef4444"},
    {"id": "Risada", "name": "Risada", "emoji": "😂", "color": "#f59e0b"},
    {"id": "Meme", "name": "Meme / Pérola", "emoji": "🤡", "color": "#8b5cf6"},
    {"id": "Call", "name": "Fala / Call", "emoji": "🎙️", "color": "#3b82f6"},
    {"id": "Efeito", "name": "Efeito Sonoro", "emoji": "🔊", "color": "#10b981"},
    {"id": "Clássico", "name": "Clássico", "emoji": "👑", "color": "#ec4899"},
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
    "overlap_audio": False,
    "auto_split_speakers": False,
    "normalize_on_import": True,
    "replay_seconds": 15,
    "stop_all_hotkey": "ctrl+shift+s",
    "replay_hotkey": "alt+f10",
    "overlay_hotkey": "ctrl+shift+o",
    "use_local_llm": True,
}


class SoundboardStore:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir).resolve()
        self.clips_dir = self.data_dir / "clips"
        self.models_dir = self.data_dir / "models"
        self.db_path = self.data_dir / "soundboard.json"
        self._lock = threading.RLock()

        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.state = {
            "settings": dict(DEFAULT_SETTINGS),
            "categories": list(DEFAULT_CATEGORIES),
            "speakers": {},
            "clips": [],
        }
        self._load()

    def _load(self):
        with self._lock:
            if self.db_path.exists():
                try:
                    loaded = json.loads(self.db_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        self.state["settings"] = {**DEFAULT_SETTINGS, **loaded.get("settings", {})}
                        self.state["categories"] = loaded.get("categories") or list(DEFAULT_CATEGORIES)
                        self.state["speakers"] = loaded.get("speakers") or {}
                        self.state["clips"] = loaded.get("clips") or []
                except Exception:
                    pass

    def save(self):
        with self._lock:
            tmp_path = self.db_path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, self.db_path)

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

            spk_id = clip_data.get("speaker_id") or (existing.get("speaker_id") if existing else "")
            spk_ids = list(clip_data.get("speaker_ids") or (existing.get("speaker_ids") if existing else []) or [])
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
                "play_count": int(clip_data.get("play_count", 0)),
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

    def increment_play_count(self, clip_id: str) -> dict | None:
        with self._lock:
            c = self.get_clip(clip_id)
            if c:
                c["play_count"] = int(c.get("play_count", 0)) + 1
                self.save()
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
                    if speaker_id != c.get("speaker_id") and speaker_id not in c_spks:
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

    def import_separados_manifest(self, separados_dir: str, analyzer_fn=None) -> list[dict]:
        sep_path = Path(separados_dir).resolve()
        manifest_path = sep_path / "mapa_de_falas.json"
        if not manifest_path.exists():
            return []

        try:
            items = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        imported = []
        for entry in items:
            rel_file = entry.get("arquivo_trecho", "")
            if not rel_file:
                continue
            src_path = (sep_path / rel_file).resolve()
            if not src_path.exists() or not src_path.is_file():
                continue

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
