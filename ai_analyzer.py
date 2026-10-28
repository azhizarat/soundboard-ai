import os
import re
import json
import time
import socket
import subprocess
import tempfile
import urllib.request
from pathlib import Path
import numpy as np

_OLLAMA_CACHE = {"checked_at": 0.0, "available": False}

VIDEOS_MODELS_DIR = Path(r"C:\Users\Usuario\Videos\.modelos_voz")

CATEGORY_STYLE = {
    "Rage": {"emoji": "🔥", "color": "#ef4444"},
    "Risada": {"emoji": "😂", "color": "#f59e0b"},
    "Meme": {"emoji": "🤡", "color": "#8b5cf6"},
    "Call": {"emoji": "🎙️", "color": "#3b82f6"},
    "Vinhetas": {"emoji": "🎵", "color": "#06b6d4"},
    "Efeito": {"emoji": "🔊", "color": "#10b981"},
    "Clássico": {"emoji": "👑", "color": "#ec4899"},
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
}

LAUGH_KEYWORDS = {
    "haha", "hehe", "hihi", "kkkk", "kakaka", "rsrs", "hahaha", "ahahaha",
}

MEME_KEYWORDS = {
    "relaxa", "relaxe", "esfolar", "esfordecar", "confia", "calma", "mentira",
    "clipa", "clipado", "olha", "mano", "cara", "véi", "vei", "bicho", "lenda",
    "mito", "cuidado", "correr", "corre", "ajuda", "deus", "jesus", "nossa",
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


def _extract_punchy_title(transcript: str, mood: str, speaker_name: str = "") -> str:
    clean = (transcript or "").strip()
    if not clean:
        base = mood.split("/")[0].strip()
        return f"{base} ({speaker_name})"[:30] if speaker_name else f"Som: {base}"[:30]

    # Split into clauses and pick the most expressive clause (exclamation, question, or last punchline)
    clauses = [c.strip() for c in re.split(r"[.!?;]+", clean) if c.strip()]
    chosen = clauses[-1] if clauses else clean
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

    short_words = words[:5]
    title = " ".join(short_words).capitalize()
    if mood == "Rage / Grito" and not title.endswith("!"):
        title = title[:29] + "!"
    return title[:30]


def _query_local_ollama(prompt: str, timeout: float = 2.0) -> dict | None:
    """Optional zero-dependency call to local Ollama if running on port 11434."""
    now = time.time()
    if now - _OLLAMA_CACHE["checked_at"] > 60.0:
        _OLLAMA_CACHE["checked_at"] = now
        try:
            with socket.create_connection(("127.0.0.1", 11434), timeout=0.12):
                _OLLAMA_CACHE["available"] = True
        except OSError:
            _OLLAMA_CACHE["available"] = False

    if not _OLLAMA_CACHE["available"]:
        return None

    try:
        payload = json.dumps({
            "model": "qwen2.5:1.5b",
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
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
    use_llm: bool = True,
) -> dict:
    """Generates catchy button title, category, emoji, color, and tags from speech + acoustic mood."""
    text_lower = (transcript or "").lower()
    words_set = set(re.findall(r"[\wÀ-ÿ]+", text_lower))

    if use_llm and transcript:
        prompt = (
            "Você nomeia botões de um Soundboard gamer em português.\n"
            f"Quem falou: {speaker_name or 'Desconhecido'}\n"
            f"Tom de voz: {mood}\n"
            f"Transcrição: \"{transcript}\"\n"
            "Responda APENAS JSON com: "
            "{\"title\": \"titulo curto (2 a 4 palavras)\", "
            "\"category\": \"Rage|Risada|Meme|Call|Efeito|Clássico\", "
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
            }

    # Smart acoustic + lexical classification
    if mood == "Risada" or bool(words_set & LAUGH_KEYWORDS) or "kkk" in text_lower or "haha" in text_lower:
        category = "Risada"
    elif mood == "Rage / Grito" or bool(words_set & RAGE_KEYWORDS):
        category = "Rage"
    elif mood == "Efeito / Meme" and not transcript.strip():
        category = "Efeito"
    elif bool(words_set & MEME_KEYWORDS) or mood == "Fala Animada" or "!" in (transcript or ""):
        category = "Meme"
    elif duration_s >= 4.5:
        category = "Clássico"
    else:
        category = "Call"

    style = CATEGORY_STYLE.get(category, CATEGORY_STYLE["Call"])
    title = _extract_punchy_title(transcript, mood, speaker_name)

    tags = [category.lower(), mood.split("/")[0].strip().lower()]
    if speaker_name:
        tags.append(speaker_name.lower())
    for kw in sorted(words_set & (RAGE_KEYWORDS | MEME_KEYWORDS | LAUGH_KEYWORDS)):
        if kw not in tags:
            tags.append(kw)

    return {
        "title": title,
        "category": category,
        "emoji": style["emoji"],
        "color": style["color"],
        "tags": tags[:6],
    }


def load_audio_mono_16k(file_path: str) -> tuple[np.ndarray, float]:
    """Loads any audio file as 16 kHz mono float32 using soundfile or ffmpeg fallback."""
    import soundfile as sf

    tmp_wav = None
    try:
        data, sr = sf.read(file_path, dtype="float32")
        if data.ndim > 1:
            data = np.mean(data, axis=1)
        dur = len(data) / max(sr, 1)
        if sr != 16000 and len(data) > 0:
            new_len = max(1, int(round(len(data) * 16000 / sr)))
            old_idx = np.linspace(0.0, 1.0, len(data))
            new_idx = np.linspace(0.0, 1.0, new_len)
            data = np.interp(new_idx, old_idx, data).astype(np.float32)
        return data, float(dur)
    except Exception:
        fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        subprocess.run(
            ["ffmpeg", "-y", "-i", file_path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp_wav, "-loglevel", "error"],
            check=True,
        )
        data, sr = sf.read(tmp_wav, dtype="float32")
        return data, float(len(data) / 16000.0)
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
    def __init__(self, models_dir: str = None):
        if models_dir and Path(models_dir).exists():
            self.models_dir = Path(models_dir)
        elif VIDEOS_MODELS_DIR.exists():
            self.models_dir = VIDEOS_MODELS_DIR
        else:
            self.models_dir = Path("data/models").resolve()
            self.models_dir.mkdir(parents=True, exist_ok=True)

        self.seg_model_path = self.models_dir / "pyannote-segmentation-3-0" / "model.onnx"
        self.emb_model_path = self.models_dir / "nemo_en_titanet_large.onnx"
        self.ref_voices_path = self.models_dir / "referencias_vozes.npz"
        self._extractor = None
        self._diarizer = None
        self._whisper = None

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

    def _get_whisper(self):
        if self._whisper is not None:
            return self._whisper
        try:
            from faster_whisper import WhisperModel
            try:
                self._whisper = WhisperModel("base", device="cuda", compute_type="float16")
            except Exception:
                self._whisper = WhisperModel("base", device="cpu", compute_type="int8")
            return self._whisper
        except Exception:
            return None

    def compute_embedding(self, audio_16k: np.ndarray) -> np.ndarray | None:
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
            return emb / (norm + 1e-8) if norm > 0 else emb
        except Exception:
            return None

    def identify_speaker(self, audio_16k: np.ndarray, threshold: float = 0.55) -> tuple[str, float]:
        if not self.ref_voices_path.exists():
            return "", 0.0
        emb = self.compute_embedding(audio_16k)
        if emb is None:
            return "", 0.0
        try:
            ref_data = np.load(self.ref_voices_path)
            best_spk = ""
            best_sim = -1.0
            for key in ref_data.files:
                vec = ref_data[key].astype(np.float32)
                vec = vec / (np.linalg.norm(vec) + 1e-8)
                sim = float(np.dot(emb, vec))
                if sim > best_sim:
                    best_sim = sim
                    best_spk = key
            if best_sim >= threshold:
                return best_spk, round(best_sim, 3)
        except Exception:
            pass
        return "", 0.0

    def enroll_speaker_reference(self, file_path: str, spk_key: str) -> bool:
        audio_16k, _ = load_audio_mono_16k(file_path)
        emb = self.compute_embedding(audio_16k)
        if emb is None:
            return False
        ref_dict = {}
        if self.ref_voices_path.exists():
            try:
                old = np.load(self.ref_voices_path)
                for k in old.files:
                    ref_dict[k] = old[k]
            except Exception:
                pass
        if spk_key in ref_dict:
            blended = 0.5 * ref_dict[spk_key] + 0.5 * emb
            ref_dict[spk_key] = blended / (np.linalg.norm(blended) + 1e-8)
        else:
            ref_dict[spk_key] = emb
        np.savez(self.ref_voices_path, **ref_dict)
        return True

    def transcribe(self, audio_16k: np.ndarray) -> str:
        model = self._get_whisper()
        if model is None or audio_16k is None or len(audio_16k) < 1600:
            return ""
        try:
            segs, _ = model.transcribe(audio_16k, language="pt", beam_size=3, vad_filter=False)
            return " ".join(s.text.strip() for s in segs).strip()
        except Exception:
            return ""

    def diarize_and_identify(self, audio_16k: np.ndarray, speaker_names: dict = None) -> list[dict]:
        """Segments audio by speaker turns using pyannote + TitaNet and matches each turn against reference voices."""
        if audio_16k is None or len(audio_16k) < 4000:
            return []
        diarizer = self._get_diarizer()
        if diarizer is None:
            return []

        sr = 16000
        total_dur = float(len(audio_16k) / sr)
        try:
            raw_segs = diarizer.process(audio_16k).sort_by_start_time()
        except Exception:
            return []

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
            return []

        # Match each local cluster centroid against referencias_vozes.npz greedily
        local_to_global = {}
        ref_dict = {}
        if self.ref_voices_path.exists():
            try:
                loaded = np.load(self.ref_voices_path)
                for k in loaded.files:
                    v = loaded[k].astype(np.float32)
                    ref_dict[k] = v / (np.linalg.norm(v) + 1e-8)
            except Exception:
                pass

        cluster_centroids = {}
        for l_spk, items in local_clusters.items():
            weights = np.array([w for w, _ in items], dtype=np.float32)[:, None]
            mat = np.vstack([e for _, e in items])
            cent = np.sum(mat * weights, axis=0)
            cent = cent / (np.linalg.norm(cent) + 1e-8)
            cluster_centroids[l_spk] = cent

        used_refs = set()
        used_locals = set()
        if ref_dict and cluster_centroids:
            pairs = []
            for l_spk, cent in cluster_centroids.items():
                for r_key, r_vec in ref_dict.items():
                    sim = float(np.dot(cent, r_vec))
                    pairs.append((sim, l_spk, r_key))
            pairs.sort(reverse=True, key=lambda x: x[0])

            for sim, l_spk, r_key in pairs:
                if sim >= 0.52 and l_spk not in used_locals and r_key not in used_refs:
                    local_to_global[l_spk] = r_key
                    used_locals.add(l_spk)
                    used_refs.add(r_key)

        # Assign remaining unmatched local clusters to new or fallback speaker IDs
        existing_nums = [
            int(k.split("_")[-1]) for k in ref_dict.keys() if "_" in k and k.split("_")[-1].isdigit()
        ]
        next_id = (max(existing_nums) + 1) if existing_nums else 1
        for l_spk in sorted(local_clusters.keys()):
            if l_spk not in local_to_global:
                new_key = f"interlocutor_{next_id}"
                while new_key in used_refs:
                    next_id += 1
                    new_key = f"interlocutor_{next_id}"
                local_to_global[l_spk] = new_key
                used_refs.add(new_key)
                next_id += 1

        mapped_Input = []
        for r in valid_raw:
            gid = local_to_global.get(r["local_spk"], "")
            if not gid and r["emb"] is not None:
                gid, _ = self.identify_speaker(audio_16k[int(r["start"] * sr) : int(r["end"] * sr)], threshold=0.50)
            mapped_Input.append({
                "start": r["start"],
                "end": r["end"],
                "dur": r["dur"],
                "speaker_id": gid or "interlocutor_1",
            })

        merged = merge_diarized_segments(mapped_Input, total_duration=total_dur)
        speaker_names = speaker_names or {}

        enriched = []
        for seg in merged:
            st_idx = max(0, int(seg["start"] * sr))
            en_idx = min(len(audio_16k), int(seg["end"] * sr))
            seg_audio = audio_16k[st_idx:en_idx]
            seg_ac = analyze_acoustics(seg_audio, sr=sr)
            seg_tx = self.transcribe(seg_audio)
            spk_id = seg["speaker_id"]
            spk_label = speaker_names.get(spk_id) or spk_id.replace("interlocutor_", "Amigo ")
            seg_meta = generate_smart_metadata(
                transcript=seg_tx,
                mood=seg_ac["mood"],
                speaker_name=spk_label,
                duration_s=seg["dur"],
                use_llm=False,
            )
            enriched.append({
                "start": seg["start"],
                "end": seg["end"],
                "dur": seg["dur"],
                "speaker_id": spk_id,
                "speaker_name": spk_label,
                "transcript": seg_tx,
                "mood": seg_ac["mood"],
                "title": seg_meta["title"],
                "category": seg_meta["category"],
                "emoji": seg_meta["emoji"],
                "color": seg_meta["color"],
            })
        return enriched

    def analyze_file(
        self,
        file_path: str,
        existing_transcript: str = None,
        existing_speaker_id: str = None,
        speaker_name: str = "",
        speaker_names: dict = None,
        use_llm: bool = True,
        skip_speaker_id: bool = False,
        forced_category: str = None,
    ) -> dict:
        audio_16k, duration_s = load_audio_mono_16k(file_path)
        acoustics = analyze_acoustics(audio_16k, sr=16000)
        speaker_names = dict(speaker_names or {})

        segments = []
        speaker_ids = []
        speaker_id = ""
        speaker_sim = 0.0

        if not skip_speaker_id:
            if not existing_speaker_id and duration_s >= 1.2:
                segments = self.diarize_and_identify(audio_16k, speaker_names=speaker_names)
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
                transcript = self.transcribe(audio_16k)

        if not speaker_name and speaker_id:
            speaker_name = speaker_names.get(speaker_id, "")

        clean_for_title = re.sub(r"\[[^\]]+\]:\s*", "", transcript)
        meta = generate_smart_metadata(
            transcript=clean_for_title,
            mood=acoustics["mood"],
            speaker_name=speaker_name if not skip_speaker_id else "",
            duration_s=duration_s,
            use_llm=use_llm,
        )
        if forced_category and forced_category in CATEGORY_STYLE:
            meta["category"] = forced_category
            meta["emoji"] = CATEGORY_STYLE[forced_category]["emoji"]
            meta["color"] = CATEGORY_STYLE[forced_category]["color"]
            if not clean_for_title.strip():
                meta["title"] = Path(file_path).stem[:28].replace("_", " ").strip().capitalize()

        return {
            "title": meta["title"],
            "category": meta["category"],
            "emoji": meta["emoji"],
            "color": meta["color"],
            "tags": meta["tags"],
            "transcript": transcript,
            "speaker_id": "" if skip_speaker_id else speaker_id,
            "speaker_ids": [] if skip_speaker_id else (speaker_ids or ([speaker_id] if speaker_id else [])),
            "segments": [] if skip_speaker_id else segments,
            "speaker_similarity": 0.0 if skip_speaker_id else speaker_sim,
            "acoustic_mood": acoustics["mood"],
            "duration": round(duration_s, 2),
            "acoustics": acoustics,
        }
