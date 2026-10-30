"""Persistent multi-voice audio mixer with device-level streams, anti-click crossfades and lookahead limiter."""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from typing import Callable
import numpy as np

logger = logging.getLogger(__name__)

TARGET_SR = 48000
DEFAULT_BLOCKSIZE = 1024
CEILING_DBTP = -1.0
CEILING_LINEAR = 0.8910  # 10 ** (-1.0 / 20.0) rounded conservatively to stay <= -1.0 dBFS
CROSSFADE_SAMPLES_10MS = 480  # 10 ms at 48 kHz
# Render sides of virtual cables. Playing "monitor" audio into one of these is
# never audible: VB-Cable accepts the stream and silently drops it, so the route
# must be rejected instead of reported as playing.
CABLE_NAME_TOKENS = (
    "cable input",
    "cable-a",
    "cable-b",
    "vb-audio virtual cable",
    "vb-audio point",
    "voicemeeter",
)

_device_info_cache: dict[int, dict] = {}
_device_info_lock = threading.Lock()


def device_info(device_id) -> dict:
    """Cached PortAudio snapshot of one device; empty dict when it is gone."""
    try:
        idx = int(device_id)
    except (TypeError, ValueError):
        return {}
    with _device_info_lock:
        cached = _device_info_cache.get(idx)
    if cached is not None:
        return cached
    try:
        import sounddevice as sd

        info = dict(sd.query_devices(idx))
    except Exception:
        info = {}
    with _device_info_lock:
        _device_info_cache.setdefault(idx, info)
    return info


def invalidate_device_cache() -> None:
    """Drops cached device metadata after a refresh/device change."""
    with _device_info_lock:
        _device_info_cache.clear()


