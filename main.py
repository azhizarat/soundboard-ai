import os
import sys
import copy
import math
import time
import atexit
import argparse
import logging
import shutil
import threading
import uuid
import webbrowser
import zipfile
from pathlib import Path

import config
import doctor
import loudness
import packs
from store import SoundboardStore
from ai_analyzer import AIAnalyzer
from audio_engine import AudioEngine, compute_waveform_peaks
from hotkeys import HotkeyManager

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SUPPORTED_AUDIO_EXTS = packs.AUDIO_EXTS
# Formats that only work when FFmpeg can convert them to PCM.
CONVERT_ONLY_EXTS = {".m4a", ".webm", ".aac", ".wma", ".opus"}
# Fields a normal editor may change. Identity, audio location and analysis
# artifacts are server-owned: a client must not be able to publish a draft or
# repoint a clip at an arbitrary file.
EDITABLE_CLIP_FIELDS = (
    "title",
    "emoji",
    "color",
    "category",
    "speaker_id",
    "speaker_ids",
    "hotkey",
    "transcript",
    "trim_start",
    "trim_end",
    "volume",
    "acoustic_mood",
    "lufs",
    "loudness_gain_db",
    "lufs_estimated",
)


def _unlink_quiet(path) -> None:
    """Best-effort delete: temp cleanup must never raise."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _as_float(value, default: float, lo: float, hi: float) -> float:
    """Coerces a persisted setting to a clamped float, falling back on corrupt data."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return max(lo, min(hi, num))



def _device_name(entries: list[dict], dev_id) -> str:
    for entry in entries or []:
        if entry.get("id") == dev_id:
            return entry.get("name") or ""
    return ""


def _resolve_device(
    entries: list[dict],
    stored_id,
    stored_name,
    role_fallback_id=None,
) -> dict | None:
    """Resolves a saved device against live entries using the durable name.

    Device indices are unstable across reboots/USB changes, so a saved *name* is
    the only trusted identity. A legacy/empty name means the stored index is
    untrusted: ignore it and fall back to the device detected for that role.
    """
    wanted_name = (stored_name or "").strip().casefold()
    if not wanted_name:
        if role_fallback_id is None:
            return None
        for entry in entries or []:
            if entry.get("id") == role_fallback_id:
                return entry
        return None

    name_match = None
    for entry in entries or []:
        if str(entry.get("name", "")).strip().casefold() != wanted_name:
            continue
        if entry.get("id") == stored_id:
            return entry
        if name_match is None:
            name_match = entry
    return name_match


