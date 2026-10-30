import threading
import uuid
from sound_collections import normalize_binding, binding_conflicts


class HotkeyManager:
    """Manages global background keyboard shortcuts for playing clips, stop-all, call replay, and mini-overlay."""

    def __init__(self, on_play_clip, on_stop_all, on_save_replay, on_toggle_overlay,
                 on_sequence=None, sequence_provider=None, on_collection=None, collection_provider=None):
        self.on_play_clip = on_play_clip
        self.on_stop_all = on_stop_all
        self.on_save_replay = on_save_replay
        self.on_toggle_overlay = on_toggle_overlay
        self._lock = threading.Lock()
        self._registered = []
        self.enabled = True
        self.on_sequence = on_sequence
        self.sequence_provider = sequence_provider
        self._sequence_pressed = set()
        self._sequence_lock = threading.Lock()
        self._release_hooks = []
        self.on_collection = on_collection
        self.collection_provider = collection_provider
        self._collection_pressed = set()
        self._collection_tokens = {}
        self.conflicts = []

    def clear(self, reset_pressed=True):
        try:
            import keyboard
            for hk in self._registered:
                try:
                    keyboard.remove_hotkey(hk)
                except Exception:
                    pass
            for hook in self._release_hooks:
                try:
                    keyboard.unhook(hook)
                except Exception:
                    pass
        except Exception:
            pass
        self._registered.clear()
        self._release_hooks.clear()
        if reset_pressed:
            with self._sequence_lock:
                self._sequence_pressed.clear()

    def refresh(self, clips: list[dict], settings: dict):
        with self._lock:
            self.clear(reset_pressed=not self.enabled)
            if not self.enabled:
                return
            try:
                import keyboard
            except Exception:
                return

            sequences = self.sequence_provider() if self.sequence_provider else []
            collection = self.collection_provider() if self.collection_provider else None
            self.conflicts = binding_conflicts({'settings': settings, 'clips': clips,
                'sequences': {s['id']: s for s in sequences}}, collection)
            blocked = {c['hotkey'] for c in self.conflicts}
            def binding(raw):
                try:
                    key = normalize_binding(raw or '')
                    return '' if key in blocked else key
                except ValueError:
                    return ''

            stop_hk = normalize_binding(settings.get("stop_all_hotkey") or '')
            if stop_hk:
                try:
                    h = keyboard.add_hotkey(stop_hk, self.trigger_stop_all)
                    self._registered.append(h)
                except Exception:
                    pass

            replay_hk = binding(settings.get("replay_hotkey"))
            if replay_hk:
                try:
                    h = keyboard.add_hotkey(replay_hk, lambda: self._safe_call(self.on_save_replay))
                    self._registered.append(h)
                except Exception:
                    pass

            overlay_hk = binding(settings.get("overlay_hotkey"))
            if overlay_hk:
                try:
                    h = keyboard.add_hotkey(overlay_hk, lambda: self._safe_call(self.on_toggle_overlay))
                    self._registered.append(h)
                except Exception:
                    pass

            for clip in clips:
                hk = binding(clip.get("hotkey"))
                cid = clip.get("id")
                if hk and cid:
                    try:
                        h = keyboard.add_hotkey(hk, lambda c=cid: self.on_play_clip(c) if callable(self.on_play_clip) else None)
                        self._registered.append(h)
                    except Exception:
                        pass

            for sequence in sequences:
                hk = binding(sequence.get('hotkey'))
                ident = sequence.get('id')
                if hk and ident:
                    try:
                        self._registered.append(keyboard.add_hotkey(hk, lambda sid=ident: self.trigger_sequence(sid)))
                        self._release_hooks.append(keyboard.on_release_key(hk.split('+')[-1], lambda event, sid=ident: self.release_sequence(sid)))
                    except Exception:
                        pass

            collection_keys = set()
            for pad in (collection or {}).get('pads', []):
                hk = binding(pad.get('hotkey'))
                if hk:
                    collection_keys.add(hk)
                    try:
                        self._registered.append(keyboard.add_hotkey(hk, lambda key=hk: self.trigger_collection(key)))
                    except Exception:
                        pass
            # O hook de soltura sobrevive à troca de coleção, mesmo se a nova não usa a tecla.
            for hk in collection_keys | self._collection_pressed:
                try:
                    self._release_hooks.append(keyboard.on_release_key(hk.split('+')[-1],
                        lambda event, key=hk: self.release_collection(key)))
                except Exception:
                    pass

    def trigger_collection(self, binding, repeat=False):
        key = normalize_binding(binding)
        with self._sequence_lock:
            if repeat or key in self._collection_pressed:
                return False
            self._collection_pressed.add(key)
            token = 'keyboard:' + str(uuid.uuid4())
            self._collection_tokens[key] = token
        if callable(self.on_collection):
            return self.on_collection(key, 'press', repeat, token)
        return False

    def release_collection(self, binding):
        key = normalize_binding(binding)
        with self._sequence_lock:
            self._collection_pressed.discard(key)
            token = self._collection_tokens.pop(key, None)
        if callable(self.on_collection) and token:
            return self.on_collection(key, 'release', False, token)

    def trigger_sequence(self, sequence_id, repeat=False):
        with self._sequence_lock:
            if repeat or sequence_id in self._sequence_pressed:
                return False
            self._sequence_pressed.add(sequence_id)
        # O handler captura o estado no key-down; não fila um avanço antecipado.
        if callable(self.on_sequence):
            self.on_sequence(sequence_id)
        return True

    def release_sequence(self, sequence_id):
        with self._sequence_lock:
            self._sequence_pressed.discard(sequence_id)

    @staticmethod
    def _safe_call(fn, *args):
        if callable(fn):
            threading.Thread(target=fn, args=args, daemon=True).start()

    def trigger_stop_all(self):
        """Encaminha ao engine, que cancela donos/esperas antes de liberar vozes."""
        self._safe_call(self.on_stop_all)
