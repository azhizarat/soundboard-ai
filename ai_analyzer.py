import os
import re
import json
import time
import socket
import subprocess
import tempfile
import threading
import uuid
from functools import wraps
import urllib.parse
import urllib.request
from pathlib import Path
import numpy as np
from audio_processing import resample_audio
from analysis_cache import AnalysisCache, DECODE_CACHE, cache_key, pcm_hash

_OLLAMA_CACHE = {"checked_at": 0.0, "available": False}
_INFERENCE_LOCK = threading.RLock()


class DiarizationError(RuntimeError):
    """Falha de análise vocal, distinta de uma execução sem segmentos de fala."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def as_dict(self):
        return {'code': self.code, 'message': str(self)}


def _serial_inference(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        # Todas as instâncias compartilham GPU/CPU, inclusive importação e replay legados.
        with _INFERENCE_LOCK:
            return method(*args, **kwargs)
    return wrapped

# Runtime overrides injected from AppConfig (see configure_runtime).
_RUNTIME = {"ffmpeg": "ffmpeg", "ollama_url": "http://127.0.0.1:11434", "ollama_model": "qwen2.5:1.5b"}


def configure_runtime(ffmpeg_path: str = None, ollama_url: str = None, ollama_model: str = None) -> None:
    """Overrides only the runtime values that were provided."""
    if ffmpeg_path:
        _RUNTIME["ffmpeg"] = str(ffmpeg_path)
    if ollama_url:
        _RUNTIME["ollama_url"] = str(ollama_url).rstrip("/")
    if ollama_model:
        _RUNTIME["ollama_model"] = str(ollama_model)

CATEGORY_STYLE = {
    "Rage": {"emoji": "🔥", "color": "#ef4444"},
    "Risada": {"emoji": "😂", "color": "#f59e0b"},
    "Meme": {"emoji": "⚡", "color": "#8b5cf6"},
    "Vinhetas": {"emoji": "🎵", "color": "#06b6d4"},
    "Call": {"emoji": "💬", "color": "#3b82f6"},
    "Efeito": {"emoji": "🎵", "color": "#06b6d4"},
    "Clássico": {"emoji": "⚡", "color": "#8b5cf6"},
}

STOPWORDS_PT = {
    "o", "a", "os", "as", "um", "uma", "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas", "por", "para", "pra", "pro", "com",
    "que", "se", "eu", "tu", "ele", "ela", "nós", "vocês", "eles", "elas",
    "é", "foi", "era", "está", "tá", "to", "tô", "né", "ai", "aí", "então",
    "mas", "ou", "como", "mais", "muito", "já", "só", "isso", "isto", "esse",
    "essa", "aqui", "ali", "lá",
}

RAGE_KEYWORDS = {
    "caralho", "porra", "puta", "merda", "cacete", "desgraça", "inferno",
    "imbecil", "idiota", "lixo", "morri", "morreu", "roubado", "hack",
    "impossível", "para", "sai", "socorro", "matou", "troll", "trolou",
    "tiltou", "tiltado", "ruim", "noob", "fudeu", "odeio", "dropei",
    "dropar", "morro", "tenta", "vsf", "tmnc", "fdp", "grrr",
}

LAUGH_KEYWORDS = {
    "haha", "hehe", "hihi", "kkkk", "kakaka", "rsrs", "hahaha", "ahahaha",
    "kkkkk", "huehue", "ksks", "kaka", "gargalhada", "chorei", "ri",
}

MEME_KEYWORDS = {
    "relaxa", "relaxe", "esfolar", "esfordecar", "confia", "calma", "mentira",
    "clipa", "clipado", "olha", "mano", "cara", "véi", "vei", "bicho", "lenda",
    "mito", "cuidado", "correr", "corre", "ajuda", "deus", "jesus", "nossa",
    "tankei", "tankou", "não tankei", "nao tankei", "suave", "esquece", "brabo",
    "braba", "mitou", "tá maluco", "ta maluco", "bora", "pode pá", "pode pa",
    "gosta", "cesar", "certeza", "vacilou", "vacilo", "papo reto",
}

VINHETA_KEYWORDS = {
    "musica", "música", "som", "beat", "trilha", "intro", "outro", "tema",
    "toque", "alarme", "efeito", "jingle", "instrumental", "vinheta", "canção",
    "toca", "soundtrack", "baixo", "guitarra", "bateria",
}


def analyze_acoustics(audio: np.ndarray, sr: int = 16000) -> dict:
    """Extracts acoustic/prosodic features and classifies vocal mood."""
    if audio is None or len(audio) == 0:
        return {
            "mood": "Calmo / Sussurro",
            "rms_db": -80.0,
            "peak_db": -80.0,
            "f0_mean": 0.0,
            "f0_std": 0.0,
            "burst_rate": 0.0,
            "duration_s": 0.0,
        }

    x = np.asarray(audio, dtype=np.float32)
    if x.ndim > 1:
        x = np.mean(x, axis=1)

    duration_s = float(len(x) / max(sr, 1))
    rms = float(np.sqrt(np.mean(x * x) + 1e-12))
    peak = float(np.max(np.abs(x)) + 1e-12)

    rms_db = float(max(-80.0, 20.0 * np.log10(rms)))
    peak_db = float(max(-80.0, 20.0 * np.log10(peak)))

    if rms < 1e-4 or peak < 1e-3:
        return {
            "mood": "Calmo / Sussurro",
            "rms_db": round(rms_db, 2),
            "peak_db": round(peak_db, 2),
            "f0_mean": 0.0,
            "f0_std": 0.0,
            "burst_rate": 0.0,
            "duration_s": round(duration_s, 2),
        }

    # Envelope analysis at 100 Hz (10 ms hop) for laughter / syllable burst detection
    hop = max(1, int(sr * 0.01))
    n_frames = len(x) // hop
    burst_rate = 0.0
    env_mod_depth = 0.0
    if n_frames >= 20:
        frames = x[: n_frames * hop].reshape(n_frames, hop)
        env = np.sqrt(np.mean(frames * frames, axis=1))
        env_mean = float(np.mean(env) + 1e-8)
        env_std = float(np.std(env))
        env_mod_depth = env_std / env_mean

        env_centered = env - np.mean(env)
        # Count positive zero crossings of smoothed envelope
        kernel = np.ones(3, dtype=np.float32) / 3.0
        smooth_env = np.convolve(env_centered, kernel, mode="same")
        peaks = 0
        thresh = 0.15 * np.max(np.abs(smooth_env))
        for i in range(1, len(smooth_env) - 1):
            if smooth_env[i] > thresh and smooth_env[i] > smooth_env[i - 1] and smooth_env[i] >= smooth_env[i + 1]:
                peaks += 1
        burst_rate = float(peaks / max(duration_s, 0.2))

    # Fundamental frequency (F0) estimation via autocorrelation on voiced frames
    frame_len = max(1, int(sr * 0.03))
    frame_step = max(1, int(sr * 0.015))
    min_lag = max(1, int(sr / 450.0))
    max_lag = max(min_lag + 1, int(sr / 80.0))

    f0_values = []
    if len(x) >= frame_len and max_lag < frame_len:
        for start in range(0, len(x) - frame_len, frame_step):
            seg = x[start : start + frame_len]
            seg_rms = np.sqrt(np.mean(seg * seg))
            if seg_rms < 0.25 * rms:
                continue
            seg = seg - np.mean(seg)
            corr = np.correlate(seg, seg, mode="full")[frame_len - 1 :]
            if corr[0] <= 1e-8:
                continue
            corr = corr / corr[0]
            sub = corr[min_lag:max_lag]
            if len(sub) > 0:
                best_idx = int(np.argmax(sub))
                if sub[best_idx] >= 0.35:
                    lag = min_lag + best_idx
                    f0_values.append(float(sr / lag))

    f0_mean = float(np.mean(f0_values)) if f0_values else 0.0
    f0_std = float(np.std(f0_values)) if len(f0_values) > 1 else 0.0
    clipping_ratio = float(np.mean(np.abs(x) >= 0.90))

    # Mood classification
    if duration_s < 0.75 and len(f0_values) < 3:
        mood = "Efeito / Meme"
    elif 3.0 <= burst_rate <= 7.0 and env_mod_depth >= 0.55 and f0_std >= 12.0:
        mood = "Risada"
    elif (rms_db >= -9.5 and (f0_mean >= 240.0 or clipping_ratio >= 0.04)) or clipping_ratio >= 0.08:
        mood = "Rage / Grito"
    elif rms_db >= -14.0 and (f0_mean >= 210.0 or f0_std >= 35.0):
        mood = "Fala Animada"
    elif rms_db <= -26.0:
        mood = "Calmo / Sussurro"
    else:
        mood = "Normal"

    return {
        "mood": mood,
        "rms_db": round(rms_db, 2),
        "peak_db": round(peak_db, 2),
        "f0_mean": round(f0_mean, 1),
        "f0_std": round(f0_std, 1),
        "burst_rate": round(burst_rate, 2),
        "duration_s": round(duration_s, 2),
    }


def _extract_punchy_title(
    transcript: str,
    mood: str,
    speaker_name: str = "",
    category: str = "Call",
) -> str:
    clean = (transcript or "").strip()
    if not clean:
        if category == "Risada":
            return f"Risada ({speaker_name})"[:30] if speaker_name else "Gargalhada"
        elif category == "Rage":
            return f"Grito ({speaker_name})"[:30] if speaker_name else "Surto na call"
        elif category == "Vinhetas":
            return "Vinheta sonora"
        elif category == "Meme":
            return f"Bordão ({speaker_name})"[:30] if speaker_name else "Pérola da call"
        base = mood.split("/")[0].strip()
        return f"{base} ({speaker_name})"[:30] if speaker_name else f"Som: {base}"[:30]

    # Split into clauses and pick the most expressive clause (exclamation, question, or first clause)
    clauses = [c.strip() for c in re.split(r"[.!?;]+", clean) if c.strip()]
    chosen = clauses[0] if clauses else clean
    for c in clauses:
        if "!" in clean or any(w in c.lower() for w in RAGE_KEYWORDS | MEME_KEYWORDS):
            chosen = c
            break

    words = re.findall(r"[\wÀ-ÿ'-]+", chosen)
    if not words:
        return clean[:28]

    # Trim leading filler words if phrase is long
    while len(words) > 4 and words[0].lower() in STOPWORDS_PT:
        words.pop(0)

    short_words = words[:4]
    title = " ".join(short_words).capitalize()
    if (mood == "Rage / Grito" or category == "Rage") and not title.endswith("!"):
        title = title[:29] + "!"
    return title[:30]


def _ollama_host_port() -> tuple[str, int]:
    """Host/port of the configured Ollama URL, falling back to 127.0.0.1:11434."""
    host = ""
    port = 11434
    try:
        parts = urllib.parse.urlsplit(_RUNTIME["ollama_url"])
        host = parts.hostname or ""
        port = int(parts.port or 11434)
    except (ValueError, TypeError):
        host, port = "", 11434
    return (host or "127.0.0.1"), port


def _query_local_ollama(prompt: str, timeout: float = 2.0) -> dict | None:
    """Optional zero-dependency call to a local Ollama endpoint."""
    host, port = _ollama_host_port()
    now = time.time()
    if now - _OLLAMA_CACHE["checked_at"] > 60.0:
        _OLLAMA_CACHE["checked_at"] = now
        try:
            with socket.create_connection((host, port), timeout=0.12):
                _OLLAMA_CACHE["available"] = True
        except OSError:
            _OLLAMA_CACHE["available"] = False

    if not _OLLAMA_CACHE["available"]:
        return None

    try:
        payload = json.dumps({
            "model": _RUNTIME["ollama_model"],
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{_RUNTIME['ollama_url']}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            parsed = json.loads(body.get("response", "{}"))
            if isinstance(parsed, dict) and parsed.get("title"):
                return parsed
    except Exception:
        return None
    return None


def generate_smart_metadata(
    transcript: str,
    mood: str,
    speaker_name: str = "",
    duration_s: float = 1.0,
    use_llm: bool = False,
    transcription_status: str = "ok",
) -> dict:
    """Generates catchy button title, category, emoji, color, and tags from speech + acoustic mood."""
    text_lower = (transcript or "").lower()
    clean_text = (transcript or "").strip()
    words_set = set(re.findall(r"[\wÀ-ÿ]+", text_lower))

    if use_llm and clean_text:
        prompt = (
            "Você nomeia botões de um Soundboard gamer em português.\n"
            f"Quem falou: {speaker_name or 'Desconhecido'}\n"
            f"Tom de voz: {mood}\n"
            f"Transcrição: \"{transcript}\"\n"
            "Responda APENAS JSON com: "
            "{\"title\": \"titulo curto (2 a 4 palavras)\", "
            "\"category\": \"Rage|Risada|Meme|Vinhetas|Call\", "
            "\"emoji\": \"🔥\", \"tags\": [\"tag1\", \"tag2\"]}"
        )
        llm_res = _query_local_ollama(prompt)
        if llm_res and llm_res.get("category") in CATEGORY_STYLE:
            cat = llm_res["category"]
            style = CATEGORY_STYLE[cat]
            return {
                "title": str(llm_res["title"])[:30],
                "category": cat,
                "emoji": llm_res.get("emoji") or style["emoji"],
                "color": style["color"],
                "tags": llm_res.get("tags") or [cat.lower(), mood.lower()],
                "metadata_source": "local_llm",
                "fallback_reason": None,
            }

    # Smart multimodal decision tree (Acoustic + Lexical + Energy)
    is_laugh = (
        mood == "Risada"
        or bool(words_set & LAUGH_KEYWORDS)
        or any(token in text_lower for token in ("kkk", "haha", "rsrs", "huehue", "ksks", "hehe", "hihi"))
    )

    is_rage = (
        mood == "Rage / Grito"
        or bool(words_set & RAGE_KEYWORDS)
    )

    transcription_available = transcription_status in ("ok", "no_speech", "provided")
    is_vinheta = transcription_available and (
        bool(words_set & VINHETA_KEYWORDS)
        or mood == "Efeito / Meme"
        or (not clean_text and duration_s >= 2.5)
    )

    is_meme = (
        bool(words_set & MEME_KEYWORDS)
        or mood == "Fala Animada"
        or (clean_text and len(clean_text) <= 18 and ("!" in transcript or "?" in transcript))
    )

    if is_vinheta and not is_rage and not is_laugh:
        category = "Vinhetas"
    elif is_laugh:
        category = "Risada"
    elif is_rage:
        category = "Rage"
    elif is_vinheta:
        category = "Vinhetas"
    elif is_meme:
        category = "Meme"
    elif not clean_text:
        category = "Vinhetas" if transcription_available else "Call"
    else:
        category = "Call"

    style = CATEGORY_STYLE.get(category, CATEGORY_STYLE["Call"])
    title = _extract_punchy_title(transcript, mood, speaker_name, category)

    tags = [category.lower(), mood.split("/")[0].strip().lower()]
    if speaker_name:
        tags.append(speaker_name.lower())
    for kw in sorted(words_set & (RAGE_KEYWORDS | MEME_KEYWORDS | LAUGH_KEYWORDS | VINHETA_KEYWORDS)):
        if kw not in tags:
            tags.append(kw)

    return {
        "title": title,
        "category": category,
        "emoji": style["emoji"],
        "color": style["color"],
        "tags": tags[:6],
        "metadata_source": "heuristic",
        "fallback_reason": "LLM local indisponível ou resposta inválida" if use_llm and clean_text else "LLM desabilitado ou texto vazio",
    }


def load_audio_mono_16k(file_path: str) -> tuple[np.ndarray, float]:
    """Loads any audio file as 16 kHz mono float32 using soundfile or ffmpeg fallback."""
    import soundfile as sf
    from clip_renderer import file_hash
    key = cache_key(file_hash(file_path), None, 'decode', 'mono16k-polyphase/1', {'ffmpeg': _RUNTIME['ffmpeg']})
    cached = DECODE_CACHE.get(key)
    if cached is not None:
        return cached

    tmp_wav = None
    try:
        data, sr = sf.read(file_path, dtype="float32")
        if data.ndim > 1:
            data = np.mean(data, axis=1)
        dur = len(data) / max(sr, 1)
        data = resample_audio(data, sr, 16000)
        result = data, float(dur)
        DECODE_CACHE.put(key, result)
        return result
    except Exception:
        fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        subprocess.run(
            [_RUNTIME["ffmpeg"], "-y", "-i", file_path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp_wav, "-loglevel", "error"],
            check=True,
        )
        data, sr = sf.read(tmp_wav, dtype="float32")
        result = data, float(len(data) / 16000.0)
        DECODE_CACHE.put(key, result)
        return result
    finally:
        if tmp_wav and os.path.exists(tmp_wav):
            try:
                os.remove(tmp_wav)
            except Exception:
                pass


def merge_diarized_segments(
    segments: list[dict],
    total_duration: float,
    max_merge_gap: float = 0.65,
    pad_start: float = 0.08,
    pad_end: float = 0.14,
) -> list[dict]:
    """Merges adjacent speech segments of the same speaker and pads boundaries without overlapping."""
    if not segments:
        return []

    ordered = sorted((dict(s) for s in segments), key=lambda x: float(x.get("start", 0.0)))
    cleaned = []
    for s in ordered:
        if cleaned and s["start"] >= cleaned[-1]["start"] and s["end"] <= cleaned[-1]["end"]:
            continue
        cleaned.append(s)

    merged = []
    for s in cleaned:
        if (
            merged
            and merged[-1].get("speaker_id") == s.get("speaker_id")
            and merged[-1].get("local_cluster_id") == s.get("local_cluster_id")
            and (float(s["start"]) - float(merged[-1]["end"])) <= max_merge_gap
        ):
            merged[-1]["end"] = max(float(merged[-1]["end"]), float(s["end"]))
            merged[-1]["dur"] = round(merged[-1]["end"] - merged[-1]["start"], 2)
        else:
            s_copy = dict(s)
            s_copy["start"] = float(s_copy["start"])
            s_copy["end"] = float(s_copy["end"])
            s_copy["dur"] = round(s_copy["end"] - s_copy["start"], 2)
            merged.append(s_copy)

    max_dur = max(float(total_duration or 0.0), max(s["end"] for s in merged))
    final_list = []
    for idx, s in enumerate(merged):
        prev_end = final_list[-1]["end"] if final_list else 0.0
        next_start = merged[idx + 1]["start"] if idx + 1 < len(merged) else max_dur
        new_start = max(0.0, max(prev_end, s["start"] - pad_start))
        new_end = min(max_dur, min(next_start, s["end"] + pad_end))
        s["start"] = round(new_start, 2)
        s["end"] = round(new_end, 2)
        s["dur"] = round(s["end"] - s["start"], 2)
        if s["dur"] >= 0.25:
            final_list.append(s)
    return final_list


class AIAnalyzer:
    def __init__(self, models_dir: str = None, ffmpeg_path: str = None, ollama_url: str = None, ollama_model: str = None):
        configure_runtime(
            ffmpeg_path=ffmpeg_path,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
        )
        if models_dir:
            # An explicit folder is authoritative even when it does not exist yet:
            # reporting the missing folder is the doctor's job, not ours.
            self.models_dir = Path(models_dir).resolve()
        else:
            self.models_dir = Path("data/models").resolve()
            self.models_dir.mkdir(parents=True, exist_ok=True)

        self.seg_model_path = self.models_dir / "pyannote-segmentation-3-0" / "model.onnx"
        self.emb_model_path = self.models_dir / "nemo_en_titanet_large.onnx"
        self.ref_voices_path = self.models_dir / "referencias_vozes.npz"
        self._extractor = None
        self._diarizer = None
        self._whisper = None
        self.voice_profiles = None
        self._extractor_fingerprint = None
        self.cache = AnalysisCache()
        self.transcription_config = {'model': 'base', 'device': 'cuda', 'beam_size': 3,
                                     'language': 'pt', 'hotwords': '', 'initial_prompt': '', 'vad_filter': False}
        self.transcription_runtime = {'model': 'base', 'device': None, 'compute_type': None,
                                      'fallback_reason': None}

    @_serial_inference
    def configure_transcription(self, options):
        previous = dict(self.transcription_config)
        self.transcription_config.update({k: v for k, v in options.items() if k in previous})
        self.transcription_config['beam_size'] = max(1, min(10, int(self.transcription_config['beam_size'])))
        if any(previous[k] != self.transcription_config[k] for k in ('model', 'device')):
            self._whisper = None
            self.transcription_runtime = {'model': self.transcription_config['model'], 'device': None,
                                          'compute_type': None, 'fallback_reason': None}

    @_serial_inference
    def _get_extractor(self):
        if self._extractor is not None:
            return self._extractor
        if not self.emb_model_path.exists():
            return None
        try:
            import sherpa_onnx
            cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self.emb_model_path),
                num_threads=2,
            )
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
            return self._extractor
        except Exception:
            return None

    @_serial_inference
    def _get_diarizer(self):
        if self._diarizer is not None:
            return self._diarizer
        if not self.seg_model_path.exists() or not self.emb_model_path.exists():
            return None
        try:
            import sherpa_onnx
            diar_config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(self.seg_model_path)),
                    num_threads=2,
                ),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(self.emb_model_path), num_threads=2),
                clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.32),
                min_duration_on=0.20,
                min_duration_off=0.10,
            )
            self._diarizer = sherpa_onnx.OfflineSpeakerDiarization(diar_config)
            return self._diarizer
        except Exception:
            return None

    @_serial_inference
    def _get_whisper(self):
        if self._whisper is not None:
            return self._whisper
        try:
            from faster_whisper import WhisperModel
            try:
                device = self.transcription_config['device']
                compute = 'float16' if device == 'cuda' else 'int8'
                self._whisper = WhisperModel(self.transcription_config['model'], device=device, compute_type=compute, local_files_only=True)
                self.transcription_runtime.update(model=self.transcription_config['model'], device=device, compute_type=compute)
            except Exception as exc:
                self.transcription_runtime['fallback_reason'] = str(exc)
                self._whisper = WhisperModel(self.transcription_config['model'], device="cpu", compute_type="int8", local_files_only=True)
                self.transcription_runtime.update(model=self.transcription_config['model'], device='cpu', compute_type='int8')
            return self._whisper
        except Exception:
            return None

    @_serial_inference
    def compute_embedding(self, audio_16k: np.ndarray) -> np.ndarray | None:
        if audio_16k is None or len(audio_16k) < 3200:
            return None
        key = cache_key(pcm_hash(audio_16k), [0, len(audio_16k)], 'embedding', self.extractor_version, {'sr': 16000})
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        extractor = self._get_extractor()
        if extractor is None or audio_16k is None or len(audio_16k) < 3200:
            return None
        try:
            stream = extractor.create_stream()
            stream.accept_waveform(sample_rate=16000, waveform=audio_16k)
            stream.input_finished()
            if not extractor.is_ready(stream):
                return None
            emb = np.array(extractor.compute(stream), dtype=np.float32)
            norm = float(np.linalg.norm(emb))
            result = emb / (norm + 1e-8) if norm > 0 else emb
            self.cache.put(key, result)
            return result
        except Exception:
            return None

    @property
    def extractor_version(self):
        if self._extractor_fingerprint is None:
            from clip_renderer import file_hash
            self._extractor_fingerprint = ('titanet:sha256:' + file_hash(self.emb_model_path)
                                           if self.emb_model_path.exists() else 'titanet:unavailable')
        return self._extractor_fingerprint

    def identify_speaker(self, audio_16k: np.ndarray, threshold: float = 0.55) -> tuple[str, float]:
        emb = self.compute_embedding(audio_16k)
        if emb is None or self.voice_profiles is None:
            return "", 0.0
        decision = self.voice_profiles.match(emb, self.extractor_version)
        if decision['status'] == 'identified':
            return decision['speaker_id'], round(decision['candidates'][0]['score'], 3)
        return "", 0.0

    def extract_speaker_reference(self, file_path: str, spk_key: str, trim_start: float = 0.0, trim_end: float = 0.0):
        if not spk_key or not spk_key.strip():
            raise ValueError("Selecione uma pessoa para cadastrar a voz")
        audio_16k, duration = load_audio_mono_16k(file_path)
        start, end = float(trim_start), float(trim_end)
        if not np.isfinite(start) or not np.isfinite(end) or start < 0 or end < 0:
            raise ValueError("Recorte de áudio inválido")
        end = min(end, duration) if end > 0 else duration
        if start >= end:
            raise ValueError("Recorte de áudio vazio")
        audio_16k = audio_16k[int(start * 16000):int(end * 16000)]
        emb = self.compute_embedding(audio_16k)
        if emb is None:
            return False
        # Cadastro persistente exige ação/origem em VoiceLearningAPI.
        return emb

    @_serial_inference
    def transcribe_result(self, audio_16k: np.ndarray) -> dict:
        """Distingue ausência de fala, modelo indisponível e falha na inferência."""
        model = self._get_whisper()
        if model is None:
            return {"transcript": "", "status": "model_unavailable", "error": "Modelo de transcrição indisponível"}
        if audio_16k is None or len(audio_16k) < 1600:
            return {"transcript": "", "status": "no_speech", "error": None}
        try:
            segs = self._transcription_segments(audio_16k, model)
            transcript = " ".join(s.text.strip() for s in segs).strip()
            return {"transcript": transcript, "status": "ok" if transcript else "no_speech", "error": None}
        except Exception as exc:
            return {"transcript": "", "status": "inference_error", "error": f"Falha na transcrição: {exc}"}

    @_serial_inference
    def transcribe_temporal(self, audio_16k, analysis_id, source_id, interval):
        from importlib.metadata import version
        from transcript_model import from_segments
        model = self._get_whisper()
        params = {k: v for k, v in self.transcription_config.items() if k not in ('model', 'device')}
        metadata = {**self.transcription_runtime, 'model_version': 'faster-whisper/' + version('faster-whisper'),
                    'parameters': {**params, 'word_timestamps': True}, 'error': None,
                    'suggestion_provenance': {'text': 'local_asr', 'correction_applied': False}}
        if model is None:
            return {**from_segments([], analysis_id, source_id, interval, metadata), 'status': 'failed',
                    'error': {'code': 'model_unavailable', 'message': 'Modelo local de transcrição indisponível'}}
        try:
            segments = self._transcription_segments(audio_16k, model)
            result = from_segments(segments, analysis_id, source_id, interval, metadata)
            result['status'] = 'ready' if result['text'] else 'no_speech'
            return result
        except Exception as exc:
            return {**from_segments([], analysis_id, source_id, interval, metadata), 'status': 'failed',
                    'error': {'code': 'inference_error', 'message': str(exc)}}

    def _transcription_segments(self, audio, model):
        from importlib.metadata import version
        # Conteúdo é o PCM da janela; IDs editoriais/perfis não alteram ASR.
        model_path = Path(self.transcription_config['model'])
        files = [model_path / name for name in ('model.bin', 'config.json', 'tokenizer.json')]
        stamp = [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files if p.is_file()]
        identity = {'package': version('faster-whisper'), 'local_files': stamp,
                    'runtime': self.transcription_runtime}
        key = cache_key(pcm_hash(audio), [0, len(audio)], 'asr', identity, self.transcription_config)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        params = {k: v for k, v in self.transcription_config.items() if k not in ('model', 'device')}
        if params.get('vad_filter'):
            params['vad_parameters'] = {'min_silence_duration_ms': 500, 'speech_pad_ms': 400}
        segments, _ = model.transcribe(audio, **params, word_timestamps=True)
        segments = list(segments)
        self.cache.put(key, segments)
        return segments

    def transcribe(self, audio_16k: np.ndarray) -> str:
        """Mantém o contrato textual para consumidores existentes."""
        return self.transcribe_result(audio_16k)["transcript"]

    @_serial_inference
    def diarize_and_identify(self, audio_16k: np.ndarray, speaker_names: dict = None, transcribe_segments=True) -> list[dict]:
        """Segments audio by speaker turns using pyannote + TitaNet and matches each turn against reference voices."""
        if audio_16k is None or len(audio_16k) < 4000:
            raise DiarizationError('insufficient_audio', 'Áudio insuficiente para identificar pessoas: use pelo menos 250 ms')
        diarizer = self._get_diarizer()
        if diarizer is None:
            raise DiarizationError('model_unavailable', 'Modelo local de pessoas indisponível')

        sr = 16000
        total_dur = float(len(audio_16k) / sr)
        try:
            raw_segs = diarizer.process(audio_16k).sort_by_start_time()
        except Exception as exc:
            raise DiarizationError('inference_error', f'Falha na diarização: {exc}') from exc

        if not raw_segs:
            return []

        # Group segments by local pyannote cluster to compute a robust centroid per local speaker
        local_clusters = {}
        valid_raw = []
        for s in raw_segs:
            dur = float(s.end - s.start)
            if dur < 0.25:
                continue
            chunk = audio_16k[int(s.start * sr) : int(s.end * sr)]
            emb = self.compute_embedding(chunk)
            valid_raw.append({"start": float(s.start), "end": float(s.end), "dur": dur, "local_spk": s.speaker, "emb": emb})
            if emb is not None:
                local_clusters.setdefault(s.speaker, []).append((dur, emb))

        if not valid_raw:
            raise DiarizationError('insufficient_speech', 'Fala detectada, mas nenhum trecho tem duração suficiente para identificar pessoas')

        cluster_centroids = {}
        for l_spk, items in local_clusters.items():
            weights = np.array([w for w, _ in items], dtype=np.float32)[:, None]
            mat = np.vstack([e for _, e in items])
            cent = np.sum(mat * weights, axis=0)
            cent = cent / (np.linalg.norm(cent) + 1e-8)
            cluster_centroids[l_spk] = cent

        from speaker_matching import match_embedding
        capture_id = uuid.uuid4().hex
        decisions = {local: (self.voice_profiles.match(centroid, self.extractor_version)
                            if self.voice_profiles else match_embedding(centroid, {}))
                     for local, centroid in cluster_centroids.items()}
        mapped_Input = []
        for r in valid_raw:
            decision = decisions.get(r['local_spk'], match_embedding([], {}))
            centroid = cluster_centroids.get(r['local_spk'])
            mapped_Input.append({
                "start": r["start"],
                "end": r["end"],
                "dur": r["dur"],
                "speaker_id": decision['speaker_id'],
                "local_cluster_id": f"local:{capture_id}:{r['local_spk']}",
                "identity": decision,
                "voice_embedding": centroid.tolist() if centroid is not None else None,
                "extractor_version": self.extractor_version,
                "embedding_intervals": [[item['start'], item['end']] for item in valid_raw
                                        if item['local_spk'] == r['local_spk'] and item['emb'] is not None],
            })

        merged = merge_diarized_segments(mapped_Input, total_duration=total_dur)
        if not transcribe_segments:
            return merged
        speaker_names = speaker_names or {}

        enriched = []
        for seg in merged:
            st_idx = max(0, int(seg["start"] * sr))
            en_idx = min(len(audio_16k), int(seg["end"] * sr))
            seg_audio = audio_16k[st_idx:en_idx]
            seg_ac = analyze_acoustics(seg_audio, sr=sr)
            seg_result = self.transcribe_result(seg_audio)
            seg_tx = seg_result["transcript"]
            spk_id = seg["speaker_id"]
            spk_label = speaker_names.get(spk_id) or spk_id.replace("interlocutor_", "Amigo ")
            seg_meta = generate_smart_metadata(
                transcript=seg_tx,
                mood=seg_ac["mood"],
                speaker_name=spk_label,
                duration_s=seg["dur"],
                transcription_status=seg_result["status"],
                use_llm=False,
            )
            enriched.append({
                **seg,
                "start": seg["start"],
                "end": seg["end"],
                "dur": seg["dur"],
                "speaker_id": spk_id,
                "speaker_name": spk_label,
                "transcript": seg_tx,
                "transcription_status": seg_result["status"],
                "transcription_error": seg_result["error"],
                "mood": seg_ac["mood"],
                "title": seg_meta["title"],
                "category": seg_meta["category"],
                "emoji": seg_meta["emoji"],
                "color": seg_meta["color"],
            })
        return enriched

    @_serial_inference
    def analyze_file(
        self,
        file_path: str,
        existing_transcript: str = None,
        existing_speaker_id: str = None,
        speaker_name: str = "",
        speaker_names: dict = None,
        use_llm: bool = False,
        skip_speaker_id: bool = False,
        forced_category: str = None,
    ) -> dict:
        try:
            audio_16k, duration_s = load_audio_mono_16k(file_path)
        except Exception as exc:
            return {"analysis_status": "decode_error", "transcription_status": "not_run",
                    "transcript": "", "error": f"Falha ao decodificar áudio: {exc}", "warnings": []}
        acoustics = analyze_acoustics(audio_16k, sr=16000)
        speaker_names = dict(speaker_names or {})

        segments = []
        speaker_ids = []
        speaker_id = ""
        speaker_sim = 0.0
        diarization_error = None

        if not skip_speaker_id:
            if not existing_speaker_id and duration_s >= 1.2:
                try:
                    segments = self.diarize_and_identify(audio_16k, speaker_names=speaker_names)
                except DiarizationError as exc:
                    # Importação continua com texto/acústica, mas sem esconder a falha vocal.
                    diarization_error = exc.as_dict()
                for seg in segments:
                    sid = seg.get("speaker_id", "")
                    if sid and sid not in speaker_ids:
                        speaker_ids.append(sid)

            speaker_id = existing_speaker_id or (speaker_ids[0] if speaker_ids else "")
            speaker_sim = 1.0 if speaker_id else 0.0
            if not speaker_id:
                speaker_id, speaker_sim = self.identify_speaker(audio_16k)
                if speaker_id:
                    speaker_ids = [speaker_id]

        transcription = {"status": "provided", "error": None}
        transcript = (existing_transcript or "").strip()
        if not transcript:
            if len(segments) > 1 and len(speaker_ids) > 1:
                parts = []
                for seg in segments:
                    tx = (seg.get("transcript") or "").strip()
                    if tx:
                        lbl = speaker_names.get(seg["speaker_id"]) or seg["speaker_id"].replace("interlocutor_", "Amigo ")
                        parts.append(f"[{lbl}]: {tx}")
                transcript = " | ".join(parts)
            elif len(segments) == 1 and segments[0].get("transcript"):
                transcript = segments[0]["transcript"]

            if not transcript:
                transcription = self.transcribe_result(audio_16k)
                transcript = transcription["transcript"]

        if not speaker_name and speaker_id:
            speaker_name = speaker_names.get(speaker_id, "")

        clean_for_title = re.sub(r"\[[^\]]+\]:\s*", "", transcript)
        meta = generate_smart_metadata(
            transcript=clean_for_title,
            mood=acoustics["mood"],
            speaker_name=speaker_name if not skip_speaker_id else "",
            duration_s=duration_s,
            transcription_status=transcription["status"],
            use_llm=use_llm,
        )
        if forced_category and forced_category in CATEGORY_STYLE:
            meta["category"] = forced_category
            meta["emoji"] = CATEGORY_STYLE[forced_category]["emoji"]
            meta["color"] = CATEGORY_STYLE[forced_category]["color"]
            if not clean_for_title.strip():
                meta["title"] = Path(file_path).stem[:28].replace("_", " ").strip().capitalize()

        warnings = list(dict.fromkeys(
            ([transcription["error"]] if transcription["error"] else [])
            + ([diarization_error['message']] if diarization_error else [])
            + [seg["transcription_error"] for seg in segments if seg.get("transcription_error")]
        ))
        return {
            "title": meta["title"],
            "category": meta["category"],
            "emoji": meta["emoji"],
            "color": meta["color"],
            "tags": meta["tags"],
            "transcript": transcript,
            "analysis_status": "partial" if warnings else "ok",
            "transcription_status": transcription["status"],
            "transcription_error": transcription["error"],
            "diarization_error": diarization_error,
            "error": None,
            "warnings": warnings,
            "speaker_id": "" if skip_speaker_id else speaker_id,
            "speaker_ids": [] if skip_speaker_id else (speaker_ids or ([speaker_id] if speaker_id else [])),
            "segments": [] if skip_speaker_id else segments,
            "speaker_similarity": 0.0 if skip_speaker_id else speaker_sim,
            "acoustic_mood": acoustics["mood"],
            "duration": round(duration_s, 2),
            "acoustics": acoustics,
            "suggestion_provenance": {'text': 'human_provided' if existing_transcript else 'local_asr',
                                      'acoustics': 'signal_heuristic/1',
                                      'editorial_category': 'human' if forced_category else meta['metadata_source'],
                                      'correction_applied': False},
        }
