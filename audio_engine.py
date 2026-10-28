import os
import time
import uuid
import subprocess
import tempfile
import threading
from collections import deque
from pathlib import Path
import numpy as np


def trim_and_normalize(
    audio: np.ndarray,
    sr: int,
    trim_start: float = 0.0,
    trim_end: float = 0.0,
    volume: float = 1.0,
    normalize: bool = True,
) -> np.ndarray:
    """Slices stereo/mono waveform safely, applies peak normalization, gain, and anti-click micro-fades."""
    if audio is None or len(audio) == 0:
        return np.zeros((1, 2), dtype=np.float32)

    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim == 1:
        arr = np.column_stack([arr, arr])
    elif arr.shape[1] == 1:
        arr = np.repeat(arr, 2, axis=1)
    elif arr.shape[1] > 2:
        arr = arr[:, :2]

    total_samples = len(arr)
    start_idx = max(0, min(total_samples - 1, int(round(max(0.0, trim_start) * sr))))
    if trim_end and trim_end > trim_start:
        end_idx = max(start_idx + 1, min(total_samples, int(round(trim_end * sr))))
    else:
        end_idx = total_samples

    sliced = arr[start_idx:end_idx].copy()
    if len(sliced) == 0:
        sliced = arr.copy()

    if normalize:
        peak = float(np.max(np.abs(sliced)))
        if peak > 1e-4:
            sliced = sliced * (0.92 / peak)

    gain = float(max(0.0, min(3.0, volume)))
    if abs(gain - 1.0) > 1e-3:
        sliced = sliced * gain

    # Soft limiter above 0.92 to avoid harsh digital clipping
    over = np.abs(sliced) > 0.92
    if np.any(over):
        sliced = np.tanh(sliced) * 0.96

    # 4ms anti-click fade in/out
    fade_len = min(int(sr * 0.004), len(sliced) // 4)
    if fade_len > 2:
        fade_in = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)[:, None]
        fade_out = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)[:, None]
        sliced[:fade_len] *= fade_in
        sliced[-fade_len:] *= fade_out

    return sliced


def compute_waveform_peaks(audio: np.ndarray, num_bins: int = 160) -> list[float]:
    """Computes normalized [0.0..1.0] amplitude peaks across num_bins for visual waveform rendering."""
    num_bins = max(8, int(num_bins))
    if audio is None or len(audio) == 0:
        return [0.0] * num_bins

    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim > 1:
        mono = np.max(np.abs(arr), axis=1)
    else:
        mono = np.abs(arr)

    if len(mono) < num_bins:
        padded = np.zeros(num_bins, dtype=np.float32)
        padded[: len(mono)] = mono
        mono = padded

    edges = np.linspace(0, len(mono), num_bins + 1, dtype=int)
    peaks = []
    for i in range(num_bins):
        seg = mono[edges[i] : max(edges[i] + 1, edges[i + 1])]
        peaks.append(float(np.max(seg)) if len(seg) > 0 else 0.0)

    max_p = max(peaks) if peaks else 0.0
    if max_p <= 1e-4:
        return [0.0] * num_bins
    return [round(float(p / max_p), 3) for p in peaks]


class PlaybackSession:
    """Represents an active clip playback with live pause/resume, seek, loop, and live volume controls."""

    def __init__(
        self,
        play_id: str,
        clip_id: str,
        data: np.ndarray,
        sr: int = 48000,
        monitor_only: bool = False,
        live_volume: float = 1.0,
    ):
        self.play_id = play_id
        self.clip_id = clip_id
        self.data = data
        self.sr = int(sr)
        self.total_samples = len(data)
        self.duration_s = round(float(self.total_samples / max(1, self.sr)), 2)
        self.pos_idx = 0
        self.paused = False
        self.loop = False
        self.monitor_only = bool(monitor_only)
        self.live_volume = float(live_volume)
        self.stop_event = threading.Event()
        self._lock = threading.Lock()

    def toggle_pause(self) -> bool:
        with self._lock:
            self.paused = not self.paused
            return self.paused

    def seek(self, position_s: float):
        with self._lock:
            target = int(round(max(0.0, float(position_s)) * self.sr))
            self.pos_idx = max(0, min(max(0, self.total_samples - 1), target))

    def set_loop(self, loop: bool) -> bool:
        with self._lock:
            self.loop = bool(loop)
            return self.loop

    def set_live_volume(self, volume: float) -> float:
        with self._lock:
            self.live_volume = max(0.0, min(3.0, float(volume)))
            return self.live_volume

    def restart(self):
        with self._lock:
            self.pos_idx = 0
            self.paused = False

    def stop(self):
        self.stop_event.set()

    def status(self) -> dict:
        with self._lock:
            cur_s = round(float(self.pos_idx / max(1, self.sr)), 2)
            return {
                "play_id": self.play_id,
                "clip_id": self.clip_id,
                "current_s": cur_s,
                "duration_s": self.duration_s,
                "paused": self.paused,
                "loop": self.loop,
                "live_volume": round(self.live_volume, 2),
                "monitor_only": self.monitor_only,
            }


