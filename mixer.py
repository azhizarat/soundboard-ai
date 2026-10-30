"""Persistent multi-voice audio mixer with device-level streams, anti-click crossfades and lookahead limiter."""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
import inspect
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Literal
from typing import Callable
import numpy as np

logger = logging.getLogger(__name__)

TARGET_SR = 48000
DEFAULT_BLOCKSIZE = 1024
CEILING_DBTP = -1.0
CEILING_LINEAR = 0.8910  # 10 ** (-1.0 / 20.0) rounded conservatively to stay <= -1.0 dBFS
CROSSFADE_SAMPLES_10MS = 480  # 10 ms at 48 kHz


@dataclass(frozen=True)
class CompletionEvent:
    play_id: str
    clip_id: str
    group_id: str | None
    reason: Literal['completed', 'cancelled', 'failed']
    error: str | None = None


def legacy_finish_adapter(callback):
    """Resolve a assinatura antes de chamar; TypeError do corpo nunca é retry."""
    if not callable(callback):
        return None
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return lambda event: callback(event.clip_id, event.play_id)
    try:
        signature.bind('clip', 'play')
    except TypeError:
        signature.bind()
        return lambda event: callback()
    return lambda event: callback(event.clip_id, event.play_id)


def notify_completion(callback, event):
    if callable(callback):
        try:
            callback(event)
        except Exception:
            logger.exception('Erro no consumidor de finalização %s', event.play_id)