def is_cable_output_device(device_id) -> bool:
    """True when the id is the render side of a virtual cable (VB-Cable, VoiceMeeter)."""
    info = device_info(device_id)
    if not info:
        return False
    try:
        if int(info.get("max_output_channels") or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    name = str(info.get("name") or "").strip().lower()
    return any(token in name for token in CABLE_NAME_TOKENS)


def _resample_block(block: np.ndarray, frames: int) -> np.ndarray:
    """Linearly resamples one rendered block to the stream's frame count."""
    src = len(block)
    if src == frames:
        return block
    if src <= 1 or frames <= 0:
        return np.zeros((max(0, frames), 2), dtype=np.float32)
    x_old = np.linspace(0.0, 1.0, src, dtype=np.float64)
    x_new = np.linspace(0.0, 1.0, frames, dtype=np.float64)
    out = np.empty((frames, block.shape[1]), dtype=np.float32)
    for ch in range(block.shape[1]):
        out[:, ch] = np.interp(x_new, x_old, block[:, ch])
    return out


def apply_soft_limiter(x: np.ndarray, ceiling: float = CEILING_LINEAR) -> np.ndarray:
    """Transparent limiter preserving linearity below 90% ceiling with smooth tanh curve above."""
    knee = 0.90 * ceiling
    over = np.abs(x) > knee
    if not np.any(over):
        return x

    y = np.copy(x)
    sign = np.sign(x[over])
    mag = np.abs(x[over])
    delta = mag - knee
    scale = 0.099 * ceiling
    y[over] = sign * (knee + scale * np.tanh(delta / scale))
    return y.astype(np.float32)


class MixerVoice:
    """Active audio voice track in the mixer."""

    def __init__(
        self,
        play_id: str,
        clip_id: str,
        data: np.ndarray,
        base_gain: float = 1.0,
        live_volume: float = 1.0,
        loop: bool = False,
        monitor_only: bool = False,
        sr: int = TARGET_SR,
        title: str = "Áudio",
        emoji: str = "🔊",
        on_finish: Callable[..., None] | None = None,
    ):
        self.play_id = play_id
        self.clip_id = clip_id
        self.data = np.asarray(data, dtype=np.float32)
        if self.data.ndim == 1:
            self.data = np.column_stack([self.data, self.data])
        elif self.data.shape[1] == 1:
            self.data = np.repeat(self.data, 2, axis=1)

        self.sr = int(sr)
        self.base_gain = max(0.0, float(base_gain))
        self.live_volume = max(0.0, min(3.0, float(live_volume)))
        self.loop = bool(loop)
        self.monitor_only = bool(monitor_only)
        self.title = str(title or "Áudio")
        self.emoji = str(emoji or "🔊")
        self.on_finish = on_finish

        self.total_samples = len(self.data)
        self.duration_s = round(float(self.total_samples / max(1, self.sr)), 2)

        self.pos_idx = 0
        self.requested_seek_pos = 0
        self.paused = False
        self.stopping = False
        self.fade_samples_remaining = CROSSFADE_SAMPLES_10MS
        self.fade_total_samples = CROSSFADE_SAMPLES_10MS
        self.seek_epoch = 0
        self._device_positions: dict[str, int] = {}
        self._destinations: set[str] = set()
        self._driver_destination: str | None = None

        self.stop_event = threading.Event()
        self._lock = threading.RLock()
        self._notified_finish = False

    def register_destinations(self, dests: set[str], driver: str | None = None) -> None:
        with self._lock:
            self._destinations = set(dests)
            self._driver_destination = driver

    def update_device_pos(self, dest_key: str, pos: int) -> None:
        with self._lock:
            self._device_positions[dest_key] = pos
            if self._driver_destination and self._driver_destination in self._device_positions:
                self.pos_idx = self._device_positions[self._driver_destination]
            elif self._device_positions:
                self.pos_idx = max(self._device_positions.values())

    def finish_destination(self, dest_key: str) -> None:
        with self._lock:
            self._device_positions.pop(dest_key, None)
            if self._destinations:
                self._destinations.discard(dest_key)
                if not self._destinations:
                    self.trigger_finish()
            else:
                self.trigger_finish()

    def set_live_volume(self, volume: float) -> float:
        with self._lock:
            self.live_volume = max(0.0, min(3.0, float(volume)))
            return self.live_volume

    def set_loop(self, loop: bool) -> bool:
        with self._lock:
            self.loop = bool(loop)
            return self.loop

    def pause(self) -> bool:
        with self._lock:
            self.paused = True
            return True

    def resume(self) -> bool:
        with self._lock:
            self.paused = False
            return False

    def toggle_pause(self) -> bool:
        with self._lock:
            self.paused = not self.paused
            return self.paused

    def restart(self) -> None:
        with self._lock:
            self.pos_idx = 0
            self.requested_seek_pos = 0
            self.seek_epoch += 1
            self.paused = False
            self.stopping = False
            for k in list(self._device_positions.keys()):
                self._device_positions[k] = 0

    def seek_to_seconds(self, sec: float) -> float:
        with self._lock:
            target_idx = max(0, min(self.total_samples, int(round(float(sec) * self.sr))))
            self.pos_idx = target_idx
            self.requested_seek_pos = target_idx
            self.seek_epoch += 1
            for k in list(self._device_positions.keys()):
                self._device_positions[k] = target_idx
            return round(float(self.pos_idx / max(1, self.sr)), 2)

    def seek(self, position_s: float) -> float:
        return self.seek_to_seconds(position_s)

    def seek_relative(self, delta_sec: float) -> float:
        with self._lock:
            cur_s = float(self.pos_idx / max(1, self.sr))
            return self.seek_to_seconds(cur_s + delta_sec)

    def stop(self, fade_ms: float = 10.0) -> None:
        with self._lock:
            if self.stopping or self.stop_event.is_set():
                return
            if fade_ms <= 0:
                self.trigger_finish()
                return
            fade_samples = max(1, int(round((fade_ms / 1000.0) * self.sr)))
            self.fade_samples_remaining = fade_samples
            self.fade_total_samples = fade_samples
            self.stopping = True

    def trigger_finish(self) -> None:
        with self._lock:
            self.stop_event.set()
            if not self._notified_finish:
                self._notified_finish = True
                if callable(self.on_finish):
                    try:
                        self.on_finish(self.clip_id, self.play_id)
                    except TypeError:
                        try:
                            self.on_finish()
                        except Exception as exc:
                            logger.warning(f"Erro no callback on_finish da voz {self.play_id}: {exc}")
                    except Exception as exc:
                        logger.warning(f"Erro no callback on_finish da voz {self.play_id}: {exc}")

    def status(self) -> dict:
        with self._lock:
            cur_s = round(float(self.pos_idx / max(1, self.sr)), 2)
            vol = round(self.live_volume, 2)
            return {
                "playing": not self.stop_event.is_set(),
                "play_id": self.play_id,
                "clip_id": self.clip_id,
                "title": self.title,
                "emoji": self.emoji,
                "position": cur_s,
                "duration": self.duration_s,
                "current_s": cur_s,
                "duration_s": self.duration_s,
                "paused": self.paused,
                "loop": self.loop,
                "volume": vol,
                "live_volume": vol,
                "monitor_only": self.monitor_only,
            }


class DeviceMixer:
    """Manages playback voices for a single output device stream."""

    def __init__(
        self,
        device_id: int | None = None,
        sr: int = TARGET_SR,
        blocksize: int = DEFAULT_BLOCKSIZE,
        volume: float = 1.0,
        dest_key: str = "device",
    ):
        self.device_id = device_id
        self.sr = sr
        self.blocksize = blocksize
        self.volume = max(0.0, float(volume))
        self.dest_key = dest_key
        self._prev_device_vol: float = self.volume
        self._voices: dict[str, MixerVoice] = {}
        self._voice_states: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._prev_duck: float = 1.0
        self._prev_gains: dict[str, float] = {}
        self._duck_provider: Callable[[], float] | None = None
        self._stream = None
        self._stream_running = False
        self._stream_sr = int(sr)
        self._stream_flags: dict | None = None  # per-stream flags for its finished_callback
        self._last_error: str | None = None

    def set_duck_provider(self, provider: Callable[[], float]) -> None:
        self._duck_provider = provider

    def _get_duck_gain(self) -> float:
        if callable(self._duck_provider):
            try:
                return float(self._duck_provider())
            except Exception:
                pass
        return 1.0

    def set_volume(self, volume: float) -> float:
        with self._lock:
            self.volume = max(0.0, min(3.0, float(volume)))
            return self.volume

    def add_voice(self, voice: MixerVoice) -> None:
        with self._lock:
            self._voices[voice.play_id] = voice
            self._prev_gains[voice.play_id] = voice.base_gain * voice.live_volume
            self._voice_states[voice.play_id] = {
                "pos": voice.pos_idx,
                "seek_epoch": voice.seek_epoch,
                "stopping": voice.stopping,
                "fade_rem": voice.fade_samples_remaining,
                "fade_tot": voice.fade_total_samples,
            }

    def remove_voice(self, play_id: str) -> None:
        with self._lock:
            voice = self._voices.pop(play_id, None)
            self._prev_gains.pop(play_id, None)
            self._voice_states.pop(play_id, None)
            if voice:
                voice.finish_destination(self.dest_key)

    def get_voice(self, play_id: str) -> MixerVoice | None:
        with self._lock:
            return self._voices.get(play_id)

    def render_block(self, frames: int, duck_gain: float | None = None) -> np.ndarray:
        mix = np.zeros((frames, 2), dtype=np.float32)
        duck_curr = self._get_duck_gain() if duck_gain is None else float(duck_gain)

        with self._lock:
            finished = []
            for play_id, voice in list(self._voices.items()):
                state = self._voice_states.get(play_id)
                if state is None:
                    continue

                with voice._lock:
                    if voice.paused:
                        continue
                    if voice.stop_event.is_set() and not state["stopping"]:
                        finished.append(play_id)
                        continue

                    if state["seek_epoch"] != voice.seek_epoch:
                        state["pos"] = voice.requested_seek_pos
                        state["seek_epoch"] = voice.seek_epoch
                        state["stopping"] = False

                    if voice.stopping and not state["stopping"]:
                        state["stopping"] = True
                        state["fade_rem"] = voice.fade_samples_remaining
                        state["fade_tot"] = voice.fade_total_samples

                    pos = state["pos"]
                    total = voice.total_samples
                    if pos >= total:
                        if voice.loop:
                            pos = 0
                            state["pos"] = 0
                        else:
                            finished.append(play_id)
                            continue

                    avail = min(frames, total - pos)
                    chunk = voice.data[pos : pos + avail]

                    target_gain = float(voice.base_gain * voice.live_volume)
                    prev_gain = self._prev_gains.get(play_id, target_gain)
                    gain_ramp = np.linspace(prev_gain, target_gain, avail, dtype=np.float32)
                    self._prev_gains[play_id] = target_gain

                    duck_ramp = np.linspace(self._prev_duck, duck_curr, avail, dtype=np.float32)
                    voice_signal = chunk * (gain_ramp * duck_ramp)[:, None]

                    if state["stopping"]:
                        fade_rem = state["fade_rem"]
                        fade_tot = max(1, state["fade_tot"])
                        fade_len = min(avail, fade_rem)
                        start_f = fade_rem / fade_tot
                        end_f = max(0.0, (fade_rem - fade_len) / fade_tot)
                        curve = np.linspace(start_f, end_f, fade_len, dtype=np.float32)
                        voice_signal[:fade_len] *= curve[:, None]
                        state["fade_rem"] -= fade_len
                        if state["fade_rem"] <= 0:
                            finished.append(play_id)

                    mix[:avail] += voice_signal
                    state["pos"] += avail

                    if avail < frames and voice.loop and not state["stopping"] and total > 0:
                        rem = frames - avail
                        rem_avail = min(rem, total)
                        mix[avail : avail + rem_avail] += (
                            voice.data[:rem_avail] * (target_gain * duck_curr)
                        )
                        state["pos"] = rem_avail

                    if not voice.loop and state["pos"] >= total and play_id not in finished:
                        finished.append(play_id)

                voice.update_device_pos(self.dest_key, state["pos"])

            for pid in finished:
                v = self._voices.pop(pid, None)
                self._prev_gains.pop(pid, None)
                self._voice_states.pop(pid, None)
                if v:
                    v.finish_destination(self.dest_key)

            # Master device volume ramp across block
            target_dev_vol = self.volume
            dev_vol_ramp = np.linspace(self._prev_device_vol, target_dev_vol, frames, dtype=np.float32)
            self._prev_device_vol = target_dev_vol
            mix *= dev_vol_ramp[:, None]

        self._prev_duck = duck_curr
        return apply_soft_limiter(mix)

    def _preferred_samplerate(self) -> int:
        """Mixer rate when the device supports it, else the device's native rate.

        Several endpoints (WASAPI Bluetooth headsets, some virtual devices) only
        accept 44.1 kHz. Hardcoding 48 kHz used to drop those routes silently.
        """
        try:
            import sounddevice as sd
        except Exception:
            return self.sr
        candidates = [self.sr]
        try:
            native = int(round(float(device_info(self.device_id).get("default_samplerate") or 0)))
        except (TypeError, ValueError):
            native = 0
        if native > 0 and native != self.sr:
            candidates.append(native)
        for rate in candidates:
            try:
                sd.check_output_settings(
                    device=self.device_id, samplerate=rate, channels=2, dtype="float32"
                )
                return rate
            except Exception:
                continue
        return self.sr

    def start_stream(self) -> bool:
        if self.device_id is None:
            return False
        if self._stream is not None and self._stream_running:
            return True

        # A dead handle must be dropped before reopening, never reused.
        self.stop_stream()

        try:
            import sounddevice as sd

            requested_sr = self._preferred_samplerate()
            # PortAudio may coerce the rate: the callback needs the real one.
            rate_box = {"sr": float(requested_sr)}
            status_reports = {"n": 0}
            # Set by stop_stream() so an intentional close stays silent.
            flags = {"closing": False}

            def _callback(outdata, frames, time_info, status):
                if status and status_reports["n"] < 3:
                    status_reports["n"] += 1
                    logger.warning(f"Stream {self.dest_key} (dispositivo {self.device_id}): {status}")
                rate = rate_box["sr"] or float(self.sr)
                if int(round(rate)) == self.sr:
                    outdata[:] = self.render_block(frames)
                    return
                needed = max(1, int(round(frames * self.sr / rate)))
                outdata[:] = _resample_block(self.render_block(needed), frames)

            def _finished():
                # Device reset/unplug/abort: flag the handle as dead so the next
                # playback reopens it instead of writing into a zombie stream.
                if flags["closing"]:
                    return  # deliberate stop_stream()/close(): not a fault
                self._stream_running = False
                logger.warning(
                    f"Stream do dispositivo {self.device_id} ({self.dest_key}) encerrou; "
                    "será reaberto na próxima reprodução."
                )

            stream = sd.OutputStream(
                samplerate=requested_sr,
                device=self.device_id,
                channels=2,
                dtype="float32",
                blocksize=self.blocksize,
                callback=_callback,
                finished_callback=_finished,
            )
            stream.start()
            rate_box["sr"] = float(getattr(stream, "samplerate", requested_sr) or requested_sr)
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning(
                f"Não foi possível iniciar stream no dispositivo {self.device_id} ({self.dest_key}): {exc}"
            )
            self.stop_stream()
            return False

        with self._lock:
            self._stream = stream
            self._stream_running = True
            self._stream_sr = int(round(rate_box["sr"]))
            self._stream_flags = flags
            self._last_error = None
        if self._stream_sr != self.sr:
            logger.info(
                f"Dispositivo {self.device_id} ({self.dest_key}) aberto a {self._stream_sr} Hz "
                f"(mixer a {self.sr} Hz, reamostrado no callback)."
            )
        return True

    def ensure_stream(self) -> bool:
        """Health-checks the persistent stream and reopens it after a device reset."""
        stream = self._stream
        if stream is not None and self._stream_running:
            try:
                if stream.active:
                    return True
            except Exception:
                pass
            logger.warning(
                f"Stream do dispositivo {self.device_id} ({self.dest_key}) não está ativo; reabrindo."
            )
            self._stream_running = False
        return self.start_stream()

    def stop_stream(self) -> None:
        # Cleared before closing so the finished_callback cannot race the reopen.
        self._stream_running = False
        flags = self._stream_flags
        if flags is not None:
            flags["closing"] = True  # intentional close: do not warn
        stream = self._stream
        self._stream = None
        self._stream_flags = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass


class AudioMixer:
    """High-level persistent audio mixer managing concurrent soundboard playback."""

    def __init__(self, sr: int = TARGET_SR, blocksize: int = DEFAULT_BLOCKSIZE):
        self.sr = sr
        self.blocksize = blocksize
        self.cable_mixer = DeviceMixer(sr=sr, blocksize=blocksize, dest_key="cable")
        self.monitor_mixer = DeviceMixer(sr=sr, blocksize=blocksize, dest_key="monitor")
        self._voices: dict[str, MixerVoice] = {}
        self._lock = threading.RLock()
        self._duck_provider: Callable[[], float] | None = None

    def set_duck_provider(self, provider: Callable[[], float]) -> None:
        self._duck_provider = provider
        self.cable_mixer.set_duck_provider(provider)
        self.monitor_mixer.set_duck_provider(provider)

    def _get_duck_gain(self) -> float:
        if callable(self._duck_provider):
            try:
                return float(self._duck_provider())
            except Exception:
                pass
        return 1.0

    def play_voice(
        self,
        clip: dict,
        data: np.ndarray,
        settings: dict,
        monitor_only: bool = False,
        on_finish: Callable[..., None] | None = None,
    ) -> MixerVoice | None:
        """Starts one voice. Returns None when no route could actually be opened."""
        cable_id = settings.get("cable_device_id")
        monitor_id = settings.get("monitor_device_id")
        try:
            cable_vol = float(settings.get("cable_volume", 1.0))
            monitor_vol = float(settings.get("monitor_volume", 0.75))
        except (TypeError, ValueError):
            return None

        has_cable = (not monitor_only) and cable_id is not None and cable_vol > 0.001
        has_monitor = (
            monitor_vol > 0.001
            and monitor_id is not None
            and (monitor_only or monitor_id != cable_id)
        )

        # Monitoring into a virtual cable is never audible: VB-Cable accepts the
        # second render stream and silently discards it. Reject the route instead
        # of pretending the clip is playing.
        if has_monitor and is_cable_output_device(monitor_id):
            logger.warning(
                f"Rota 'Fone' ignorada: dispositivo {monitor_id} é um cabo virtual "
                "(monitorar no cabo não gera áudio audível)."
            )
            has_monitor = False

        if not has_cable and not has_monitor:
            return None

        play_id = uuid.uuid4().hex[:8]
        clip_id = str(clip.get("id", ""))
        live_vol = float(clip.get("volume", 1.0))

        with self._lock:
            if has_cable and self.cable_mixer.device_id != int(cable_id):
                self.cable_mixer.stop_stream()
                self.cable_mixer.device_id = int(cable_id)
            if has_monitor and self.monitor_mixer.device_id != int(monitor_id):
                self.monitor_mixer.stop_stream()
                self.monitor_mixer.device_id = int(monitor_id)

            # Open/health-check the device streams first: a route that cannot be
            # opened must not count as a destination.
            destinations = set()
            if has_cable:
                self.cable_mixer.set_volume(cable_vol)
                if self.cable_mixer.ensure_stream():
                    destinations.add("cable")
            if has_monitor:
                self.monitor_mixer.set_volume(monitor_vol)
                if self.monitor_mixer.ensure_stream():
                    destinations.add("monitor")

            if not destinations:
                logger.warning("Nenhuma rota de áudio pôde ser aberta (cabo/fone indisponíveis).")
                return None

            voice = MixerVoice(
                play_id=play_id,
                clip_id=clip_id,
                data=data,
                base_gain=1.0,
                live_volume=live_vol,
                loop=False,
                monitor_only=monitor_only,
                sr=self.sr,
                title=str(clip.get("title") or "Áudio"),
                emoji=str(clip.get("emoji") or "🔊"),
                on_finish=on_finish,
            )
            voice.register_destinations(destinations, driver="cable" if "cable" in destinations else "monitor")

            self._voices[play_id] = voice
            if "cable" in destinations:
                self.cable_mixer.add_voice(voice)
            if "monitor" in destinations:
                self.monitor_mixer.add_voice(voice)

        return voice

    def get_voice(self, play_id: str) -> MixerVoice | None:
        with self._lock:
            return self._voices.get(play_id)

    def stop(self, play_id: str, fade_ms: float = 10.0) -> None:
        with self._lock:
            voice = self._voices.get(play_id)
            if voice:
                voice.stop(fade_ms=fade_ms)

    def stop_all(self, fade_ms: float = 10.0) -> None:
        with self._lock:
            for voice in list(self._voices.values()):
                voice.stop(fade_ms=fade_ms)

    def set_live_volume(self, play_id: str, volume: float) -> float | None:
        with self._lock:
            voice = self._voices.get(play_id)
            if voice:
                return voice.set_live_volume(volume)
            return None

    def shutdown(self) -> None:
        with self._lock:
            for voice in list(self._voices.values()):
                voice.stop(fade_ms=0)
            self._voices.clear()
            self.cable_mixer.stop_stream()
            self.monitor_mixer.stop_stream()