class ReplayRingBuffer:
    """Thread-safe circular audio buffer storing the last N seconds of system/call audio."""

    def __init__(self, max_seconds: float = 15.0, sr: int = 48000, channels: int = 2):
        self.sr = int(sr)
        self.channels = int(channels)
        self.max_samples = max(1, int(round(max_seconds * self.sr)))
        self._chunks = deque()
        self._current_samples = 0
        self._lock = threading.Lock()

    def set_max_seconds(self, max_seconds: float):
        with self._lock:
            self.max_samples = max(1, int(round(max_seconds * self.sr)))
            self._trim_unlocked()

    def append(self, chunk: np.ndarray):
        if chunk is None or len(chunk) == 0:
            return
        arr = np.asarray(chunk, dtype=np.float32)
        if arr.ndim == 1:
            arr = np.column_stack([arr, arr])
        elif arr.shape[1] == 1:
            arr = np.repeat(arr, 2, axis=1)
        elif arr.shape[1] > self.channels:
            arr = arr[:, : self.channels]

        with self._lock:
            self._chunks.append(arr)
            self._current_samples += len(arr)
            self._trim_unlocked()

    def _trim_unlocked(self):
        while self._current_samples > self.max_samples and self._chunks:
            excess = self._current_samples - self.max_samples
            first = self._chunks[0]
            if len(first) <= excess:
                self._chunks.popleft()
                self._current_samples -= len(first)
            else:
                self._chunks[0] = first[excess:]
                self._current_samples -= excess

    def snapshot(self, last_seconds: float = None) -> np.ndarray:
        with self._lock:
            if not self._chunks:
                return np.zeros((0, self.channels), dtype=np.float32)
            full = np.vstack(list(self._chunks))

        if last_seconds is not None and last_seconds > 0:
            want = int(round(last_seconds * self.sr))
            if len(full) > want:
                full = full[-want:]
        return full


