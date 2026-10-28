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
        self.paused = False
        self.stopping = False
        self.fade_samples_remaining = CROSSFADE_SAMPLES_10MS
        self.fade_total_samples = CROSSFADE_SAMPLES_10MS
        self.seek_epoch = 0

        self.stop_event = threading.Event()
        self._lock = threading.RLock()
        self._notified_finish = False

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
            self.seek_epoch += 1
            self.paused = False
            self.stopping = False

    def seek_to_seconds(self, sec: float) -> float:
        with self._lock:
            target_idx = max(0, min(self.total_samples, int(round(float(sec) * self.sr))))
            self.pos_idx = target_idx
            self.seek_epoch += 1
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
    ):
        self.device_id = device_id
        self.sr = sr
        self.blocksize = blocksize
        self.volume = max(0.0, float(volume))
        self._prev_device_vol: float = self.volume
        self._voices: dict[str, MixerVoice] = {}
        self._lock = threading.RLock()
        self._prev_duck: float = 1.0
        self._prev_gains: dict[str, float] = {}
        self._duck_provider: Callable[[], float] | None = None
        self._stream = None
        self._stream_running = False

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

    def remove_voice(self, play_id: str) -> None:
        with self._lock:
            voice = self._voices.pop(play_id, None)
            self._prev_gains.pop(play_id, None)
            if voice:
                voice.trigger_finish()

    def get_voice(self, play_id: str) -> MixerVoice | None:
        with self._lock:
            return self._voices.get(play_id)

    def render_block(self, frames: int, duck_gain: float | None = None) -> np.ndarray:
        mix = np.zeros((frames, 2), dtype=np.float32)
        duck_curr = self._get_duck_gain() if duck_gain is None else float(duck_gain)

        with self._lock:
            finished = []
            for play_id, voice in list(self._voices.items()):
                with voice._lock:
                    if voice.paused:
                        continue
                    if voice.stop_event.is_set() and not voice.stopping:
                        finished.append(play_id)
                        continue

                    pos = voice.pos_idx
                    total = voice.total_samples
                    if pos >= total:
                        if voice.loop:
                            pos = 0
                            voice.pos_idx = 0
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

                    if voice.stopping:
                        fade_rem = voice.fade_samples_remaining
                        fade_tot = max(1, voice.fade_total_samples)
                        fade_len = min(avail, fade_rem)
                        start_f = fade_rem / fade_tot
                        end_f = max(0.0, (fade_rem - fade_len) / fade_tot)
                        curve = np.linspace(start_f, end_f, fade_len, dtype=np.float32)
                        voice_signal[:fade_len] *= curve[:, None]
                        voice.fade_samples_remaining -= fade_len
                        if voice.fade_samples_remaining <= 0:
                            finished.append(play_id)

                    mix[:avail] += voice_signal
                    voice.pos_idx += avail

                    if avail < frames and voice.loop and not voice.stopping and total > 0:
                        rem = frames - avail
                        rem_avail = min(rem, total)
                        mix[avail : avail + rem_avail] += (
                            voice.data[:rem_avail] * (target_gain * duck_curr)
                        )
                        voice.pos_idx = rem_avail

            for pid in finished:
                v = self._voices.pop(pid, None)
                self._prev_gains.pop(pid, None)
                if v:
                    v.trigger_finish()

            # Master device volume ramp across block
            target_dev_vol = self.volume
            dev_vol_ramp = np.linspace(self._prev_device_vol, target_dev_vol, frames, dtype=np.float32)
            self._prev_device_vol = target_dev_vol
            mix *= dev_vol_ramp[:, None]

        self._prev_duck = duck_curr
        return apply_soft_limiter(mix)

    def start_stream(self) -> bool:
        if self.device_id is None:
            return False
        if self._stream is not None and self._stream_running:
            return True

        try:
            import sounddevice as sd

            def _callback(outdata, frames, time_info, status):
                outdata[:] = self.render_block(frames)

            stream = sd.OutputStream(
                samplerate=self.sr,
                device=self.device_id,
                channels=2,
                dtype="float32",
                blocksize=self.blocksize,
                callback=_callback,
            )
            stream.start()
            self._stream = stream
            self._stream_running = True
            return True
        except Exception as exc:
            logger.warning(f"Não foi possível iniciar stream no dispositivo {self.device_id}: {exc}")
            self._stream = None
            self._stream_running = False
            return False

    def stop_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            self._stream_running = False


class AudioMixer:
    """High-level persistent audio mixer managing concurrent soundboard playback."""

    def __init__(self, sr: int = TARGET_SR, blocksize: int = DEFAULT_BLOCKSIZE):
        self.sr = sr
        self.blocksize = blocksize
        self.cable_mixer = DeviceMixer(sr=sr, blocksize=blocksize)
        self.monitor_mixer = DeviceMixer(sr=sr, blocksize=blocksize)
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
    ) -> MixerVoice:
        cable_id = settings.get("cable_device_id")
        monitor_id = settings.get("monitor_device_id")
        cable_vol = float(settings.get("cable_volume", 1.0))
        monitor_vol = float(settings.get("monitor_volume", 0.75))

        has_cable = (not monitor_only) and cable_id is not None and cable_vol > 0.001
        has_monitor = (
            monitor_vol > 0.001
            and monitor_id is not None
            and (monitor_only or monitor_id != cable_id)
        )

        play_id = uuid.uuid4().hex[:8]
        clip_id = str(clip.get("id", ""))
        live_vol = float(clip.get("volume", 1.0))

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

        with self._lock:
            self._voices[play_id] = voice
            if has_cable:
                if self.cable_mixer.device_id != int(cable_id):
                    self.cable_mixer.stop_stream()
                    self.cable_mixer.device_id = int(cable_id)
                self.cable_mixer.set_volume(cable_vol)
                self.cable_mixer.add_voice(voice)
                self.cable_mixer.start_stream()
            if has_monitor:
                if self.monitor_mixer.device_id != int(monitor_id):
                    self.monitor_mixer.stop_stream()
                    self.monitor_mixer.device_id = int(monitor_id)
                self.monitor_mixer.set_volume(monitor_vol)
                self.monitor_mixer.add_voice(voice)
                self.monitor_mixer.start_stream()

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
