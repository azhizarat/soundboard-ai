import os
import sys
import time
import shutil
import threading
from pathlib import Path

from store import SoundboardStore
from ai_analyzer import AIAnalyzer
from audio_engine import AudioEngine
from hotkeys import HotkeyManager

SUPPORTED_AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".webm")


class SoundboardAPI:
    def __init__(self, data_dir: str = "data", start_background_services: bool = True):
        self.base_dir = Path(__file__).parent.resolve()
        resolved_data = Path(data_dir) if os.path.isabs(data_dir) else (self.base_dir / data_dir)
        self.store = SoundboardStore(data_dir=str(resolved_data))
        self.ai = AIAnalyzer()
        self.audio = AudioEngine(
            replay_seconds=float(self.store.state["settings"].get("replay_seconds", 15)),
            start_loopback=start_background_services,
        )
        self.window = None
        self.is_mini_overlay = False

        self._auto_bind_devices()

        self.hotkeys = HotkeyManager(
            on_play_clip=self.play_clip,
            on_stop_all=self.stop_all,
            on_save_replay=self.capture_replay_clip,
            on_toggle_overlay=self.toggle_mini_overlay,
        )
        if start_background_services:
            self.hotkeys.refresh(self.store.state["clips"], self.store.state["settings"])
            if self.store.state["settings"].get("mic_passthrough"):
                self._apply_mic_passthrough()

    def _auto_bind_devices(self):
        dev_info = self.audio.list_devices()
        settings = self.store.state["settings"]
        changed = False

        if settings.get("cable_device_id") is None and dev_info.get("detected_cable_id") is not None:
            settings["cable_device_id"] = dev_info["detected_cable_id"]
            settings["cable_device_name"] = dev_info.get("detected_cable_name", "")
            changed = True

        if settings.get("monitor_device_id") is None and dev_info.get("default_output_id") is not None:
            settings["monitor_device_id"] = dev_info["default_output_id"]
            changed = True

        if settings.get("mic_device_id") is None and dev_info.get("default_input_id") is not None:
            settings["mic_device_id"] = dev_info["default_input_id"]
            changed = True

        if changed:
            self.store.save()

    def _apply_mic_passthrough(self):
        s = self.store.state["settings"]
        self.audio.set_mic_passthrough(
            enabled=bool(s.get("mic_passthrough", False)),
            mic_id=s.get("mic_device_id"),
            cable_id=s.get("cable_device_id"),
            gain=float(s.get("mic_gain", 1.0)),
        )

    def _notify_ui(self, event_name: str, payload: dict = None):
        if self.window is None:
            return
        try:
            import json
            js_payload = json.dumps(payload or {}, ensure_ascii=False)
            self.window.evaluate_js(
                f"window.onBackendEvent && window.onBackendEvent({json.dumps(event_name)}, {js_payload});"
            )
        except Exception:
            pass

    def get_initial_state(self) -> dict:
        devs = self.audio.list_devices()
        # Discover existing 'separados' folders in C:\Users\Usuario\Videos for 1-click import
        discovered_separados = []
        videos_root = Path(r"C:\Users\Usuario\Videos")
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

        return {
            "settings": self.store.state["settings"],
            "categories": self.store.state["categories"],
            "speakers": self.store.state["speakers"],
            "clips": self.store.state["clips"],
            "devices": devs,
            "discovered_separados": discovered_separados,
            "models_dir": str(self.ai.models_dir),
            "has_titanet": self.ai.emb_model_path.exists(),
            "has_ref_voices": self.ai.ref_voices_path.exists(),
            "is_mini_overlay": self.is_mini_overlay,
        }

    def refresh_devices(self) -> dict:
        self._auto_bind_devices()
        return self.audio.list_devices()

    def update_settings(self, updates: dict) -> dict:
        s = self.store.update_settings(updates)
        if "replay_seconds" in updates:
            self.audio.replay_buffer.set_max_seconds(float(s.get("replay_seconds", 15)))
        if any(k in updates for k in ("mic_passthrough", "mic_device_id", "cable_device_id", "mic_gain")):
            self._apply_mic_passthrough()
        if any(k in updates for k in ("stop_all_hotkey", "replay_hotkey", "overlay_hotkey")):
            self.hotkeys.refresh(self.store.state["clips"], s)
        return s

    def play_clip(self, clip_id: str, monitor_only: bool = False) -> dict:
        clip = self.store.get_clip(clip_id)
        if not clip:
            return {"ok": False, "error": "Clip não encontrado"}

        settings = self.store.state["settings"]
        play_id = self.audio.play_clip(
            clip=clip,
            settings=settings,
            on_finish=lambda cid, pid: self._notify_ui("clip_finished", {"clip_id": cid, "play_id": pid}),
            monitor_only=bool(monitor_only),
        )
        if not monitor_only:
            self.store.increment_play_count(clip_id)
        self._notify_ui("clip_started", {
            "clip_id": clip_id,
            "play_id": play_id,
            "duration": max(0.2, float(clip.get("trim_end") or clip.get("duration") or 1.5) - float(clip.get("trim_start") or 0.0)),
        })
        return {"ok": True, "play_id": play_id}

    def stop_all(self) -> dict:
        self.audio.stop_all()
        self._notify_ui("stopped_all", {})
        return {"ok": True}

    def save_clip_edits(self, clip_data: dict) -> dict:
        updated = self.store.add_or_update_clip(clip_data)
        self.hotkeys.refresh(self.store.state["clips"], self.store.state["settings"])
        return {"ok": True, "clip": updated, "speakers": self.store.state["speakers"]}

    def toggle_favorite(self, clip_id: str) -> dict:
        clip = self.store.get_clip(clip_id)
        if not clip:
            return {"ok": False}
        updated = self.store.add_or_update_clip({"id": clip_id, "favorite": not clip.get("favorite", False)})
        return {"ok": True, "clip": updated}

    def delete_clip(self, clip_id: str, delete_file: bool = False) -> dict:
        ok = self.store.delete_clip(clip_id, delete_file=delete_file)
        self.hotkeys.refresh(self.store.state["clips"], self.store.state["settings"])
        return {"ok": ok}

    def update_speaker(self, spk_id: str, name: str, color: str = None, avatar: str = None) -> dict:
        spk = self.store.update_speaker(spk_id, name=name, color=color, avatar=avatar)
        return {"ok": True, "speaker": spk, "speakers": self.store.state["speakers"]}

    def enroll_speaker_from_clip(self, clip_id: str, spk_id: str, speaker_name: str = "") -> dict:
        clip = self.store.get_clip(clip_id)
        if not clip or not clip.get("file_path"):
            return {"ok": False, "error": "Áudio inválido"}
        if speaker_name:
            self.store.update_speaker(spk_id, name=speaker_name)
        else:
            self.store.ensure_speaker(spk_id)
        ok = self.ai.enroll_speaker_reference(clip["file_path"], spk_id)
        self.store.add_or_update_clip({"id": clip_id, "speaker_id": spk_id})
        return {
            "ok": ok,
            "speakers": self.store.state["speakers"],
            "clip": self.store.get_clip(clip_id),
        }

    def reanalyze_clip_ai(self, clip_id: str) -> dict:
        clip = self.store.get_clip(clip_id)
        if not clip or not clip.get("file_path") or not os.path.exists(clip["file_path"]):
            return {"ok": False, "error": "Arquivo de áudio não encontrado"}

        spk_id = clip.get("speaker_id", "")
        spk_name = self.store.state["speakers"].get(spk_id, {}).get("name", "")
        res = self.ai.analyze_file(
            clip["file_path"],
            existing_transcript=None,
            existing_speaker_id=None,
            speaker_name=spk_name,
            use_llm=bool(self.store.state["settings"].get("use_local_llm", True)),
        )
        detected_spk = res.get("speaker_id") or spk_id
        if detected_spk:
            self.store.ensure_speaker(detected_spk)

        updated = self.store.add_or_update_clip({
            "id": clip_id,
            "title": res["title"],
            "category": res["category"],
            "emoji": res["emoji"],
            "color": res["color"],
            "tags": res["tags"],
            "transcript": res["transcript"],
            "speaker_id": detected_spk,
            "acoustic_mood": res["acoustic_mood"],
            "duration": res["duration"],
        })
        return {"ok": True, "clip": updated, "speakers": self.store.state["speakers"]}

    def import_audio_files(self, file_paths: list[str] = None) -> dict:
        if not file_paths and self.window is not None:
            try:
                import webview
                selected = self.window.create_file_dialog(
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
        use_llm = bool(self.store.state["settings"].get("use_local_llm", True))
        for src in file_paths:
            src_path = Path(src)
            if not src_path.exists() or not src_path.is_file():
                continue
            dst_name = f"{int(time.time() * 1000) % 1000000}_{src_path.name}"
            dst_path = self.store.clips_dir / dst_name
            shutil.copy2(src_path, dst_path)

            res = self.ai.analyze_file(str(dst_path), use_llm=use_llm)
            spk_id = res.get("speaker_id", "")
            if spk_id:
                self.store.ensure_speaker(spk_id)

            clip = self.store.add_or_update_clip({
                "title": res.get("title") or src_path.stem[:28],
                "file_path": str(dst_path),
                "category": res.get("category") or "Call",
                "speaker_id": spk_id,
                "transcript": res.get("transcript") or "",
                "acoustic_mood": res.get("acoustic_mood") or "Normal",
                "emoji": res.get("emoji") or "🔊",
                "color": res.get("color") or "#3b82f6",
                "tags": res.get("tags") or [],
                "duration": res.get("duration") or 1.0,
            })
            imported.append(clip)

        self._notify_ui("clips_updated", {
            "clips": self.store.state["clips"],
            "speakers": self.store.state["speakers"],
        })
        return {"ok": True, "imported": imported, "clips": self.store.state["clips"], "speakers": self.store.state["speakers"]}

    def import_folder_or_separados(self, folder_path: str = None) -> dict:
        if not folder_path and self.window is not None:
            try:
                import webview
                selected = self.window.create_file_dialog(webview.FOLDER_DIALOG)
                if selected:
                    folder_path = selected[0] if isinstance(selected, (list, tuple)) else str(selected)
            except Exception:
                folder_path = None

        if not folder_path:
            return {"ok": False, "imported": []}

        target = Path(folder_path).resolve()
        if not target.exists():
            return {"ok": False, "error": "Pasta não encontrada"}

        # Check if target or target/separados has mapa_de_falas.json
        if (target / "separados" / "mapa_de_falas.json").exists():
            target = target / "separados"

        use_llm = bool(self.store.state["settings"].get("use_local_llm", True))
        if (target / "mapa_de_falas.json").exists():
            def _fast_analyzer(fp, existing_transcript="", existing_speaker_id="", speaker_name=""):
                return self.ai.analyze_file(
                    fp,
                    existing_transcript=existing_transcript,
                    existing_speaker_id=existing_speaker_id,
                    speaker_name=speaker_name,
                    use_llm=use_llm,
                )

            imported = self.store.import_separados_manifest(str(target), analyzer_fn=_fast_analyzer)
            self._notify_ui("clips_updated", {
                "clips": self.store.state["clips"],
                "speakers": self.store.state["speakers"],
            })
            return {
                "ok": True,
                "imported_count": len(imported),
                "clips": self.store.state["clips"],
                "speakers": self.store.state["speakers"],
            }

        # Otherwise scan audio files in folder
        audio_files = [
            str(p) for p in sorted(target.rglob("*"))
            if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS and not p.name.endswith("_completo.mp3")
        ]
        res = self.import_audio_files(audio_files)
        res["imported_count"] = len(res.get("imported", []))
        return res

    def capture_replay_clip(self, last_seconds: float = None) -> dict:
        secs = float(last_seconds or self.store.state["settings"].get("replay_seconds", 15))
        out_name = f"replay_{time.strftime('%Y%m%d_%H%M%S')}.wav"
        out_path = str(self.store.clips_dir / out_name)

        saved = self.audio.save_replay_wav(out_path, last_seconds=secs)
        if not saved:
            self._notify_ui("toast", {
                "type": "warning",
                "message": "Nenhum áudio recente detectado nos últimos segundos da call/sistema.",
            })
            return {"ok": False, "error": "Buffer de replay vazio ou silencioso"}

        res = self.ai.analyze_file(
            saved,
            use_llm=bool(self.store.state["settings"].get("use_local_llm", True)),
        )
        spk_id = res.get("speaker_id", "")
        if spk_id:
            self.store.ensure_speaker(spk_id)

        clip = self.store.add_or_update_clip({
            "title": res.get("title") or f"Replay {time.strftime('%H:%M:%S')}",
            "file_path": saved,
            "category": res.get("category") or "Call",
            "speaker_id": spk_id,
            "transcript": res.get("transcript") or "",
            "acoustic_mood": res.get("acoustic_mood") or "Normal",
            "emoji": res.get("emoji") or "⚡",
            "color": res.get("color") or "#f59e0b",
            "tags": res.get("tags") or ["replay"],
            "duration": res.get("duration") or secs,
        })
        self._notify_ui("replay_captured", {
            "clip": clip,
            "clips": self.store.state["clips"],
            "speakers": self.store.state["speakers"],
        })
        return {"ok": True, "clip": clip, "clips": self.store.state["clips"], "speakers": self.store.state["speakers"]}

    def toggle_mini_overlay(self) -> dict:
        self.is_mini_overlay = not self.is_mini_overlay
        if self.window is not None:
            try:
                self.window.on_top = self.is_mini_overlay
                if self.is_mini_overlay:
                    self.window.resize(410, 580)
                else:
                    self.window.resize(1280, 820)
            except Exception:
                pass
        self._notify_ui("overlay_toggled", {"is_mini_overlay": self.is_mini_overlay})
        return {"ok": True, "is_mini_overlay": self.is_mini_overlay}


def main():
    import webview

    api = SoundboardAPI(data_dir="data", start_background_services=True)
    ui_path = Path(__file__).parent.resolve() / "ui" / "index.html"

    window = webview.create_window(
        title="Soundboard AI — VB-Audio Cable Studio",
        url=str(ui_path),
        js_api=api,
        width=1280,
        height=820,
        min_size=(360, 460),
        background_color="#0b0f19",
    )
    api.window = window
    webview.start(debug=False)


if __name__ == "__main__":
    main()