class AudioEngine:
    TARGET_SR = 48000

    def __init__(self, replay_seconds: float = 15.0, start_loopback: bool = True):
        self.replay_buffer = ReplayRingBuffer(max_seconds=replay_seconds, sr=self.TARGET_SR, channels=2)
        self._cache = {}
        self._active_events = {}
        self._sessions: dict[str, PlaybackSession] = {}
        self._latest_play_id: str | None = None
        self._lock = threading.Lock()
        self._mic_stop_event = None
        self._mic_thread = None
        self._loopback_stop_event = threading.Event()
        self._loopback_thread = None
        self.loopback_active = False

        if start_loopback:
            self.start_loopback_capture()

    def list_devices(self) -> dict:
        outputs = []
        inputs = []
        cable_id = None
        cable_name = ""
        default_out_id = None
        default_in_id = None

        try:
            import sounddevice as sd
            devs = sd.query_devices()
            hostapis = sd.query_hostapis()
            default_in, default_out = sd.default.device

            # Prefer MME or DirectSound/WASAPI without showing 4 duplicates of every device
            seen_out = set()
            seen_in = set()

            for idx, d in enumerate(devs):
                name = d.get("name", f"Device {idx}").strip()
                api_idx = d.get("hostapi", 0)
                api_name = hostapis[api_idx].get("name", "") if api_idx < len(hostapis) else ""
                # Prioritize MME / Windows DirectSound / WASAPI
                key_name = name.lower()

                if d.get("max_output_channels", 0) > 0:
                    if key_name not in seen_out or idx == default_out or "cable input" in key_name:
                        seen_out.add(key_name)
                        outputs.append({
                            "id": idx,
                            "name": name,
                            "hostapi": api_name,
                            "is_cable": "cable input" in key_name or "vb-audio" in key_name,
                            "is_default": idx == default_out,
                        })
                        if cable_id is None and ("cable input" in key_name or "vb-audio virtual" in key_name):
                            cable_id = idx
                            cable_name = name
                        if idx == default_out:
                            default_out_id = idx

                if d.get("max_input_channels", 0) > 0:
                    if key_name not in seen_in or idx == default_in:
                        seen_in.add(key_name)
                        inputs.append({
                            "id": idx,
                            "name": name,
                            "hostapi": api_name,
                            "is_default": idx == default_in,
                        })
                        if idx == default_in:
                            default_in_id = idx
        except Exception:
            pass

        return {
            "outputs": outputs,
            "inputs": inputs,
            "detected_cable_id": cable_id,
            "detected_cable_name": cable_name,
            "default_output_id": default_out_id,
            "default_input_id": default_in_id,
            "loopback_active": self.loopback_active,
        }

    def load_clip_stereo_48k(self, file_path: str) -> tuple[np.ndarray, float]:
        norm_key = os.path.normcase(os.path.abspath(file_path))
        try:
            mtime = os.path.getmtime(file_path)
        except OSError:
            mtime = 0

        cached = self._cache.get(norm_key)
        if cached and cached["mtime"] == mtime:
            return cached["data"], cached["duration"]

        import soundfile as sf
        tmp_wav = None
        try:
            data, sr = sf.read(file_path, dtype="float32", always_2d=True)
            if sr != self.TARGET_SR and len(data) > 0:
                new_len = max(1, int(round(len(data) * self.TARGET_SR / sr)))
                old_x = np.linspace(0.0, 1.0, len(data))
                new_x = np.linspace(0.0, 1.0, new_len)
                ch0 = np.interp(new_x, old_x, data[:, 0])
                ch1 = np.interp(new_x, old_x, data[:, 1] if data.shape[1] > 1 else data[:, 0])
                data = np.column_stack([ch0, ch1]).astype(np.float32)
            elif data.shape[1] == 1:
                data = np.repeat(data, 2, axis=1)
            elif data.shape[1] > 2:
                data = data[:, :2]
        except Exception:
            fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", file_path, "-vn",
                    "-ac", "2", "-ar", str(self.TARGET_SR),
                    "-c:a", "pcm_s16le", tmp_wav, "-loglevel", "error",
                ],
                check=True,
            )
            data, _ = sf.read(tmp_wav, dtype="float32", always_2d=True)
        finally:
            if tmp_wav and os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except Exception:
                    pass

        dur = float(len(data) / self.TARGET_SR)
        self._cache[norm_key] = {"mtime": mtime, "data": data, "duration": dur}
        return data, dur

    def _stream_session_to_device(
        self,
        session: PlaybackSession,
        device_id: int | None,
        base_gain: float,
        is_driver: bool,
        on_tick=None,
    ):
        if base_gain <= 0.001 or session.total_samples == 0:
            return
        try:
            import sounddevice as sd

            chunk_size = 1024
            last_tick = 0.0
            local_idx = 0

            with sd.OutputStream(
                samplerate=self.TARGET_SR,
                device=device_id,
                channels=2,
                dtype="float32",
                blocksize=chunk_size,
            ) as stream:
                while not session.stop_event.is_set():
                    if session.paused:
                        time.sleep(0.03)
                        continue

                    if is_driver:
                        with session._lock:
                            cur = session.pos_idx
                            if cur >= session.total_samples:
                                if session.loop:
                                    session.pos_idx = 0
                                    cur = 0
                                else:
                                    break
                            end = min(cur + chunk_size, session.total_samples)
                            raw_slice = session.data[cur:end]
                            session.pos_idx = end
                            live_v = session.live_volume
                    else:
                        with session._lock:
                            cur = session.pos_idx
                            live_v = session.live_volume
                            if cur >= session.total_samples and not session.loop:
                                break
                        # Secondary stream follows driver position closely
                        read_st = max(0, min(cur, session.total_samples - chunk_size))
                        end = min(read_st + chunk_size, session.total_samples)
                        raw_slice = session.data[read_st:end]
                        if len(raw_slice) == 0:
                            break

                    scaled = np.clip(raw_slice * float(base_gain * live_v), -1.0, 1.0).astype(np.float32)
                    stream.write(scaled)

                    if is_driver and callable(on_tick):
                        now = time.time()
                        if now - last_tick >= 0.08:
                            last_tick = now
                            try:
                                on_tick(session.status())
                            except Exception:
                                pass
        except Exception:
            pass

    def play_clip(
        self,
        clip: dict,
        settings: dict,
        on_finish=None,
        on_tick=None,
        monitor_only: bool = False,
    ) -> str:
        file_path = clip.get("file_path", "")
        if not file_path or not os.path.exists(file_path):
            return ""

        if not settings.get("overlap_audio", False):
            self.stop_all()

        raw_audio, _ = self.load_clip_stereo_48k(file_path)
        processed = trim_and_normalize(
            raw_audio,
            sr=self.TARGET_SR,
            trim_start=float(clip.get("trim_start", 0.0)),
            trim_end=float(clip.get("trim_end", 0.0)),
            volume=1.0,
            normalize=bool(settings.get("normalize_on_import", True)),
        )

        play_id = uuid.uuid4().hex[:8]
        session = PlaybackSession(
            play_id=play_id,
            clip_id=clip.get("id", ""),
            data=processed,
            sr=self.TARGET_SR,
            monitor_only=monitor_only,
            live_volume=float(clip.get("volume", 1.0)),
        )
        with self._lock:
            self._active_events[play_id] = session.stop_event
            self._sessions[play_id] = session
            self._latest_play_id = play_id

        cable_id = settings.get("cable_device_id")
        monitor_id = settings.get("monitor_device_id")
        cable_vol = float(settings.get("cable_volume", 1.0))
        monitor_vol = float(settings.get("monitor_volume", 0.75))

        def _runner():
            threads = []
            has_cable = (not monitor_only) and (cable_id is not None) and (cable_vol > 0.001)
            has_monitor = (monitor_vol > 0.001) and (monitor_only or monitor_id != cable_id)

            if has_cable:
                t_cable = threading.Thread(
                    target=self._stream_session_to_device,
                    args=(session, int(cable_id), cable_vol, True, on_tick),
                    daemon=True,
                )
                threads.append(t_cable)
                t_cable.start()

            if has_monitor:
                t_mon = threading.Thread(
                    target=self._stream_session_to_device,
                    args=(
                        session,
                        int(monitor_id) if monitor_id is not None else None,
                        monitor_vol,
                        not has_cable,
                        on_tick if not has_cable else None,
                    ),
                    daemon=True,
                )
                threads.append(t_mon)
                t_mon.start()

            for t in threads:
                t.join()

            with self._lock:
                self._active_events.pop(play_id, None)
                self._sessions.pop(play_id, None)
                if self._latest_play_id == play_id:
                    self._latest_play_id = next(iter(self._sessions.keys()), None)
            if callable(on_finish):
                try:
                    on_finish(clip.get("id"), play_id)
                except Exception:
                    pass

        threading.Thread(target=_runner, daemon=True).start()
        return play_id

    def get_session(self, play_id: str = None) -> PlaybackSession | None:
        with self._lock:
            pid = play_id or self._latest_play_id
            return self._sessions.get(pid) if pid else None

    def stop_all(self):
        with self._lock:
            events = list(self._active_events.values())
            self._active_events.clear()
            self._sessions.clear()
            self._latest_play_id = None
        for ev in events:
            ev.set()

    def set_mic_passthrough(self, enabled: bool, mic_id: int = None, cable_id: int = None, gain: float = 1.0):
        if self._mic_stop_event is not None:
            self._mic_stop_event.set()
            self._mic_stop_event = None

        if not enabled or cable_id is None:
            return

        stop_ev = threading.Event()
        self._mic_stop_event = stop_ev

        def _mic_loop():
            try:
                import sounddevice as sd

                def _callback(indata, outdata, frames, time_info, status):
                    if stop_ev.is_set():
                        raise sd.CallbackStop()
                    if indata.shape[1] == 1:
                        stereo = np.repeat(indata, 2, axis=1)
                    else:
                        stereo = indata[:, :2]
                    outdata[:] = np.clip(stereo * float(gain), -1.0, 1.0)

                with sd.Stream(
                    device=(mic_id, int(cable_id)),
                    samplerate=self.TARGET_SR,
                    blocksize=1024,
                    dtype="float32",
                    channels=(1, 2),
                    callback=_callback,
                ):
                    while not stop_ev.is_set():
                        time.sleep(0.1)
            except Exception:
                pass

        self._mic_thread = threading.Thread(target=_mic_loop, daemon=True)
        self._mic_thread.start()

    def start_loopback_capture(self):
        if self._loopback_thread and self._loopback_thread.is_alive():
            return

        self._loopback_stop_event.clear()

        def _loopback_worker():
            try:
                import pyaudiowpatch as pyaudio
                with pyaudio.PyAudio() as p:
                    wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
                    default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
                    if not default_speakers.get("isLoopbackDevice", False):
                        for loopback in p.get_loopback_device_info_generator():
                            if default_speakers["name"] in loopback["name"]:
                                default_speakers = loopback
                                break

                    src_sr = int(default_speakers["defaultSampleRate"])
                    src_ch = max(1, int(default_speakers["maxInputChannels"]))
                    chunk_frames = max(512, int(src_sr * 0.05))

                    stream = p.open(
                        format=pyaudio.paFloat32,
                        channels=src_ch,
                        rate=src_sr,
                        frames_per_buffer=chunk_frames,
                        input=True,
                        input_device_index=default_speakers["index"],
                    )
                    self.loopback_active = True
                    try:
                        while not self._loopback_stop_event.is_set():
                            raw = stream.read(chunk_frames, exception_on_overflow=False)
                            arr = np.frombuffer(raw, dtype=np.float32).reshape(-1, src_ch)
                            if src_ch == 1:
                                arr = np.repeat(arr, 2, axis=1)
                            elif src_ch > 2:
                                arr = arr[:, :2]
                            if src_sr != self.TARGET_SR and len(arr) > 0:
                                new_len = max(1, int(round(len(arr) * self.TARGET_SR / src_sr)))
                                old_x = np.linspace(0.0, 1.0, len(arr))
                                new_x = np.linspace(0.0, 1.0, new_len)
                                c0 = np.interp(new_x, old_x, arr[:, 0])
                                c1 = np.interp(new_x, old_x, arr[:, 1])
                                arr = np.column_stack([c0, c1]).astype(np.float32)
                            self.replay_buffer.append(arr)
                    finally:
                        stream.stop_stream()
                        stream.close()
            except Exception:
                self.loopback_active = False

        self._loopback_thread = threading.Thread(target=_loopback_worker, daemon=True)
        self._loopback_thread.start()

    def save_replay_wav(self, out_path: str, last_seconds: float = 15.0) -> str:
        import soundfile as sf

        snap = self.replay_buffer.snapshot(last_seconds=last_seconds)
        if len(snap) == 0:
            return ""

        # Trim leading/trailing dead silence (< -50 dB) while keeping 0.15s padding
        mono_env = np.max(np.abs(snap), axis=1)
        active_idx = np.where(mono_env > 0.003)[0]
        if len(active_idx) > 0:
            pad = int(self.TARGET_SR * 0.15)
            st = max(0, int(active_idx[0]) - pad)
            en = min(len(snap), int(active_idx[-1]) + pad)
            snap = snap[st:en]

        if len(snap) < int(self.TARGET_SR * 0.25):
            return ""

        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_path, snap, self.TARGET_SR, subtype="PCM_16")
        return str(out_path)