class SoundboardAPI:
    def __init__(self, data_dir: str = None, start_background_services: bool = True):
        # All internal state attributes MUST start with '_' so pywebview does not
        # recursively inspect/serialize them (especially self._window.native!).
        self._config = config.load_config(data_dir)
        self._store = SoundboardStore(data_dir=str(self._config.data_dir))
        # Replay drafts live in data/tmp and are throwaway: never survive a restart.
        self._store.purge_pending_clips()
        if start_background_services:
            atexit.register(self._store.flush)
        self._ai = AIAnalyzer(
            str(self._config.models_dir),
            ffmpeg_path=self._config.ffmpeg_path,
            ollama_url=self._config.ollama_url,
            ollama_model=self._config.ollama_model,
        )
        self._audio = AudioEngine(
            replay_seconds=max(60.0, float(self._store.state["settings"].get("replay_seconds", 15))),
            start_loopback=start_background_services,
            ffmpeg_path=self._config.ffmpeg_path,
            mixer_mode=self._config.mixer_mode,
        )
        self._window = None
        self._is_mini_overlay = False
        self._cable_warned = False

        self._auto_bind_devices()

        self._hotkeys = HotkeyManager(
            on_play_clip=lambda cid: self.play_clip(cid, monitor_only=False, from_hotkey=True),
            on_stop_all=lambda: self.stop_all(from_hotkey=True),
            on_save_replay=self.capture_replay_clip,
            on_toggle_overlay=self.toggle_mini_overlay,
        )
        if start_background_services:
            self._hotkeys.refresh(self._public_clips(self._store.state["clips"]), self._store.state["settings"])
            if self._store.state["settings"].get("mic_passthrough"):
                self._apply_mic_passthrough()
            # Pre-warm TitaNet and Faster-Whisper in background so first clip is instant
            threading.Thread(target=self._prewarm_ai, daemon=True).start()

    def _set_window(self, window):
        self._window = window

    def _prewarm_ai(self):
        try:
            self._ai._get_extractor()
            self._ai._get_diarizer()
            self._ai._get_whisper()
        except Exception:
            pass

    def _get_speaker_names_map(self) -> dict:
        return {k: v.get("name", k) for k, v in self._store.state["speakers"].items()}

    @staticmethod
    def _public_clips(clips: list[dict]) -> list[dict]:
        """Drafts are internal: the UI and hotkeys never see them."""
        return [c for c in clips if isinstance(c, dict) and not c.get("pending")]

    def _reject_pending(self, clip_id: str, action: str = "") -> dict | None:
        """Blocks destructive analysis on a replay draft (it is not saved yet)."""
        clip = self._store.get_clip(clip_id)
        if clip and clip.get("pending"):
            return {"ok": False, "error": "Rascunhos de replay não podem ser alterados assim."}
        return None

    def _auto_bind_devices(self):
        dev_info = self._audio.list_devices()
        settings = self._store.state["settings"]
        outputs = dev_info.get("outputs") or []
        inputs = dev_info.get("inputs") or []
        updates = {}

        def _bind(id_key, name_key, entries, fallback_id):
            stored_id = settings.get(id_key)
            stored_name = settings.get(name_key) or ""
            resolved = _resolve_device(entries, stored_id, stored_name, fallback_id)
            if resolved is not None:
                new_id = resolved.get("id")
                new_name = resolved.get("name") or ""
                if stored_id != new_id:
                    updates[id_key] = new_id
                if stored_name != new_name:
                    updates[name_key] = new_name
                return
            if stored_id is not None or stored_name.strip():
                # Configured device is gone: keep the saved name for a later re-resolve,
                # but never silently route to whatever index happens to exist now.
                if stored_id is not None:
                    updates[id_key] = None
                return
            # Unconfigured (legacy install with empty name): adopt the role default.
            if fallback_id is not None:
                updates[id_key] = fallback_id
                updates[name_key] = _device_name(entries, fallback_id)

        _bind(
            "cable_device_id",
            "cable_device_name",
            outputs,
            dev_info.get("detected_cable_id"),
        )
        _bind(
            "monitor_device_id",
            "monitor_device_name",
            outputs,
            dev_info.get("default_output_id"),
        )
        _bind(
            "mic_device_id",
            "mic_device_name",
            inputs,
            dev_info.get("default_input_id"),
        )

        if updates:
            self._store.update_settings(updates)

    def _apply_ducking(self):
        """Pushes ducking settings only — never touches the mic stream."""
        s = self._store.state["settings"]
        self._audio.set_ducking(
            bool(s.get("duck_enabled", False)),
            _as_float(s.get("duck_amount_db"), -12.0, -24.0, 0.0),
            _as_float(s.get("duck_threshold_dbfs"), -40.0, -60.0, -10.0),
        )

    def _apply_mic_passthrough(self):
        self._apply_ducking()
        s = self._store.state["settings"]
        enabled = bool(s.get("mic_passthrough", False))
        if enabled:
            # A missing cable is not fatal: silence the route and warn once.
            try:
                cable_ok = self._capabilities().get("cable_routing", True)
            except Exception:
                cable_ok = True
            if not cable_ok:
                enabled = False
                if not self._cable_warned:
                    self._cable_warned = True
                    self._notify_ui("toast", {
                        "type": "warning",
                        "message": "VB-Cable não encontrado: encaminhamento do microfone desativado.",
                    })
        self._audio.set_mic_passthrough(
            enabled=enabled,
            mic_id=s.get("mic_device_id"),
            cable_id=s.get("cable_device_id"),
            gain=_as_float(s.get("mic_gain", 1.0), 1.0, 0.0, 3.0),
        )

    def _notify_ui(self, event_name: str, payload: dict = None):
        if self._window is None:
            return
        try:
            import json
            js_payload = json.dumps(payload or {}, ensure_ascii=False)
            self._window.evaluate_js(
                f"window.onBackendEvent && window.onBackendEvent({json.dumps(event_name)}, {js_payload});"
            )
        except Exception:
            pass

    def _emit_progress(self, active: bool, current: int = 0, total: int = 1, label: str = "", detail: str = ""):
        pct = min(100, int(round((current / max(1, total)) * 100))) if active else 100
        self._notify_ui("progress", {
            "active": active,
            "current": current,
            "total": total,
            "percent": pct,
            "label": label,
            "detail": detail,
        })

    def get_initial_state(self) -> dict:
        devs = self._audio.list_devices()
        discovered_separados = []
        videos_root = self._config.separados_root
        if videos_root.exists():
            try:
                for sub in sorted(videos_root.iterdir()):
                    if sub.is_dir() and (sub / "separados" / "mapa_de_falas.json").exists():
                        discovered_separados.append({
                            "label": f"Videos/{sub.name}/separados",
                            "path": str((sub / "separados").resolve()),
                        })
            except Exception:
                pass

        snap = self._store.snapshot()
        env = doctor.snapshot(self._config)
        return {
            "settings": snap["settings"],
            "categories": snap["categories"],
            "speakers": snap["speakers"],
            "clips": self._public_clips(snap["clips"]),
            "devices": devs,
            "discovered_separados": discovered_separados,
            "models_dir": str(self._config.models_dir),
            "has_titanet": self._ai.emb_model_path.exists(),
            "has_ref_voices": self._ai.ref_voices_path.exists(),
            "is_mini_overlay": self._is_mini_overlay,
            "environment": env,
            "config": config.config_dict(self._config),
        }

    def get_environment_status(self, refresh=False) -> dict:
        return doctor.snapshot(self._config, refresh=bool(refresh))

    def refresh_environment(self) -> dict:
        self._cable_warned = False
        doctor.invalidate_cache()
        return doctor.snapshot(self._config, refresh=True)

    def save_app_config(self, updates: dict) -> dict:
        try:
            self._config = config.save_config(self._config, updates or {})
        except Exception as exc:
            return {"ok": False, "error": f"Não foi possível salvar a configuração: {exc}"}
        self._apply_config()
        doctor.invalidate_cache()
        environment = doctor.snapshot(self._config, refresh=True)
        self._notify_ui("environment_updated", environment)
        return {
            "ok": True,
            "config": config.config_dict(self._config),
            "environment": environment,
        }

    def _apply_config(self) -> None:
        """Rebuilds everything that depends on config paths without restarting."""
        self._cable_warned = False
        self._ai = AIAnalyzer(
            str(self._config.models_dir),
            ffmpeg_path=self._config.ffmpeg_path,
            ollama_url=self._config.ollama_url,
            ollama_model=self._config.ollama_model,
        )
        self._audio.ffmpeg_path = self._config.ffmpeg_path
        # Re-applying keeps the route in sync with the new devices/config; only
        # touch the mic stream when the user actually enabled it.
        if self._store.state["settings"].get("mic_passthrough"):
            self._apply_mic_passthrough()

    def pick_directory(self, kind: str) -> dict:
        if kind not in ("models_dir", "separados_root"):
            return {"ok": False, "error": "Pasta desconhecida."}
        if self._window is None:
            return {"ok": False, "error": "Janela indisponível."}
        try:
            import webview
            chosen = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            path = chosen if isinstance(chosen, str) else (chosen[0] if chosen else None)
        except Exception as exc:
            return {"ok": False, "error": f"Não foi possível abrir o seletor: {exc}"}
        if not path:
            return {"ok": False, "error": "Seleção cancelada."}
        return self.save_app_config({kind: path})

    def open_help_url(self, url: str) -> dict:
        safe = doctor.safe_help_url(url)
        if not safe:
            return {"ok": False, "error": "Link não permitido."}
        try:
            webbrowser.open(safe)
        except Exception as exc:
            return {"ok": False, "error": f"Não foi possível abrir o link: {exc}"}
        return {"ok": True}

    def _capabilities(self) -> dict:
        return doctor.capabilities(doctor.run_checks(self._config))

    def _capability_error(self, name: str) -> dict | None:
        if self._capabilities().get(name):
            return None
        messages = {
            "ai_analysis": "Análise de IA indisponível: coloque os modelos em data/models ou abra ⚙️ Ambiente.",
            "audio_convert": "Conversão de áudio indisponível: instale o FFmpeg para importar este formato.",
            "cable_routing": "VB-Cable não encontrado: sem rota para o cabo virtual.",
        }
        return {"ok": False, "error": messages.get(name, "Recurso indisponível.")}

    def refresh_devices(self) -> dict:
        self._auto_bind_devices()
        return self._audio.list_devices()

    def _apply_device_names(self, updates: dict):
        """Keeps the saved device names in sync when a device is selected by index."""
        keys = {
            "cable_device_id": ("cable_device_name", "outputs"),
            "monitor_device_id": ("monitor_device_name", "outputs"),
            "mic_device_id": ("mic_device_name", "inputs"),
        }
        pending = [k for k in keys if k in updates]
        if not pending:
            return
        dev_info = self._audio.list_devices()
        for id_key in pending:
            name_key, list_key = keys[id_key]
            dev_id = updates.get(id_key)
            if dev_id is None:
                # Explicit "no device": keep the saved name for a later re-resolve.
                continue
            matched = False
            for entry in dev_info.get(list_key) or []:
                if entry.get("id") == dev_id:
                    updates[name_key] = entry.get("name") or ""
                    matched = True
                    break
            if not matched:
                # Stale index: clear the old name so the next refresh cannot
                # silently rebind the saved name to a different device.
                updates[name_key] = ""

    def update_settings(self, updates: dict) -> dict:
        updates = dict(updates or {})
        if "replay_seconds" in updates:
            updates["replay_seconds"] = round(max(1.0, min(120.0, float(updates["replay_seconds"]))), 1)
        if any(k in updates for k in ("cable_device_id", "monitor_device_id", "mic_device_id")):
            self._apply_device_names(updates)
        s = self._store.update_settings(updates)
        if "replay_seconds" in updates:
            self._audio.replay_buffer.set_max_seconds(max(60.0, float(s.get("replay_seconds", 15))))
        if any(k in updates for k in ("mic_passthrough", "mic_device_id", "cable_device_id", "mic_gain")):
            self._apply_mic_passthrough()
        if any(k in updates for k in ("stop_all_hotkey", "replay_hotkey", "overlay_hotkey")):
            self._hotkeys.refresh(self._public_clips(self._store.snapshot()["clips"]), s)
        return s

    def set_ducking(self, payload=None, amount_db=None, threshold_dbfs=None) -> dict:
        """Updates ducking settings.

        Accepts the JS call style ``set_ducking({...})``, the positional style
        ``set_ducking(True, -6.0, -35.0)`` and keyword overrides.
        """
        if isinstance(payload, dict):
            data = dict(payload)
        else:
            data = {}
            if payload is not None:
                data["enabled"] = payload
        if amount_db is not None:
            data["amount_db"] = amount_db
        if threshold_dbfs is not None:
            data["threshold_dbfs"] = threshold_dbfs

        updates = {}
        if "enabled" in data:
            enabled = data.get("enabled")
            if isinstance(enabled, str):
                # JS/property payloads arrive as strings: "false" must disable.
                updates["duck_enabled"] = enabled.strip().lower() in ("1", "true", "yes", "on")
            else:
                updates["duck_enabled"] = bool(enabled)
        if data.get("amount_db") is not None:
            try:
                updates["duck_amount_db"] = max(-24.0, min(0.0, float(data["amount_db"])))
            except (TypeError, ValueError):
                return {"ok": False, "error": "Intensidade de ducking inválida."}
        if data.get("threshold_dbfs") is not None:
            try:
                updates["duck_threshold_dbfs"] = max(-60.0, min(-10.0, float(data["threshold_dbfs"])))
            except (TypeError, ValueError):
                return {"ok": False, "error": "Limiar de ducking inválido."}

        settings = self._store.snapshot()["settings"]
        if not updates:
            return {"ok": True, "settings": settings}
        self._store.update_settings(updates)
        # Ducking-only change: the mic stream must keep running.
        self._apply_ducking()
        return {"ok": True, "settings": self._store.snapshot()["settings"]}

    def play_clip(self, clip_id: str, monitor_only: bool = False, from_hotkey: bool = False) -> dict:
        clip = self._store.get_clip(clip_id)
        if not clip:
            return {"ok": False, "error": "Clip não encontrado"}

        file_path = clip.get("file_path") or ""
        if not file_path or not os.path.exists(file_path):
            error = "Arquivo de áudio não encontrado."
            if from_hotkey:
                self._notify_ui("toast", {"type": "warning", "message": error})
            return {"ok": False, "error": error}

        settings = self._store.state["settings"]
        eff_dur = max(
            0.2,
            float(clip.get("trim_end") or clip.get("duration") or 1.5) - float(clip.get("trim_start") or 0.0),
        )
        play_id = self._audio.play_clip(
            clip=clip,
            settings=settings,
            on_finish=None,
            on_tick=None,
            monitor_only=bool(monitor_only),
        )
        if not play_id:
            # No active/valid output route (muted cable+monitor, stale device, ...).
            error = "Nenhuma saída de áudio ativa (cabo/fone mudos ou dispositivo indisponível)."
            if from_hotkey:
                self._notify_ui("toast", {"type": "warning", "message": error})
            return {"ok": False, "error": error}
        if not monitor_only:
            self._store.increment_play_count(clip_id)
        sess = self._audio.get_session(play_id)
        initial_status = sess.status() if sess else {
            "playing": True,
            "clip_id": clip_id,
            "play_id": play_id,
            "title": clip.get("title") or "Áudio",
            "emoji": clip.get("emoji") or "🔊",
            "position": 0.0,
            "duration": eff_dur,
            "paused": False,
            "loop": False,
            "volume": float(clip.get("volume", 1.0)),
            "monitor_only": bool(monitor_only),
        }
        if from_hotkey:
            self._notify_ui("clip_started", initial_status)
        return {"ok": True, "play_id": play_id, "status": initial_status}

    def pause_resume_playback(self, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        paused = sess.toggle_pause()
        st = sess.status()
        return {"ok": True, "paused": paused, "status": st}

    def seek_playback(self, position_s: float, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        sess.seek(position_s)
        st = sess.status()
        return {"ok": True, "status": st}

    def set_playback_loop(self, loop: bool, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        loop_val = sess.set_loop(loop)
        st = sess.status()
        return {"ok": True, "loop": loop_val, "status": st}

    def set_live_clip_volume(self, volume: float, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        vol = sess.set_live_volume(volume)
        self._store.set_clip_volume(sess.clip_id, vol)
        st = sess.status()
        return {"ok": True, "volume": vol, "status": st}

    def restart_playback(self, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        sess.restart()
        st = sess.status()
        return {"ok": True, "status": st}

    def get_clip_waveform(self, clip_id: str, num_bins: int = 160) -> dict:
        clip = self._store.get_clip(clip_id)
        if not clip or not clip.get("file_path") or not os.path.exists(clip["file_path"]):
            return {"ok": False, "peaks": []}
        try:
            raw_audio, dur = self._audio.load_clip_stereo_48k(clip["file_path"])
            peaks = compute_waveform_peaks(raw_audio, num_bins=num_bins)
            return {"ok": True, "peaks": peaks, "duration": round(dur, 2)}
        except Exception:
            return {"ok": False, "peaks": []}

    def stop_all(self, from_hotkey: bool = False) -> dict:
        self._audio.stop_all()
        self._notify_ui("stopped_all", {})
        return {"ok": True}

    def _calculate_clip_loudness(
        self, file_path: str, trim_start: float = 0.0, trim_end: float = 0.0
    ) -> dict:
        try:
            samples, dur = self._audio.load_clip_stereo_48k(file_path)
            if samples is None or len(samples) == 0:
                return {"lufs": None, "loudness_gain_db": 0.0, "lufs_estimated": False}
            sr = int(self._audio.TARGET_SR)
            t_start = max(0.0, float(trim_start or 0.0))
            t_end = float(trim_end or 0.0)
            if t_end <= 0.0 or t_end > dur:
                t_end = dur
            if t_end > t_start:
                i_start = int(t_start * sr)
                i_end = int(t_end * sr)
                samples = samples[i_start:i_end]
            return loudness.analyze_audio_loudness(samples, sr=sr)
        except Exception:
            return {"lufs": None, "loudness_gain_db": 0.0, "lufs_estimated": False}

    def reanalyze_clip_loudness(self, clip_id: str) -> dict:
        clip = self._store.get_clip(clip_id)
        if not clip:
            return {"ok": False, "error": "Clip não encontrado."}
        file_path = clip.get("file_path", "")
        if not file_path or not os.path.exists(file_path):
            return {"ok": False, "error": "Arquivo de áudio não encontrado."}
        loud_info = self._calculate_clip_loudness(
            file_path,
            trim_start=float(clip.get("trim_start", 0.0)),
            trim_end=float(clip.get("trim_end", 0.0)),
        )
        updated = self._store.add_or_update_clip({"id": clip_id, **loud_info})
        snap = self._store.snapshot()
        self._notify_ui("clips_updated", {
            "clips": self._public_clips(snap["clips"]),
        })
        return {"ok": True, "clip": copy.deepcopy(updated)}

    def save_clip_edits(self, clip_data: dict) -> dict:
        data = dict(clip_data or {})
        # Whitelist: a draft must not be publishable (pending/file_path/duration/...).
        payload = {"id": data.get("id")}
        for key in EDITABLE_CLIP_FIELDS:
            if key in data:
                payload[key] = data[key]
        clip_id = payload.get("id")
        existing = self._store.get_clip(clip_id) if clip_id else None
        if existing and existing.get("file_path"):
            trim_start = float(payload.get("trim_start", existing.get("trim_start", 0.0)))
            trim_end = float(payload.get("trim_end", existing.get("trim_end", 0.0)))
            loud_info = self._calculate_clip_loudness(
                existing["file_path"], trim_start=trim_start, trim_end=trim_end
            )
            payload.update(loud_info)
        updated = self._store.add_or_update_clip(payload)
        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        return {"ok": True, "clip": copy.deepcopy(updated), "speakers": snap["speakers"]}

    def toggle_favorite(self, clip_id: str) -> dict:
        clip = self._store.get_clip(clip_id)
        if not clip:
            return {"ok": False}
        updated = self._store.add_or_update_clip({"id": clip_id, "favorite": not clip.get("favorite", False)})
        return {"ok": True, "clip": copy.deepcopy(updated)}

    def delete_clip(self, clip_id: str, delete_file: bool = False) -> dict:
        clip = self._store.get_clip(clip_id)
        file_path = (clip or {}).get("file_path") or ""
        ok = self._store.delete_clip(clip_id, delete_file=delete_file)
        if file_path:
            self._audio.invalidate_cache(file_path)
        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        return {
            "ok": bool(ok),
            "clip": copy.deepcopy(clip) if ok and clip else None,
            "can_undo": bool(ok and clip and not delete_file),
        }

    def restore_clip(self, clip: dict) -> dict:
        """Restores a soft-deleted clip (undo). The audio file must still exist."""
        payload = copy.deepcopy(clip or {})
        clip_id = str(payload.get("id") or "").strip()
        empty = self._store.snapshot()
        if not clip_id:
            return {"ok": False, "error": "Clipe inválido.", "clips": self._public_clips(empty["clips"]), "speakers": empty["speakers"]}
        if self._store.get_clip(clip_id):
            return {
                "ok": False,
                "error": "Este botão já existe.",
                "clips": self._public_clips(empty["clips"]),
                "speakers": empty["speakers"],
            }

        raw_path = str(payload.get("file_path") or "")
        clips_root = self._store.clips_dir.resolve()
        try:
            resolved = Path(raw_path).resolve() if raw_path else None
        except Exception:
            resolved = None
        # Undo must never resurrect a clip pointing at an arbitrary file, nor at
        # a directory (including clips_dir itself).
        if resolved is None or not resolved.is_relative_to(clips_root) or not resolved.is_file():
            return {
                "ok": False,
                "error": "Arquivo do áudio não encontrado.",
                "clips": self._public_clips(empty["clips"]),
                "speakers": empty["speakers"],
            }

        # A re-imported clip owns that file now: undoing would create two clips
        # sharing one file, so one deletion would silently break the other.
        for other in empty["clips"]:
            other_path = str(other.get("file_path") or "")
            if not other_path:
                continue
            try:
                if Path(other_path).resolve() == resolved:
                    return {
                        "ok": False,
                        "error": "Este áudio já pertence a outro botão.",
                        "clips": self._public_clips(empty["clips"]),
                        "speakers": empty["speakers"],
                    }
            except Exception:
                continue

        payload["file_path"] = str(resolved)
        saved = self._store.add_or_update_clip(payload)
        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        return {
            "ok": True,
            "clip": copy.deepcopy(saved),
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }

    def update_speaker(self, spk_id: str, name: str, color: str = None, avatar: str = None) -> dict:
        spk = self._store.update_speaker(spk_id, name=name, color=color, avatar=avatar)
        snap = self._store.snapshot()
        return {"ok": True, "speaker": copy.deepcopy(spk), "speakers": snap["speakers"]}

    def delete_speaker(self, spk_id: str) -> dict:
        ok = self._store.delete_speaker(spk_id)
        if self._ai.ref_voices_path.exists():
            try:
                import numpy as np
                old = np.load(self._ai.ref_voices_path)
                kept = {k: old[k] for k in old.files if k != spk_id}
                np.savez(self._ai.ref_voices_path, **kept)
            except Exception:
                pass
        snap = self._store.snapshot()
        return {
            "ok": ok,
            "speakers": snap["speakers"],
            "clips": self._public_clips(snap["clips"]),
        }

    def enroll_speaker_from_clip(self, clip_id: str, spk_id: str, speaker_name: str = "") -> dict:
        blocked = self._capability_error("ai_analysis")
        if blocked:
            return blocked
        rejected = self._reject_pending(clip_id, "cadastrar voz")
        if rejected:
            return rejected
        clip = self._store.get_clip(clip_id)
        if not clip or not clip.get("file_path"):
            return {"ok": False, "error": "Áudio inválido"}
        if speaker_name:
            self._store.update_speaker(spk_id, name=speaker_name)
        else:
            self._store.ensure_speaker(spk_id)
        ok = self._ai.enroll_speaker_reference(clip["file_path"], spk_id)
        updated = self._store.add_or_update_clip({"id": clip_id, "speaker_id": spk_id})
        snap = self._store.snapshot()
        return {
            "ok": ok,
            "speakers": snap["speakers"],
            "clip": copy.deepcopy(updated),
        }

    def reanalyze_clip_ai(self, clip_id: str) -> dict:
        blocked = self._capability_error("ai_analysis")
        if blocked:
            return blocked
        rejected = self._reject_pending(clip_id, "reanalisar")
        if rejected:
            return rejected
        clip = self._store.get_clip(clip_id)
        if not clip or not clip.get("file_path") or not os.path.exists(clip["file_path"]):
            return {"ok": False, "error": "Arquivo de áudio não encontrado"}

        self._emit_progress(True, 1, 2, "Re-analisando com IA Local...", clip.get("title", ""))
        try:
            spk_id = clip.get("speaker_id", "")
            spk_names = self._get_speaker_names_map()
            spk_name = spk_names.get(spk_id, "")
            res = self._ai.analyze_file(
                clip["file_path"],
                existing_transcript=None,
                existing_speaker_id=None,
                speaker_name=spk_name,
                speaker_names=spk_names,
                use_llm=bool(self._store.state["settings"].get("use_local_llm", True)),
            )
            detected_spk = res.get("speaker_id") or spk_id
            detected_spks = res.get("speaker_ids") or ([detected_spk] if detected_spk else [])
            for sid in detected_spks:
                self._store.ensure_speaker(sid)

            loud_info = self._calculate_clip_loudness(
                clip["file_path"],
                trim_start=float(clip.get("trim_start", 0.0)),
                trim_end=float(clip.get("trim_end", 0.0)),
            )
            updated = self._store.add_or_update_clip({
                "id": clip_id,
                "title": res["title"],
                "category": res["category"],
                "emoji": res["emoji"],
                "color": res["color"],
                "tags": res["tags"],
                "transcript": res["transcript"],
                "speaker_id": detected_spk,
                "speaker_ids": detected_spks,
                "segments": res.get("segments") or [],
                "acoustic_mood": res["acoustic_mood"],
                "duration": res["duration"],
                **loud_info,
            })
            snap = self._store.snapshot()
            return {"ok": True, "clip": copy.deepcopy(updated), "speakers": snap["speakers"]}
        finally:
            self._emit_progress(False)

    def split_clip_by_speakers(self, clip_id: str) -> dict:
        blocked = self._capability_error("ai_analysis")
        if blocked:
            return blocked
        import soundfile as sf
        from ai_analyzer import load_audio_mono_16k

        rejected = self._reject_pending(clip_id, "separar falas")
        if rejected:
            return rejected
        clip = self._store.get_clip(clip_id)
        if not clip or not clip.get("file_path") or not os.path.exists(clip["file_path"]):
            return {"ok": False, "error": "Arquivo de áudio não encontrado"}

        self._emit_progress(True, 1, 2, "Separando falas por voz...", clip.get("title", ""))
        try:
            spk_names = self._get_speaker_names_map()
            segments = clip.get("segments") or []
            if len(segments) <= 1:
                audio_16k, _ = load_audio_mono_16k(clip["file_path"])
                segments = self._ai.diarize_and_identify(audio_16k, speaker_names=spk_names)

            if len(segments) <= 1:
                return {"ok": False, "error": "Apenas 1 fala/interlocutor contínuo foi detectado neste áudio."}

            stereo_data, _ = self._audio.load_clip_stereo_48k(clip["file_path"])
            sr = self._audio.TARGET_SR
            created_clips = []
            base_stem = Path(clip["file_path"]).stem
            total_segs = len(segments)

            for idx, seg in enumerate(segments, 1):
                self._emit_progress(True, idx, total_segs, f"Cortando fala {idx}/{total_segs}...", seg.get("title", ""))
                st_s = float(seg.get("start", 0.0))
                en_s = float(seg.get("end", 0.0))
                if en_s - st_s < 0.25:
                    continue
                st_idx = max(0, int(round(st_s * sr)))
                en_idx = min(len(stereo_data), int(round(en_s * sr)))
                chunk = stereo_data[st_idx:en_idx]
                if len(chunk) == 0:
                    continue

                spk_id = seg.get("speaker_id") or "interlocutor_1"
                spk_obj = self._store.ensure_speaker(spk_id)
                out_name = f"{base_stem}_part{idx:02d}_{spk_id}.wav"
                out_path = str(self._store.clips_dir / out_name)
                sf.write(out_path, chunk, sr, subtype="PCM_16")

                new_c = self._store.add_or_update_clip({
                    "title": seg.get("title") or f"{spk_obj.get('name', spk_id)} #{idx}",
                    "file_path": out_path,
                    "category": seg.get("category") or clip.get("category") or "Call",
                    "speaker_id": spk_id,
                    "speaker_ids": [spk_id],
                    "segments": [seg],
                    "transcript": seg.get("transcript") or "",
                    "acoustic_mood": seg.get("mood") or "Normal",
                    "emoji": seg.get("emoji") or "🎙️",
                    "color": spk_obj.get("color") or seg.get("color") or "#3b82f6",
                    "tags": [spk_obj.get("name", spk_id).lower(), "separado"],
                    "duration": round(en_s - st_s, 2),
                    "created_at": time.time(),
                })
                created_clips.append(new_c)

            snap = self._store.snapshot()
            self._notify_ui("clips_updated", {
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            })
            return {
                "ok": True,
                "created_count": len(created_clips),
                "created_clips": copy.deepcopy(created_clips),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
        finally:
            self._emit_progress(False)

    def import_audio_files(
        self,
        file_paths: list[str] = None,
        skip_speaker_id: bool = False,
        forced_category: str = None,
    ) -> dict:
        if not file_paths and self._window is not None:
            try:
                import webview
                selected = self._window.create_file_dialog(
                    webview.OPEN_DIALOG,
                    allow_multiple=True,
                    file_types=("Arquivos de Áudio (*.mp3;*.wav;*.ogg;*.flac;*.m4a)", "Todos os arquivos (*.*)"),
                )
                file_paths = list(selected) if selected else []
            except Exception:
                file_paths = []

        if not file_paths:
            return {"ok": False, "imported": []}

        imported = []
        blocked_names = []
        caps = self._capabilities()
        use_llm = bool(self._store.state["settings"].get("use_local_llm", True))
        total_files = len(file_paths)
        mode_lbl = "Importando Vinhetas / Sons" if skip_speaker_id else "Importando e analisando áudios com IA"

        try:
            for idx, src in enumerate(file_paths, 1):
                src_path = Path(src)
                if not src_path.exists() or not src_path.is_file():
                    continue
                if src_path.suffix.lower() in CONVERT_ONLY_EXTS and not caps.get("audio_convert", True):
                    blocked_names.append(src_path.name)
                    continue
                self._emit_progress(True, idx, total_files, f"{mode_lbl} ({idx}/{total_files})", src_path.name)
                dst_name = f"{int(time.time() * 1000) % 1000000}_{src_path.name}"
                dst_path = self._store.clips_dir / dst_name
                shutil.copy2(src_path, dst_path)

                res = self._ai.analyze_file(
                    str(dst_path),
                    speaker_names=self._get_speaker_names_map(),
                    use_llm=use_llm,
                    skip_speaker_id=bool(skip_speaker_id),
                    forced_category=forced_category,
                )
                spk_id = "" if skip_speaker_id else res.get("speaker_id", "")
                spk_ids = [] if skip_speaker_id else (res.get("speaker_ids") or ([spk_id] if spk_id else []))
                for sid in spk_ids:
                    self._store.ensure_speaker(sid)

                loud_info = self._calculate_clip_loudness(str(dst_path))
                clip = self._store.add_or_update_clip({
                    "title": res.get("title") or src_path.stem[:28],
                    "file_path": str(dst_path),
                    "category": forced_category or res.get("category") or "Call",
                    "speaker_id": spk_id,
                    "speaker_ids": spk_ids,
                    "segments": [] if skip_speaker_id else (res.get("segments") or []),
                    "transcript": res.get("transcript") or "",
                    "acoustic_mood": res.get("acoustic_mood") or "Normal",
                    "emoji": res.get("emoji") or ("🎵" if forced_category == "Vinhetas" else "🔊"),
                    "color": res.get("color") or ("#06b6d4" if forced_category == "Vinhetas" else "#3b82f6"),
                    "tags": res.get("tags") or [],
                    "duration": res.get("duration") or 1.0,
                    "created_at": time.time(),
                    **loud_info,
                })
                imported.append(clip)
        finally:
            self._emit_progress(False)

        snap = self._store.snapshot()
        self._notify_ui("clips_updated", {
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        })
        if blocked_names and not imported:
            names = ", ".join(blocked_names[:5])
            return {
                "ok": False,
                "error": f"Instale o FFmpeg para importar este formato: {names}",
                "imported": [],
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
        result = {
            "ok": True,
            "imported": copy.deepcopy(imported),
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }
        if blocked_names:
            names = ", ".join(blocked_names[:5])
            result["warnings"] = [f"Instale o FFmpeg para importar este formato: {names}"]
        return result

    def import_folder_or_separados(
        self,
        folder_path: str = None,
        skip_speaker_id: bool = False,
        forced_category: str = None,
    ) -> dict:
        if not folder_path and self._window is not None:
            try:
                import webview
                selected = self._window.create_file_dialog(webview.FOLDER_DIALOG)
                if selected:
                    folder_path = selected[0] if isinstance(selected, (list, tuple)) else str(selected)
            except Exception:
                folder_path = None

        if not folder_path:
            return {"ok": False, "imported": []}

        target = Path(folder_path).resolve()
        if not target.exists():
            return {"ok": False, "error": "Pasta não encontrada"}

        if not skip_speaker_id and (target / "separados" / "mapa_de_falas.json").exists():
            target = target / "separados"

        use_llm = bool(self._store.state["settings"].get("use_local_llm", True))
        if not skip_speaker_id and (target / "mapa_de_falas.json").exists():
            try:
                def _fast_analyzer(fp, existing_transcript="", existing_speaker_id="", speaker_name=""):
                    return self._ai.analyze_file(
                        fp,
                        existing_transcript=existing_transcript,
                        existing_speaker_id=existing_speaker_id,
                        speaker_name=speaker_name,
                        use_llm=use_llm,
                    )

                def _on_prog(cur, tot, fname):
                    self._emit_progress(True, cur, tot, f"Importando pasta separados ({cur}/{tot})", fname)

                imported = self._store.import_separados_manifest(
                    str(target),
                    analyzer_fn=_fast_analyzer,
                    progress_fn=_on_prog,
                )
            finally:
                self._emit_progress(False)

            snap = self._store.snapshot()
            self._notify_ui("clips_updated", {
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            })
            return {
                "ok": True,
                "imported_count": len(imported),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }

        audio_files = [
            str(p) for p in sorted(target.rglob("*"))
            if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS and not p.name.endswith("_completo.mp3")
        ]
        res = self.import_audio_files(audio_files, skip_speaker_id=skip_speaker_id, forced_category=forced_category)
        res["imported_count"] = len(res.get("imported", []))
        return res

    def _capture_replay_analysis(self, out_dir: Path, last_seconds: float = None) -> dict:
        """Captures the replay buffer, runs AI and returns a clip payload (not persisted)."""
        secs = float(last_seconds or self._store.state["settings"].get("replay_seconds", 15))
        out_name = f"replay_{time.strftime('%Y%m%d_%H%M%S')}.wav"
        out_path = str(Path(out_dir) / out_name)

        self._emit_progress(True, 1, 3, f"Capturando últimos {int(secs)}s da call...", out_name)
        saved = self._audio.save_replay_wav(out_path, last_seconds=secs)
        if not saved:
            return {"ok": False, "error": "Nenhum áudio recente tocado no fone/sistema nos últimos 15s"}

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

    def capture_replay_clip(self, last_seconds: float = None) -> dict:
        """Alt+F10 path: saves the replay straight into the library."""
        try:
            captured = self._capture_replay_analysis(self._store.clips_dir, last_seconds)
            if not captured.get("ok"):
                self._notify_ui("toast", {"type": "warning", "message": captured["error"]})
                return captured
            clip = self._store.add_or_update_clip(captured["clip"])
            split_count = 0
            settings = self._store.state["settings"]
            if settings.get("auto_split_speakers") and len(clip.get("segments") or []) > 1:
                split_res = self.split_clip_by_speakers(clip["id"])
                if split_res.get("ok"):
                    split_count = int(split_res.get("created_count") or 0)
            snap = self._store.snapshot()
            self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
            self._notify_ui("replay_captured", {
                "clip": copy.deepcopy(clip),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            })
            result = {
                "ok": True,
                "clip": copy.deepcopy(clip),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
            if split_count:
                result["split_count"] = split_count
            return result
        finally:
            self._emit_progress(False)

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

        # The id comes from a client payload: never let it shape the path.
        safe_id = "".join(ch for ch in str(clip_id) if ch.isalnum() or ch in "-_") or uuid.uuid4().hex[:10]
        dst = self._store.clips_dir / f"replay_{safe_id}{src.suffix or '.wav'}"
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:
            return {**base, "error": f"Falha ao salvar o replay: {exc}"}

        try:
            saved = self._store.add_or_update_clip({
                "id": clip_id,
                "file_path": str(dst),
                "pending": False,
                "created_at": time.time(),
            })
            self._audio.invalidate_cache(str(src))

            split_count = 0
            settings = self._store.state["settings"]
            if settings.get("auto_split_speakers") and len(saved.get("segments") or []) > 1:
                split_res = self.split_clip_by_speakers(saved["id"])
                if split_res.get("ok"):
                    split_count = int(split_res.get("created_count") or 0)
        except Exception:
            # Atomic commit: put the audio back in tmp and keep the clip a draft.
            self._rollback_failed_commit(clip_id, src, dst)
            snap = self._store.snapshot()
            return {
                "ok": False,
                "error": "Falha ao salvar o replay. Tente de novo.",
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        return {
            "ok": True,
            "clip": copy.deepcopy(self._store.get_clip(clip_id) or saved),
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
            "split_count": split_count,
        }

    def _rollback_failed_commit(self, clip_id: str, src: Path, dst: Path) -> None:
        """Best effort: audio back to tmp, clip pending again. Never raises."""
        try:
            if dst.exists() and not src.exists():
                shutil.move(str(dst), str(src))
        except OSError:
            pass
        try:
            clip = self._store.get_clip(clip_id)
            if clip is not None and not clip.get("pending"):
                self._store.add_or_update_clip({
                    "id": clip_id,
                    "file_path": str(src),
                    "pending": True,
                })
        except Exception:
            pass

    def discard_replay_draft(self, clip_id: str) -> dict:
        clip = self._store.get_clip(clip_id)
        ok = bool(clip and clip.get("pending"))
        if ok:
            self._store.delete_clip(clip_id, delete_file=False)
            self._store.delete_file_inside(self._store.tmp_dir, clip.get("file_path"))
            self._audio.invalidate_cache(clip.get("file_path") or "")
        snap = self._store.snapshot()
        return {
            "ok": ok,
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }

    def _ensure_imported_speaker(self, name: str, color: str = "") -> str:
        """Maps a pack speaker to a local one by name, creating it when missing."""
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
            self._store.update_speaker(spk_id, name=clean, color=color)
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

        try:
            out = Path(path)
            if out.suffix.lower() != ".zip":
                out = out.with_name(out.name + ".zip")
            tmp = out.with_suffix(out.suffix + ".tmp")
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "Falha ao exportar: caminho inválido.",
                "count": 0,
                "skipped": 0,
            }
        try:
            res = packs.write_pack(tmp, clips, snap["speakers"])
            os.replace(tmp, out)
        except packs.PackError as exc:
            _unlink_quiet(tmp)
            return {"ok": False, "error": str(exc), "count": 0, "skipped": 0}
        except Exception as exc:
            _unlink_quiet(tmp)
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
            return {
                "ok": False,
                "error": "Importação cancelada.",
                "imported": [],
                "skipped": 0,
                "warnings": [],
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }

        try:
            pack = packs.validate_pack(path)
        except packs.PackError as exc:
            snap = self._store.snapshot()
            return {
                "ok": False,
                "error": str(exc),
                "imported": [],
                "skipped": 0,
                "warnings": [],
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }

        import soundfile as sf

        manifest = pack["manifest"]
        entries = manifest["clips"]
        speakers_meta = {
            str(s.get("name") or "").casefold(): s
            for s in (manifest.get("speakers") or [])
            if isinstance(s, dict)
        }
        existing = self._store.snapshot()
        used_hotkeys = {
            str(c.get("hotkey") or "").lower()
            for c in self._public_clips(existing["clips"])
            if c.get("hotkey")
        }
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
        remaining = packs.MAX_UNCOMPRESSED_BYTES
        copied_names: set[str] = set()
        try:
            with zipfile.ZipFile(path) as archive:
                for idx, entry in enumerate(entries, 1):
                    self._emit_progress(True, idx, total, f"Importando pacote ({idx}/{total})", entry.get("title") or "")
                    name = str(entry.get("file") or "")
                    label = entry.get("title") or name
                    if name in copied_names:
                        skipped += 1
                        warnings.append(f"{label}: áudio repetido no pacote.")
                        continue
                    ext = Path(name).suffix.lower()
                    if ext not in packs.AUDIO_EXTS:
                        skipped += 1
                        warnings.append(f"{label}: formato não suportado.")
                        continue

                    new_id = uuid.uuid4().hex[:10]
                    final = self._store.clips_dir / f"{new_id}{ext}"
                    while final.exists():
                        new_id = uuid.uuid4().hex[:10]
                        final = self._store.clips_dir / f"{new_id}{ext}"
                    part = self._store.clips_dir / f"{new_id}{ext}.part"
                    try:
                        try:
                            digest, size = packs.copy_entry(archive, name, part, remaining=remaining)
                        except packs.PackError as exc:
                            skipped += 1
                            warnings.append(f"{label}: {exc}")
                            continue
                        copied_names.add(name)
                        remaining -= size

                        if digest in known_hashes:
                            skipped += 1
                            warnings.append(f"{label}: já existe na biblioteca.")
                            continue

                        os.replace(part, final)
                        known_hashes[digest] = new_id

                        try:
                            duration = float(sf.info(str(final)).duration or 0.0)
                        except Exception:
                            duration = _as_float(entry.get("duration"), 0.0, 0.0, 24 * 3600.0)
                        duration = max(0.2, duration or _as_float(entry.get("duration"), 0.2, 0.2, 24 * 3600.0))

                        spk_ids = []
                        raw_spk_names = entry.get("speaker_names")
                        for spk_name in raw_spk_names if isinstance(raw_spk_names, list) else []:
                            meta = speakers_meta.get(str(spk_name).strip().casefold(), {})
                            sid = self._ensure_imported_speaker(str(spk_name), str(meta.get("color") or ""))
                            if sid and sid not in spk_ids:
                                spk_ids.append(sid)

                        hotkey = str(entry.get("hotkey") or "").strip()
                        if hotkey and hotkey.lower() in used_hotkeys:
                            hotkey = ""
                            warnings.append(f"{label}: atalho já em uso, importado sem atalho.")
                        if hotkey:
                            used_hotkeys.add(hotkey.lower())

                        # Keep a positive, audible window inside the real duration.
                        trim_start = _as_float(entry.get("trim_start"), 0.0, 0.0, duration)
                        trim_start = min(trim_start, max(0.0, duration - 0.2))
                        trim_end = _as_float(entry.get("trim_end"), 0.0, 0.0, duration)
                        if trim_end <= trim_start:
                            trim_end = duration
                        trim_end = min(duration, max(trim_end, trim_start + 0.05))
                        volume = _as_float(entry.get("volume"), 1.0, 0.0, 3.0)
                        raw_tags = entry.get("tags")

                        try:
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
                                "tags": [str(t) for t in (raw_tags if isinstance(raw_tags, list) else [])][:32],
                                "hotkey": hotkey,
                                "trim_start": trim_start,
                                "trim_end": trim_end,
                                "volume": volume,
                                "favorite": bool(entry.get("favorite")),
                                "duration": duration,
                                "created_at": time.time(),
                                "pending": False,
                            })
                        except Exception:
                            # Never leave a file the library does not reference.
                            _unlink_quiet(final)
                            raise
                        imported.append(clip)
                    finally:
                        # The part file is gone after os.replace; on any failure it is
                        # removed here so a bad pack never leaves orphans behind.
                        _unlink_quiet(part)
        except Exception as exc:
            snap = self._store.snapshot()
            return {
                "ok": False,
                "error": f"Falha ao importar: {exc}",
                "imported": copy.deepcopy(imported),
                "skipped": skipped,
                "warnings": warnings,
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
        finally:
            self._emit_progress(False)

        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        self._notify_ui("clips_updated", {
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        })
        return {
            "ok": True,
            "imported": copy.deepcopy(imported),
            "skipped": skipped,
            "warnings": warnings,
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }

    def toggle_mini_overlay(self) -> dict:
        self._is_mini_overlay = not self._is_mini_overlay
        if self._window is not None and sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.windll.user32
                user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
                user32.FindWindowW.restype = wintypes.HWND
                user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
                user32.ShowWindowAsync.restype = wintypes.BOOL
                user32.SetWindowPos.argtypes = [
                    wintypes.HWND,
                    wintypes.HWND,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_uint,
                ]
                user32.SetWindowPos.restype = wintypes.BOOL

                hwnd = user32.FindWindowW(None, "Soundboard AI — VB-Audio Cable Studio")
                if hwnd:
                    # Restore if maximized (SW_RESTORE = 9) asynchronously on the UI thread
                    user32.ShowWindowAsync(hwnd, 9)
                    dpi = user32.GetDpiForWindow(hwnd) if hasattr(user32, "GetDpiForWindow") else 96
                    scale = (dpi / 96.0) if dpi and dpi > 0 else 1.0
                    target_w = int((420 if self._is_mini_overlay else 1280) * scale)
                    target_h = int((600 if self._is_mini_overlay else 820) * scale)
                    hwnd_insert_after = wintypes.HWND(-1 if self._is_mini_overlay else -2)
                    # SWP_NOMOVE (0x0002) | SWP_NOACTIVATE (0x0010) | SWP_ASYNCWINDOWPOS (0x4000)
                    user32.SetWindowPos(
                        hwnd,
                        hwnd_insert_after,
                        0,
                        0,
                        target_w,
                        target_h,
                        0x0002 | 0x0010 | 0x4000,
                    )
            except Exception:
                pass
        self._notify_ui("overlay_toggled", {"is_mini_overlay": self._is_mini_overlay})
        return {"ok": True, "is_mini_overlay": self._is_mini_overlay}


def parse_args(args=None):
    parser = argparse.ArgumentParser(description="Soundboard AI — VB-Audio Cable Studio")
    parser.add_argument("--minimized", action="store_true", help="Inicia minimizado na bandeja do sistema")
    parser.add_argument("--debug", action="store_true", help="Ativa modo debug e logs no console")
    parser.add_argument("--devtools", action="store_true", help="Abre o painel de desenvolvedor (DevTools) do WebView")
    parser.add_argument("--no-tray", action="store_true", help="Desativa o ícone na bandeja do sistema")
    parser.add_argument("--data-dir", type=str, default=None, help="Caminho personalizado da pasta de dados")
    return parser.parse_args(args)


def main(args=None):
    import webview
    from single_instance import SingleInstance
    from app_logging import setup_logging
    from tray import SoundboardTray, ensure_icon_image, ensure_ico_file

    opts = parse_args(args)

    app_cfg = config.load_config(opts.data_dir)
    log_file = setup_logging(app_cfg.data_dir, debug=opts.debug)
    logging.info(f"Soundboard AI iniciando. Log em: {log_file}")

    single_inst = SingleInstance(data_dir=app_cfg.data_dir)
    if not single_inst.acquire():
        logging.warning("Outra instância do Soundboard AI já está em execução. Encerrando.")
        single_inst.notify_already_running()
        sys.exit(0)

    atexit.register(single_inst.release)

    api = SoundboardAPI(data_dir=opts.data_dir, start_background_services=True)
    ui_path = Path(__file__).parent.resolve() / "ui" / "index.html"
    icon_path = Path(__file__).parent.resolve() / "ui" / "icon.png"
    ensure_icon_image(icon_path)
    ico_path = ensure_ico_file(icon_path) if sys.platform == "win32" else icon_path

    window = webview.create_window(
        title="Soundboard AI — VB-Audio Cable Studio",
        url=str(ui_path),
        js_api=api,
        width=1280,
        height=820,
        min_size=(360, 460),
        background_color="#0b0f19",
        hidden=opts.minimized,
    )
    api._set_window(window)

    tray = None
    is_exiting = False

    if not opts.no_tray:
        def on_open():
            window.show()
            window.restore()

        def on_exit():
            nonlocal is_exiting
            is_exiting = True
            if tray:
                tray.stop()
            window.destroy()

        def on_open_data_dir():
            try:
                os.startfile(str(api._config.data_dir))
            except Exception:
                import webbrowser
                webbrowser.open(api._config.data_dir.as_uri())

        tray = SoundboardTray(
            on_open=on_open,
            on_exit=on_exit,
            on_open_data_dir=on_open_data_dir,
            app_root=Path(__file__).parent.resolve(),
            icon_path=icon_path,
        )
        tray.run_detached()

        def on_closing():
            if not is_exiting:
                window.hide()
                return False
            if tray:
                tray.stop()
            return True

        window.events.closing += on_closing

    try:
        webview.start(debug=opts.devtools, icon=str(ico_path) if ico_path and ico_path.exists() else None)
    finally:
        if tray:
            tray.stop()


if __name__ == "__main__":
    main()