@dataclass(eq=False)
class PreparedGroup:
    """Lote de PCM e rotas congeladas; só pode ser publicado uma vez."""
    group_id: str | None
    voices: tuple
    offsets: tuple
    routes: tuple
    state: str = 'prepared'
    start_samples: dict = field(default_factory=dict)

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
        group_id: str | None = None,
        on_complete: Callable[[CompletionEvent], None] | None = None,
    ):
        self.play_id = play_id
        self.group_id = group_id
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
        self._legacy_finish = legacy_finish_adapter(on_finish)
        self.on_complete = on_complete
        self.completion = None
        self._finish_reason = 'completed'

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
            self._destinations.discard(dest_key)
            finished = not self._destinations
        if finished:
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
            if (self.stopping and fade_ms > 0) or self.stop_event.is_set():
                return
            self._finish_reason = 'cancelled'
            if fade_ms > 0:
                fade_samples = max(1, int(round((fade_ms / 1000.0) * self.sr)))
                self.fade_samples_remaining = fade_samples
                self.fade_total_samples = fade_samples
                self.stopping = True
        if fade_ms <= 0:
            self.trigger_finish('cancelled')

    def trigger_finish(self, reason=None, error=None) -> None:
        with self._lock:
            if self._notified_finish:
                return
            self.stop_event.set()
            self._notified_finish = True
            self.completion = CompletionEvent(self.play_id, self.clip_id, self.group_id,
                                              reason or self._finish_reason, error)
        notify_completion(self.on_complete, self.completion)
        notify_completion(self._legacy_finish, self.completion)

    def status(self) -> dict:
        with self._lock:
            cur_s = round(float(self.pos_idx / max(1, self.sr)), 2)
            vol = round(self.live_volume, 2)
            return {
                "playing": not self.stop_event.is_set(),
                "play_id": self.play_id,
                "group_id": self.group_id,
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
        self._stream_epoch = 0
        # Retida após close: uma rota que já tentou abrir stream nativo não pode
        # aceitar a ausência de handle como uma preparação bem-sucedida.
        self._latest_stream_request = None
        self._closed = False
        self._last_error: str | None = None
        self.sample_clock = 0
        self.generation = 0
        self.on_route_lost = None

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

    def add_voice(self, voice: MixerVoice, start_sample=None) -> None:
        with self._lock:
            self._voices[voice.play_id] = voice
            self._prev_gains[voice.play_id] = voice.base_gain * voice.live_volume
            self._voice_states[voice.play_id] = {
                "pos": voice.pos_idx,
                "seek_epoch": voice.seek_epoch,
                "stopping": voice.stopping,
                "fade_rem": voice.fade_samples_remaining,
                "fade_tot": voice.fade_total_samples,
                "start_sample": self.sample_clock if start_sample is None else start_sample,
            }

    def remove_voice(self, play_id: str) -> None:
        with self._lock:
            voice = self._voices.pop(play_id, None)
            self._prev_gains.pop(play_id, None)
            self._voice_states.pop(play_id, None)
        if voice:
            voice.finish_destination(self.dest_key)

    def discard_voice(self, play_id):
        """Limpeza sem callback; a finalização já foi decidida pelo dono."""
        with self._lock:
            self._voices.pop(play_id, None)
            self._prev_gains.pop(play_id, None)
            self._voice_states.pop(play_id, None)

    def invalidate_route(self, error='Saída indisponível'):
        with self._lock:
            voices, generation = self._invalidate_locked()
        self._notify_route_lost(error, voices, generation)

    def _invalidate_locked(self):
        self.generation += 1
        self._stream_epoch += 1
        self._stream_running = False
        return list(self._voices.values()), self.generation

    def _notify_route_lost(self, error, voices, generation):
        if callable(self.on_route_lost):
            self.on_route_lost(self, error, generation)
        for voice in voices:
            self.discard_voice(voice.play_id)
            voice.trigger_finish('failed', error)

    def _detach_stream_locked(self):
        stream, flags = self._stream, self._stream_flags
        if flags is not None:
            flags['closing'] = True
        self._stream = None
        self._stream_flags = None
        voices, generation = self._invalidate_locked()
        return stream, flags, voices, generation

    @staticmethod
    def _close_handle(stream, flags):
        if flags is not None:
            flags['closing'] = True
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def configure_device(self, device_id):
        """Troca a identidade e invalida aberturas antigas na mesma transição."""
        with self._lock:
            if self._closed:
                raise ValueError('Mixer de saída encerrado')
            if self.device_id == device_id:
                return
            self.device_id = device_id
            stream, flags, voices, generation = self._detach_stream_locked()
        self._notify_route_lost('Dispositivo de saída alterado', voices, generation)
        self._close_handle(stream, flags)

    def _valid_stream_locked(self, flags):
        return (not self._closed and flags is not None and self._stream_flags is flags
                and flags['epoch'] == self._stream_epoch and flags['device_id'] == self.device_id
                and not flags['closing'] and not flags['finished'] and self._stream_running)

    def get_voice(self, play_id: str) -> MixerVoice | None:
        with self._lock:
            return self._voices.get(play_id)

    def render_block(self, frames: int, duck_gain: float | None = None, stream_flags=None) -> np.ndarray:
        mix = np.zeros((frames, 2), dtype=np.float32)
        duck_curr = self._get_duck_gain() if duck_gain is None else float(duck_gain)

        with self._lock:
            # A validação pertence ao mesmo lock que lê vozes: um callback antigo
            # nunca pode avançar o relógio ou emitir PCM da nova rota.
            if stream_flags is not None and not self._valid_stream_locked(stream_flags):
                return mix
            finished = []
            completed = []
            duck_block = np.linspace(self._prev_duck, duck_curr, frames, dtype=np.float32)
            for play_id, voice in list(self._voices.items()):
                state = self._voice_states.get(play_id)
                if state is None:
                    continue

                with voice._lock:
                    if voice.stop_event.is_set() and not state["stopping"]:
                        finished.append(play_id)
                        continue
                    offset = max(0, state['start_sample'] - self.sample_clock)
                    if voice.stopping and (offset > 0 or voice.paused):
                        finished.append(play_id)
                        continue
                    if voice.paused or offset >= frames:
                        continue
                    available_frames = frames - offset

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

                    avail = min(available_frames, total - pos)
                    chunk = voice.data[pos : pos + avail]

                    target_gain = float(voice.base_gain * voice.live_volume)
                    prev_gain = self._prev_gains.get(play_id, target_gain)
                    gain_ramp = np.linspace(prev_gain, target_gain, avail, dtype=np.float32)
                    self._prev_gains[play_id] = target_gain

                    duck_ramp = duck_block[offset:offset + avail]
                    voice_signal = chunk * (gain_ramp * duck_ramp)[:, None]

                    if state["stopping"]:
                        fade_rem = state["fade_rem"]
                        fade_tot = max(1, state["fade_tot"])
                        fade_len = min(avail, fade_rem)
                        start_f = fade_rem / fade_tot
                        end_f = max(0.0, (fade_rem - fade_len) / fade_tot)
                        curve = np.linspace(start_f, end_f, fade_len, dtype=np.float32)
                        voice_signal[:fade_len] *= curve[:, None]
                        voice_signal[fade_len:] = 0
                        state["fade_rem"] -= fade_len
                        if state["fade_rem"] <= 0:
                            finished.append(play_id)

                    mix[offset:offset + avail] += voice_signal
                    state["pos"] += avail

                    if avail < available_frames and voice.loop and not state["stopping"] and total > 0:
                        rem = available_frames - avail
                        rem_avail = min(rem, total)
                        mix[offset + avail : offset + avail + rem_avail] += (
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
                    completed.append(v)

            # Master device volume ramp across block
            target_dev_vol = self.volume
            dev_vol_ramp = np.linspace(self._prev_device_vol, target_dev_vol, frames, dtype=np.float32)
            self._prev_device_vol = target_dev_vol
            mix *= dev_vol_ramp[:, None]
            self.sample_clock += frames

        self._prev_duck = duck_curr
        for voice in completed:
            voice.finish_destination(self.dest_key)
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
        with self._lock:
            if self.device_id is None or self._closed:
                return False
            if self._stream is not None and self._valid_stream_locked(self._stream_flags):
                return True
            old_stream, old_flags, voices, generation = self._detach_stream_locked()
            requested_device = self.device_id
            epoch = self._stream_epoch
            flags = {'closing': False, 'finished': False, 'epoch': epoch, 'device_id': requested_device}
            self._latest_stream_request = flags
        self._notify_route_lost('Stream fechado ou substituído', voices, generation)
        self._close_handle(old_stream, old_flags)
        stream = None

        try:
            import sounddevice as sd

            requested_sr = self._preferred_samplerate()
            # PortAudio may coerce the rate: the callback needs the real one.
            rate_box = {"sr": float(requested_sr)}
            status_reports = {"n": 0}
            def _callback(outdata, frames, time_info, status):
                if status and status_reports["n"] < 3:
                    status_reports["n"] += 1
                    logger.warning(f"Stream {self.dest_key} (dispositivo {requested_device}): {status}")
                rate = rate_box["sr"] or float(self.sr)
                if int(round(rate)) == self.sr:
                    outdata[:] = self.render_block(frames, stream_flags=flags)
                    return
                needed = max(1, int(round(frames * self.sr / rate)))
                outdata[:] = _resample_block(self.render_block(needed, stream_flags=flags), frames)

            def _finished():
                # Device reset/unplug/abort: flag the handle as dead so the next
                # playback reopens it instead of writing into a zombie stream.
                with self._lock:
                    valid = self._valid_stream_locked(flags)
                    flags['finished'] = True
                    if not valid:
                        return
                    voices, generation = self._invalidate_locked()
                self._notify_route_lost('Stream encerrado pelo dispositivo', voices, generation)
                logger.warning(
                    f"Stream do dispositivo {requested_device} ({self.dest_key}) encerrou; "
                    "será reaberto na próxima reprodução."
                )

            stream = sd.OutputStream(
                samplerate=requested_sr,
                device=requested_device,
                channels=2,
                dtype="float32",
                blocksize=self.blocksize,
                callback=_callback,
                finished_callback=_finished,
            )
            stream.start()
            rate_box["sr"] = float(getattr(stream, "samplerate", requested_sr) or requested_sr)
        except Exception as exc:
            with self._lock:
                if epoch == self._stream_epoch:
                    self._last_error = str(exc)
            logger.warning(
                f"Não foi possível iniciar stream no dispositivo {requested_device} ({self.dest_key}): {exc}"
            )
            self._close_handle(stream, flags)
            return False

        with self._lock:
            valid = (not self._closed and epoch == self._stream_epoch and self.device_id == requested_device
                     and not flags['finished'])
            if valid:
                self._stream = stream
                self._stream_running = True
                self._stream_sr = int(round(rate_box["sr"]))
                self._stream_flags = flags
                self._last_error = None
        if not valid:
            self._close_handle(stream, flags)
            return False
        if self._stream_sr != self.sr:
            logger.info(
                f"Dispositivo {self.device_id} ({self.dest_key}) aberto a {self._stream_sr} Hz "
                f"(mixer a {self.sr} Hz, reamostrado no callback)."
            )
        return True

    def ensure_stream(self) -> bool:
        """Health-checks the persistent stream and reopens it after a device reset."""
        with self._lock:
            stream, flags = self._stream, self._stream_flags
            running = self._stream_running
        if stream is not None and running:
            try:
                if stream.active:
                    with self._lock:
                        if self._stream is stream and self._valid_stream_locked(flags):
                            return True
            except Exception:
                pass
            logger.warning(
                f"Stream do dispositivo {self.device_id} ({self.dest_key}) não está ativo; reabrindo."
            )
            with self._lock:
                if self._stream is stream:
                    self._stream_running = False
        return self.start_stream()

    def stop_stream(self) -> None:
        with self._lock:
            stream, flags, voices, generation = self._detach_stream_locked()
        self._notify_route_lost('Stream fechado ou substituído', voices, generation)
        self._close_handle(stream, flags)

    def shutdown(self):
        with self._lock:
            self._closed = True
        self.stop_stream()


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
        self._groups: list[PreparedGroup] = []
        self._closed = False
        self.cable_mixer.on_route_lost = self._route_lost
        self.monitor_mixer.on_route_lost = self._route_lost

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
        """Compatibilidade com clipe avulso; novos consumidores usam prepare/start."""
        try:
            group = self.prepare_group(None, [{'clip': clip, 'data': data}], settings,
                                       monitor_only=monitor_only, on_finish=on_finish,
                                       require_all_routes=False)
            self.start_group(group)
            return group.voices[0]
        except ValueError:
            return None

    def prepare_group(self, group_id, items, settings, monitor_only=False,
                      on_complete=None, on_finish=None, require_all_routes=True, register=None):
        """Valida o lote inteiro antes de publicar. PCM e rotas ficam congelados."""
        with self._lock:
            if self._closed:
                raise ValueError('Mixer encerrado')
        if not items:
            raise ValueError('Grupo vazio')
        voices, offsets = [], []
        legacy = legacy_finish_adapter(on_finish)
        for item in items:
            offset = item.get('offset_samples', 0)
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError('Offset deve ser um inteiro de amostras não negativo')
            data = np.array(item['data'], dtype=np.float32, copy=True)
            if data.ndim not in (1, 2) or not len(data) or not np.isfinite(data).all():
                raise ValueError('PCM vazio ou inválido')
            if data.ndim == 2 and data.shape[1] not in (1, 2):
                raise ValueError('PCM deve ser mono ou estéreo')
            clip = dict(item['clip'])
            gain = float(clip.get('volume', 1.))
            if not math.isfinite(gain) or not 0 <= gain <= 3:
                raise ValueError('Volume inválido')
            voice = MixerVoice(item.get('play_id') or uuid.uuid4().hex, str(clip.get('id', '')),
                               data, live_volume=gain, monitor_only=monitor_only, sr=self.sr,
                               title=clip.get('title'), emoji=clip.get('emoji'), group_id=group_id)
            voice.data.setflags(write=False)
            voices.append(voice)
            offsets.append(offset)
        if len({v.play_id for v in voices}) != len(voices):
            raise ValueError('play_id duplicado')
        destinations = self._prepare_routes(settings, monitor_only, require_all_routes)
        group = PreparedGroup(group_id, tuple(voices), tuple(offsets), tuple(destinations))
        def completed(event):
            # Nenhum callback de consumidor ocorre sob locks de áudio/registro.
            for device, _, _ in group.routes:
                device.discard_voice(event.play_id)
            with self._lock:
                self._voices.pop(event.play_id, None)
                if all(voice.stop_event.is_set() for voice in group.voices):
                    if group.state == 'started':
                        group.state = 'finished'
                    if group in self._groups:
                        self._groups.remove(group)
            notify_completion(on_complete, event)
            notify_completion(legacy, event)
        for voice in voices:
            voice.on_complete = completed
            voice.register_destinations({dev.dest_key for dev, _, _ in destinations},
                                        driver=destinations[0][0].dest_key)
        if callable(register):
            register(group)
        with self._lock:
            self._groups.append(group)
            closed = self._closed
        if closed:
            self.cancel_prepared(group)
            raise ValueError('Mixer encerrado durante a preparação')
        return group

    def _prepare_routes(self, settings, monitor_only, require_all_routes):
        cable_id = settings.get("cable_device_id")
        monitor_id = settings.get("monitor_device_id")
        try:
            cable_vol = float(settings.get("cable_volume", 1.0))
            monitor_vol = float(settings.get("monitor_volume", 0.75))
        except (TypeError, ValueError):
            raise ValueError('Volume de saída inválido')
        if not all(math.isfinite(v) and 0 <= v <= 3 for v in (cable_vol, monitor_vol)):
            raise ValueError('Volume de saída inválido')

        has_cable = (not monitor_only) and cable_id is not None and cable_vol > 0.001
        has_monitor = (
            monitor_vol > 0.001
            and monitor_id is not None
            and (monitor_only or monitor_id != cable_id)
        )
        if monitor_only and monitor_id == cable_id:
            has_monitor = False

        # Monitoring into a virtual cable is never audible: VB-Cable accepts the
        # second render stream and silently discards it. Reject the route instead
        # of pretending the clip is playing.
        if has_monitor and is_cable_output_device(monitor_id):
            logger.warning(
                f"Rota 'Fone' ignorada: dispositivo {monitor_id} é um cabo virtual "
                "(monitorar no cabo não gera áudio audível)."
            )
            if require_all_routes:
                raise ValueError('Monitor deve usar uma saída audível, não o cabo virtual')
            has_monitor = False

        if not has_cable and not has_monitor:
            raise ValueError('Nenhuma saída de áudio ativa')
        destinations = []
        for enabled, device, device_id, volume in (
            (has_cable, self.cable_mixer, cable_id, cable_vol),
            (has_monitor, self.monitor_mixer, monitor_id, monitor_vol),
        ):
            if not enabled:
                continue
            device.configure_device(int(device_id))
            device.set_volume(volume)
            if device.ensure_stream():
                with device._lock:
                    if device.device_id != int(device_id):
                        raise ValueError('Saída alterada durante a preparação')
                    if (device._latest_stream_request is not None or device._stream is not None) and not device._valid_stream_locked(device._stream_flags):
                        raise ValueError('Handle de saída alterado durante a preparação')
                    destinations.append((device, device.generation, int(device_id)))
            elif require_all_routes:
                raise ValueError('Saída obrigatória indisponível: ' + device.dest_key)
        if not destinations:
            raise ValueError('Nenhuma saída de áudio pôde ser aberta')
        return destinations

    def start_group(self, group, delay_samples=0):
        if isinstance(delay_samples, bool) or not isinstance(delay_samples, int) or delay_samples < 0:
            raise ValueError('Atraso deve ser um inteiro de amostras não negativo')
        # Ordem fixa: cabo -> monitor -> registro. Nenhum callback é disparado aqui.
        with ExitStack() as locks:
            for device, _, _ in group.routes:
                locks.enter_context(device._lock)
            with self._lock:
                if self._closed or group not in self._groups or group.state != 'prepared':
                    raise ValueError('Grupo já iniciado ou encerrado')
                if any(dev.generation != generation or dev.device_id != device_id
                       for dev, generation, device_id in group.routes):
                    raise ValueError('A geração da saída preparada mudou')
                if any(dev._latest_stream_request is not None and not dev._valid_stream_locked(dev._stream_flags)
                       for dev, _, _ in group.routes):
                    raise ValueError('Handle de saída preparado indisponível')
                if any(v.play_id in self._voices for v in group.voices):
                    raise ValueError('play_id já está em uso')
                group.state = 'started'
                for device, _, _ in group.routes:
                    start = device.sample_clock + delay_samples
                    group.start_samples[device.dest_key] = start
                    for voice, offset in zip(group.voices, group.offsets):
                        device.add_voice(voice, start_sample=start + offset)
                self._voices.update((v.play_id, v) for v in group.voices)
        return group

    def _route_lost(self, device, error, generation=None):
        with self._lock:
            affected = [group for group in self._groups
                        if any(route[0] is device and (generation is None or route[1] < generation)
                               for route in group.routes)]
            owners = {group.group_id for group in affected if group.group_id is not None}
            # None representa avulso, não um dono comum a todos os avulsos.
            groups = [group for group in self._groups if group in affected or group.group_id in owners]
        for group in groups:
            self.cancel_prepared(group, reason='failed', error=error)

    def cancel_prepared(self, group, reason='cancelled', error=None, fade_ms=0):
        with self._lock:
            if group not in self._groups:
                return
            prepared = group.state == 'prepared'
            group.state = reason
        for voice in group.voices:
            if reason == 'failed':
                voice.trigger_finish('failed', error)
            else:
                voice.stop(0 if prepared else fade_ms)

    def stop_group(self, group_id, fade_ms=10.0, reason='cancelled', error=None):
        if group_id is None:
            return
        with self._lock:
            groups = [group for group in self._groups if group.group_id == group_id]
        for group in groups:
            self.cancel_prepared(group, fade_ms=fade_ms, reason=reason, error=error)

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
            groups = list(self._groups)
        for group in groups:
            self.cancel_prepared(group, fade_ms=fade_ms)

    def set_live_volume(self, play_id: str, volume: float) -> float | None:
        with self._lock:
            voice = self._voices.get(play_id)
            if voice:
                return voice.set_live_volume(volume)
            return None

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self.stop_all(fade_ms=0)
        self.cable_mixer.shutdown()
        self.monitor_mixer.shutdown()
