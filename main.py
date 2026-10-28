import os
import sys
import time
import shutil
import threading
from pathlib import Path

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

SUPPORTED_AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".webm")


class SoundboardAPI:
    def __init__(self, data_dir: str = "data", start_background_services: bool = True):
        # All internal state attributes MUST start with '_' so pywebview does not
        # recursively inspect/serialize them (especially self._window.native!).
        self._base_dir = Path(__file__).parent.resolve()
        resolved_data = Path(data_dir) if os.path.isabs(data_dir) else (self._base_dir / data_dir)
        self._store = SoundboardStore(data_dir=str(resolved_data))
        self._ai = AIAnalyzer()
        self._audio = AudioEngine(
            replay_seconds=max(60.0, float(self._store.state["settings"].get("replay_seconds", 15))),
            start_loopback=start_background_services,
        )
        self._window = None
        self._is_mini_overlay = False

        self._auto_bind_devices()

        self._hotkeys = HotkeyManager(
            on_play_clip=self.play_clip,
            on_stop_all=self.stop_all,
            on_save_replay=self.capture_replay_clip,
            on_toggle_overlay=self.toggle_mini_overlay,
        )
        if start_background_services:
            self._hotkeys.refresh(self._store.state["clips"], self._store.state["settings"])
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

    def _auto_bind_devices(self):
        dev_info = self._audio.list_devices()
        settings = self._store.state["settings"]
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
            self._store.save()

    def _apply_mic_passthrough(self):
        s = self._store.state["settings"]
        self._audio.set_mic_passthrough(
            enabled=bool(s.get("mic_passthrough", False)),
            mic_id=s.get("mic_device_id"),
            cable_id=s.get("cable_device_id"),
            gain=float(s.get("mic_gain", 1.0)),
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
            "settings": self._store.state["settings"],
            "categories": self._store.state["categories"],
            "speakers": self._store.state["speakers"],
            "clips": self._store.state["clips"],
            "devices": devs,
            "discovered_separados": discovered_separados,
            "models_dir": str(self._ai.models_dir),
            "has_titanet": self._ai.emb_model_path.exists(),
            "has_ref_voices": self._ai.ref_voices_path.exists(),
            "is_mini_overlay": self._is_mini_overlay,
        }

    def refresh_devices(self) -> dict:
        self._auto_bind_devices()
        return self._audio.list_devices()

    def update_settings(self, updates: dict) -> dict:
        if "replay_seconds" in updates:
            updates["replay_seconds"] = round(max(1.0, min(120.0, float(updates["replay_seconds"]))), 1)
        s = self._store.update_settings(updates)
        if "replay_seconds" in updates:
            self._audio.replay_buffer.set_max_seconds(max(60.0, float(s.get("replay_seconds", 15))))
        if any(k in updates for k in ("mic_passthrough", "mic_device_id", "cable_device_id", "mic_gain")):
            self._apply_mic_passthrough()
        if any(k in updates for k in ("stop_all_hotkey", "replay_hotkey", "overlay_hotkey")):
            self._hotkeys.refresh(self._store.state["clips"], s)
        return s

    def play_clip(self, clip_id: str, monitor_only: bool = False) -> dict:
        clip = self._store.get_clip(clip_id)
        if not clip:
            return {"ok": False, "error": "Clip não encontrado"}

        settings = self._store.state["settings"]
        eff_dur = max(
            0.2,
            float(clip.get("trim_end") or clip.get("duration") or 1.5) - float(clip.get("trim_start") or 0.0),
        )
        play_id = self._audio.play_clip(
            clip=clip,
            settings=settings,
            on_finish=lambda cid, pid: self._notify_ui("clip_finished", {"clip_id": cid, "play_id": pid}),
            on_tick=lambda st: self._notify_ui("playback_tick", st),
            monitor_only=bool(monitor_only),
        )
        if not monitor_only:
            self._store.increment_play_count(clip_id)
        self._notify_ui("clip_started", {
            "clip_id": clip_id,
            "play_id": play_id,
            "duration": eff_dur,
            "monitor_only": bool(monitor_only),
            "live_volume": float(clip.get("volume", 1.0)),
        })
        return {"ok": True, "play_id": play_id}

    def pause_resume_playback(self, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        paused = sess.toggle_pause()
        st = sess.status()
        self._notify_ui("playback_tick", st)
        return {"ok": True, "paused": paused, "status": st}

    def seek_playback(self, position_s: float, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        sess.seek(position_s)
        st = sess.status()
        self._notify_ui("playback_tick", st)
        return {"ok": True, "status": st}

    def set_playback_loop(self, loop: bool, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        loop_val = sess.set_loop(loop)
        st = sess.status()
        self._notify_ui("playback_tick", st)
        return {"ok": True, "loop": loop_val, "status": st}

    def set_live_clip_volume(self, volume: float, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        vol = sess.set_live_volume(volume)
        self._store.add_or_update_clip({"id": sess.clip_id, "volume": vol})
        st = sess.status()
        self._notify_ui("playback_tick", st)
        return {"ok": True, "volume": vol, "status": st}

    def restart_playback(self, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        sess.restart()
        st = sess.status()
        self._notify_ui("playback_tick", st)
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

    def stop_all(self) -> dict:
        self._audio.stop_all()
        self._notify_ui("stopped_all", {})
        return {"ok": True}

    def save_clip_edits(self, clip_data: dict) -> dict:
        updated = self._store.add_or_update_clip(clip_data)
        self._hotkeys.refresh(self._store.state["clips"], self._store.state["settings"])
        return {"ok": True, "clip": updated, "speakers": self._store.state["speakers"]}

    def toggle_favorite(self, clip_id: str) -> dict:
        clip = self._store.get_clip(clip_id)
        if not clip:
            return {"ok": False}
        updated = self._store.add_or_update_clip({"id": clip_id, "favorite": not clip.get("favorite", False)})
        return {"ok": True, "clip": updated}

    def delete_clip(self, clip_id: str, delete_file: bool = False) -> dict:
        ok = self._store.delete_clip(clip_id, delete_file=delete_file)
        self._hotkeys.refresh(self._store.state["clips"], self._store.state["settings"])
        return {"ok": ok}

    def update_speaker(self, spk_id: str, name: str, color: str = None, avatar: str = None) -> dict:
        spk = self._store.update_speaker(spk_id, name=name, color=color, avatar=avatar)
        return {"ok": True, "speaker": spk, "speakers": self._store.state["speakers"]}

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
        return {
            "ok": ok,
            "speakers": self._store.state["speakers"],
            "clips": self._store.state["clips"],
        }

    def enroll_speaker_from_clip(self, clip_id: str, spk_id: str, speaker_name: str = "") -> dict:
        clip = self._store.get_clip(clip_id)
        if not clip or not clip.get("file_path"):
            return {"ok": False, "error": "Áudio inválido"}
        if speaker_name:
            self._store.update_speaker(spk_id, name=speaker_name)
        else:
            self._store.ensure_speaker(spk_id)
        ok = self._ai.enroll_speaker_reference(clip["file_path"], spk_id)
        self._store.add_or_update_clip({"id": clip_id, "speaker_id": spk_id})
        return {
            "ok": ok,
            "speakers": self._store.state["speakers"],
            "clip": self._store.get_clip(clip_id),
        }

    def reanalyze_clip_ai(self, clip_id: str) -> dict:
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
            })
            return {"ok": True, "clip": updated, "speakers": self._store.state["speakers"]}
        finally:
            self._emit_progress(False)

    def split_clip_by_speakers(self, clip_id: str) -> dict:
        import soundfile as sf
        from ai_analyzer import load_audio_mono_16k

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

            self._notify_ui("clips_updated", {
                "clips": self._store.state["clips"],
                "speakers": self._store.state["speakers"],
            })
            return {
                "ok": True,
                "created_count": len(created_clips),
                "created_clips": created_clips,
                "clips": self._store.state["clips"],
                "speakers": self._store.state["speakers"],
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
        use_llm = bool(self._store.state["settings"].get("use_local_llm", True))
        total_files = len(file_paths)
        mode_lbl = "Importando Vinhetas / Sons" if skip_speaker_id else "Importando e analisando áudios com IA"

        try:
            for idx, src in enumerate(file_paths, 1):
                src_path = Path(src)
                if not src_path.exists() or not src_path.is_file():
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
                })
                imported.append(clip)
        finally:
            self._emit_progress(False)

        self._notify_ui("clips_updated", {
            "clips": self._store.state["clips"],
            "speakers": self._store.state["speakers"],
        })
        return {"ok": True, "imported": imported, "clips": self._store.state["clips"], "speakers": self._store.state["speakers"]}

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

            self._notify_ui("clips_updated", {
                "clips": self._store.state["clips"],
                "speakers": self._store.state["speakers"],
            })
            return {
                "ok": True,
                "imported_count": len(imported),
                "clips": self._store.state["clips"],
                "speakers": self._store.state["speakers"],
            }

        audio_files = [
            str(p) for p in sorted(target.rglob("*"))
            if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS and not p.name.endswith("_completo.mp3")
        ]
        res = self.import_audio_files(audio_files, skip_speaker_id=skip_speaker_id, forced_category=forced_category)
        res["imported_count"] = len(res.get("imported", []))
        return res

    def capture_replay_clip(self, last_seconds: float = None) -> dict:
        secs = float(last_seconds or self._store.state["settings"].get("replay_seconds", 15))
        out_name = f"replay_{time.strftime('%Y%m%d_%H%M%S')}.wav"
        out_path = str(self._store.clips_dir / out_name)

        self._emit_progress(True, 1, 3, f"Capturando últimos {int(secs)}s da call...", out_name)
        try:
            saved = self._audio.save_replay_wav(out_path, last_seconds=secs)
            if not saved:
                self._notify_ui("toast", {
                    "type": "warning",
                    "message": "Nenhum som recente tocado no seu fone/sistema nos últimos segundos.",
                })
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

            clip = self._store.add_or_update_clip({
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
            })

            if self._store.state["settings"].get("auto_split_speakers") and len(res.get("segments") or []) > 1:
                split_res = self.split_clip_by_speakers(clip["id"])
                if split_res.get("ok"):
                    return {
                        "ok": True,
                        "clip": clip,
                        "split_count": split_res.get("created_count", 0),
                        "clips": self._store.state["clips"],
                        "speakers": self._store.state["speakers"],
                    }

            self._notify_ui("replay_captured", {
                "clip": clip,
                "clips": self._store.state["clips"],
                "speakers": self._store.state["speakers"],
            })
            return {"ok": True, "clip": clip, "clips": self._store.state["clips"], "speakers": self._store.state["speakers"]}
        finally:
            self._emit_progress(False)

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
    api._set_window(window)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
