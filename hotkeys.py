import threading


class HotkeyManager:
    """Manages global background keyboard shortcuts for playing clips, stop-all, call replay, and mini-overlay."""

    def __init__(self, on_play_clip, on_stop_all, on_save_replay, on_toggle_overlay):
        self.on_play_clip = on_play_clip
        self.on_stop_all = on_stop_all
        self.on_save_replay = on_save_replay
        self.on_toggle_overlay = on_toggle_overlay
        self._lock = threading.Lock()
        self._registered = []
        self.enabled = True

    def clear(self):
        try:
            import keyboard
            for hk in self._registered:
                try:
                    keyboard.remove_hotkey(hk)
                except Exception:
                    pass
        except Exception:
            pass
        self._registered.clear()

    def refresh(self, clips: list[dict], settings: dict):
        with self._lock:
            self.clear()
            if not self.enabled:
                return
            try:
                import keyboard
            except Exception:
                return

            stop_hk = (settings.get("stop_all_hotkey") or "").strip().lower()
            if stop_hk:
                try:
                    h = keyboard.add_hotkey(stop_hk, lambda: self._safe_call(self.on_stop_all))
                    self._registered.append(h)
                except Exception:
                    pass

            replay_hk = (settings.get("replay_hotkey") or "").strip().lower()
            if replay_hk:
                try:
                    h = keyboard.add_hotkey(replay_hk, lambda: self._safe_call(self.on_save_replay))
                    self._registered.append(h)
                except Exception:
                    pass

            overlay_hk = (settings.get("overlay_hotkey") or "").strip().lower()
            if overlay_hk:
                try:
                    h = keyboard.add_hotkey(overlay_hk, lambda: self._safe_call(self.on_toggle_overlay))
                    self._registered.append(h)
                except Exception:
                    pass

            for clip in clips:
                hk = (clip.get("hotkey") or "").strip().lower()
                cid = clip.get("id")
                if hk and cid:
                    try:
                        h = keyboard.add_hotkey(hk, lambda c=cid: self._safe_call(self.on_play_clip, c))
                        self._registered.append(h)
                    except Exception:
                        pass

    @staticmethod
    def _safe_call(fn, *args):
        if callable(fn):
            threading.Thread(target=fn, args=args, daemon=True).start()
