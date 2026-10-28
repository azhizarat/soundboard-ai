# Soundboard AI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a low-latency Windows desktop digital soundboard (`soundboard-ai`) with dual-output routing (`CABLE Input` + local monitor), optional microphone passthrough, WASAPI loopback instant call replay ("Clipar Call"), local AI speaker recognition + transcription + acoustic mood analysis + smart naming/categorization, global hotkeys, and a StreamDeck-style PyWebView UI with mini-overlay mode.

**Architecture:** Modular single-process Python 3.10+ application managed by `uv`. Four backend modules (`store.py`, `ai_analyzer.py`, `audio_engine.py`, `hotkeys.py`) expose a unified `SoundboardBridge` in `main.py` to a modern vanilla HTML/CSS/JS frontend rendered via `pywebview`.

**Tech Stack:** Python 3.10+, `uv`, `sounddevice`, `soundfile`, `pyaudiowpatch`, `numpy`, `sherpa-onnx`, `faster-whisper`, `keyboard`, `pywebview`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-10-28-soundboard-design.md`

## Global Constraints

- Target Platform: Windows 10/11 (Python 3.10+, managed via `uv`).
- Reuse voice models from `C:\Users\Usuario\Videos\.modelos_voz` (`pyannote-segmentation-3-0/model.onnx`, `nemo_en_titanet_large.onnx`, `referencias_vozes.npz`) when available, falling back to `data/models/`.
- Dual audio playback at `48000 Hz` `float32` stereo on `CABLE Input` and local monitor device with independent volumes.
- Atomic state persistence in `data/soundboard.json` and audio clips in `data/clips/`.

## Review Focus

- Silent or near-silent audio waveform passed to acoustic analysis or normalization must not divide by zero or return `NaN`/`Inf` values.
- Importing a `separados` folder where `mapa_de_falas.json` references a missing `.mp3` file must skip the missing file gracefully without aborting the batch import.
- Audio trimming where `trim_end <= trim_start` or `trim_end > duration` must clamp safely to valid sample bounds and return a non-empty array.
- Re-importing the same clip path or `mapa_de_falas.json` entry twice must update/deduplicate instead of creating duplicate buttons.
- Disconnected or missing `CABLE Input` device id must fall back without raising an unhandled `PortAudioError` on play.

---

### Task 1: Project Scaffolding & State Store (`store.py`)

**Files:**
- Create: `pyproject.toml`
- Create: `store.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Produces:
  - `SoundboardStore(data_dir: str = "data")`
  - `SoundboardStore.add_or_update_clip(clip_data: dict) -> dict`
  - `SoundboardStore.delete_clip(clip_id: str) -> bool`
  - `SoundboardStore.update_speaker(spk_id: str, name: str, color: str = None, avatar: str = None) -> dict`
  - `SoundboardStore.search_clips(query: str = "", category: str = "Todos", speaker_id: str = "Todos", favorites_only: bool = False) -> list[dict]`
  - `SoundboardStore.import_separados_manifest(separados_dir: str, analyzer_fn=None) -> list[dict]`
  - `SoundboardStore.update_settings(updates: dict) -> dict`

- [ ] **Step 1: Write the failing store tests in `tests/test_core.py`**

```python
import json
from pathlib import Path
from store import SoundboardStore

def test_store_crud_and_deduplication(tmp_path):
    store = SoundboardStore(data_dir=str(tmp_path))
    c1 = store.add_or_update_clip({
        "title": "Relaxa",
        "file_path": "clips/test1.mp3",
        "category": "Call",
        "speaker_id": "interlocutor_1",
        "transcript": "Não vai esfolar, relaxe.",
    })
    assert c1["id"]
    # Re-importing same file_path updates existing clip instead of duplicating
    c2 = store.add_or_update_clip({
        "title": "Relaxa Editado",
        "file_path": "clips/test1.mp3",
        "category": "Meme",
    })
    assert c1["id"] == c2["id"]
    assert len(store.state["clips"]) == 1
    assert store.search_clips(query="esfolar")[0]["title"] == "Relaxa Editado"

def test_import_separados_skips_missing_files(tmp_path):
    sep_dir = tmp_path / "separados"
    (sep_dir / "interlocutor_1").mkdir(parents=True)
    valid_mp3 = sep_dir / "interlocutor_1" / "01.mp3"
    valid_mp3.write_bytes(b"RIFF_FAKE_AUDIO")
    manifest = [
        {"interlocutor": 1, "arquivo_trecho": "interlocutor_1/01.mp3", "duracao_s": 2.0, "transcricao": "Oi tudo bem"},
        {"interlocutor": 2, "arquivo_trecho": "interlocutor_2/missing.mp3", "duracao_s": 1.5, "transcricao": "Sumiu"},
    ]
    (sep_dir / "mapa_de_falas.json").write_text(json.dumps(manifest), encoding="utf-8")
    store = SoundboardStore(data_dir=str(tmp_path / "data"))
    imported = store.import_separados_manifest(str(sep_dir))
    assert len(imported) == 1
    assert imported[0]["speaker_id"] == "interlocutor_1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'store'`

- [ ] **Step 3: Implement `pyproject.toml` and `store.py`**

Create `pyproject.toml` with dependencies (`numpy`, `sounddevice`, `soundfile`, `pyaudiowpatch`, `sherpa-onnx`, `faster-whisper`, `keyboard`, `pywebview`, `huggingface_hub`, `pytest`) and implement `SoundboardStore` in `store.py` with atomic JSON writes, speaker profiles (`interlocutor_1`..`N`), categories (`Rage`, `Risada`, `Meme`, `Call`, `Efeito`, `Clássico`), search/filtering, and `import_separados_manifest`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml store.py tests/test_core.py
git commit -m "feat: add SoundboardStore state persistence and separados importer"
```

---

### Task 2: Local AI Analyzer (`ai_analyzer.py`)

**Files:**
- Create: `ai_analyzer.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Produces:
  - `analyze_acoustics(audio: np.ndarray, sr: int = 16000) -> dict` returning `{"mood": str, "rms_db": float, "peak_db": float, "f0_mean": float, "f0_std": float, "burst_rate": float, "duration_s": float}`
  - `generate_smart_metadata(transcript: str, mood: str, speaker_name: str = "", duration_s: float = 1.0, use_llm: bool = True) -> dict` returning `{"title": str, "category": str, "emoji": str, "color": str, "tags": list[str]}`
  - `AIAnalyzer(models_dir: str = None)` with `.analyze_file(file_path: str, existing_transcript: str = None, existing_speaker_id: str = None, speaker_names: dict = None) -> dict` and `.enroll_speaker_reference(file_path: str, spk_key: str) -> bool`

- [ ] **Step 1: Write the failing AI & acoustic analysis tests in `tests/test_core.py`**

```python
import numpy as np
from ai_analyzer import analyze_acoustics, generate_smart_metadata

def test_analyze_acoustics_handles_silence_and_moods():
    sr = 16000
    # 1. Pure silence must not produce NaN/Inf
    silence = np.zeros(sr, dtype=np.float32)
    res_sil = analyze_acoustics(silence, sr)
    assert np.isfinite(res_sil["rms_db"])
    assert res_sil["mood"] == "Calmo / Sussurro"

    # 2. High-amplitude clipped signal -> Rage / Grito
    t = np.linspace(0, 1.5, int(1.5 * sr), endpoint=False)
    rage_wave = np.clip(0.95 * np.sin(2 * np.pi * 340 * t), -1.0, 1.0).astype(np.float32)
    res_rage = analyze_acoustics(rage_wave, sr)
    assert res_rage["mood"] in ("Rage / Grito", "Fala Animada")

    # 3. Rhythmic 4.5 Hz laughter bursts
    env = (0.5 * (1 + np.sin(2 * np.pi * 4.5 * t))) ** 3
    laugh_wave = (0.6 * env * np.sin(2 * np.pi * 280 * (1 + 0.15 * np.sin(2 * np.pi * 9 * t)) * t)).astype(np.float32)
    res_laugh = analyze_acoustics(laugh_wave, sr)
    assert res_laugh["mood"] == "Risada"

def test_generate_smart_metadata_ptbr():
    meta = generate_smart_metadata(
        transcript="Não vai esfolar, relaxa cara!",
        mood="Calmo / Sussurro",
        speaker_name="João",
        duration_s=2.0,
        use_llm=False,
    )
    assert 2 <= len(meta["title"]) <= 32
    assert meta["category"] in ("Rage", "Risada", "Meme", "Call", "Efeito", "Clássico")
    assert meta["emoji"]
    assert meta["color"].startswith("#")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_analyzer'`

- [ ] **Step 3: Implement `ai_analyzer.py`**

Implement:
- `analyze_acoustics(audio, sr)`: RMS/peak in dBFS (clamped at `-80.0` dB on silence), autocorrelation pitch ($F_0$ in 80–450 Hz), 3.5–6.5 Hz amplitude-envelope burst detector for laughter, and classification into `Rage / Grito`, `Risada`, `Calmo / Sussurro`, `Efeito / Meme`, `Fala Animada`, or `Normal`.
- `generate_smart_metadata(...)`: Checks local Ollama (`http://localhost:11434/api/generate`) or `llama_cpp` if available when `use_llm=True`, and falls back to a deterministic PT-BR punchline extractor + sentiment/mood classifier producing concise button titles, category, emoji, color, and tags.
- `AIAnalyzer`: Loads `nemo_en_titanet_large.onnx` and `referencias_vozes.npz` from `C:\Users\Usuario\Videos\.modelos_voz` (or `data/models`), identifies speaker via cosine similarity ($\ge 0.55$), runs `faster-whisper` (CUDA `float16` with automatic CPU `int8` fallback), and supports enrolling new speaker centroids back to `referencias_vozes.npz`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ai_analyzer.py tests/test_core.py
git commit -m "feat: add local AI analyzer with TitaNet speaker ID, Whisper, acoustic mood, and smart naming"
```

---

### Task 3: Dual-Output Audio Engine, Normalization & Replay Buffer (`audio_engine.py`)

**Files:**
- Create: `audio_engine.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Produces:
  - `trim_and_normalize(audio: np.ndarray, sr: int, trim_start: float = 0.0, trim_end: float = 0.0, volume: float = 1.0, normalize: bool = True) -> np.ndarray`
  - `ReplayRingBuffer(max_seconds: float = 15.0, sr: int = 48000, channels: int = 2)` with `.append(chunk: np.ndarray)` and `.snapshot(last_seconds: float = None) -> np.ndarray`
  - `AudioEngine` with `.list_devices() -> dict`, `.play_clip(clip: dict, settings: dict) -> str`, `.stop_all()`, `.set_mic_passthrough(enabled: bool, mic_id: int = None, cable_id: int = None, gain: float = 1.0)`, `.save_replay_wav(out_path: str, last_seconds: float = 15.0) -> str`

- [ ] **Step 1: Write the failing audio engine tests in `tests/test_core.py`**

```python
import numpy as np
from audio_engine import trim_and_normalize, ReplayRingBuffer

def test_trim_and_normalize_bounds_and_silence():
    sr = 48000
    stereo = np.ones((sr * 2, 2), dtype=np.float32) * 0.25
    # Valid 0.5s to 1.5s trim + peak normalize to ~0.92
    out = trim_and_normalize(stereo, sr, trim_start=0.5, trim_end=1.5, volume=1.0, normalize=True)
    assert out.shape == (sr, 2)
    assert 0.85 <= float(np.max(np.abs(out))) <= 0.96

    # Invalid trim_end <= trim_start clamps safely without empty crash
    safe = trim_and_normalize(stereo, sr, trim_start=1.8, trim_end=0.5, volume=1.0, normalize=True)
    assert len(safe) > 0

def test_replay_ring_buffer_snapshot():
    buf = ReplayRingBuffer(max_seconds=2.0, sr=100, channels=2)
    for i in range(3):
        buf.append(np.full((100, 2), float(i + 1), dtype=np.float32))
    snap = buf.snapshot(last_seconds=2.0)
    assert snap.shape == (200, 2)
    assert np.allclose(snap[:100], 2.0)
    assert np.allclose(snap[100:], 3.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audio_engine'`

- [ ] **Step 3: Implement `audio_engine.py`**

Implement:
- `trim_and_normalize`: safe sample slicing, peak normalization to `-0.7 dBFS` (`0.92`), volume scaling with soft `np.tanh` limiter above `0.95` to avoid digital harshness.
- `ReplayRingBuffer`: thread-safe ring buffer for 48 kHz stereo chunks.
- `AudioEngine`:
  - Auto-discovers `"CABLE Input"` (VB-Audio Virtual Cable), default output monitor, and default input microphone.
  - Decodes `.mp3/.wav/.ogg/.flac/.m4a` via `soundfile` (with `ffmpeg` subprocess fallback for formats not natively handled).
  - Plays simultaneously on `cable_device_id` and `monitor_device_id` using non-blocking `sd.OutputStream` threads, catching `PortAudioError` gracefully if a device is unavailable.
  - Background WASAPI loopback capture via `pyaudiowpatch` feeding `ReplayRingBuffer`.
  - Optional microphone passthrough stream forwarding mic input directly to `CABLE Input`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_core.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add audio_engine.py tests/test_core.py
git commit -m "feat: add dual-output AudioEngine with VB-Cable routing, mic passthrough, and WASAPI replay buffer"
```

---

### Task 4: Global Hotkeys, Desktop UI (`ui/`) & Main Application (`main.py`)

**Files:**
- Create: `hotkeys.py`
- Create: `ui/index.html`
- Create: `ui/styles.css`
- Create: `ui/app.js`
- Create: `main.py`
- Create: `iniciar_soundboard.bat`

**Interfaces:**
- Consumes: `SoundboardStore`, `AIAnalyzer`, `AudioEngine`
- Produces:
  - `HotkeyManager` in `hotkeys.py` binding clip hotkeys, `stop_all_hotkey`, `replay_hotkey`, and `overlay_hotkey`
  - `SoundboardAPI` in `main.py` exposed via `webview.create_window(..., js_api=api)`
  - 1-click launcher `iniciar_soundboard.bat`

- [ ] **Step 1: Write smoke test for `SoundboardAPI` initialization and batch import in `tests/test_core.py`**

```python
from main import SoundboardAPI

def test_soundboard_api_smoke(tmp_path):
    api = SoundboardAPI(data_dir=str(tmp_path), start_background_services=False)
    state = api.get_initial_state()
    assert "clips" in state
    assert "devices" in state
    assert "speakers" in state
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Implement `hotkeys.py`, `ui/index.html`, `ui/styles.css`, `ui/app.js`, `main.py`, and `iniciar_soundboard.bat`**

Implement:
- `hotkeys.py`: Global keyboard listener using `keyboard` library with safe hook registration/refresh whenever clip hotkeys or global settings change.
- `ui/index.html`, `ui/styles.css`, `ui/app.js`:
  - Top bar with `CABLE Input` & `Monitor` device selectors, independent volume sliders, **Mic Passthrough** toggle, **Overlap** toggle, **⚡ Clipar Call (15s)** button, **📂 Importar Áudios / Pasta `separados`**, **🛑 Stop All**, and **📌 Modo Mini-Overlay** toggle.
  - Left sidebar with Category filters, **Interlocutores / Amigos** filter list (with inline rename modal so `Interlocutor 1` becomes the friend's name), and AI status badge.
  - Main Launchpad Grid with search bar (`Ctrl+F`), StreamDeck cards showing emoji, title, speaker badge, acoustic mood badge, transcript preview, duration, hotkey badge, favorite star, and active progress bar.
  - **Clip Studio Modal** for editing title, category, speaker, emoji, color, press-to-bind hotkey, trim start/end sliders with preview, volume boost, **"✨ Re-analisar com IA"**, and **"🎙️ Salvar como Voz de Referência deste Amigo"**.
- `main.py` & `iniciar_soundboard.bat`: Launches PyWebView window (`Soundboard AI — VB-Cable Studio`) and wires all frontend calls.

- [ ] **Step 4: Run full test suite to verify all tests pass**

Run: `uv run pytest tests/test_core.py -v`
Expected: PASS (all 6 tests passing)

- [ ] **Step 5: Commit and push to GitHub**

```bash
git add hotkeys.py ui/ main.py iniciar_soundboard.bat tests/test_core.py
git commit -m "feat: complete Soundboard AI desktop app, global hotkeys, and StreamDeck UI"
git push origin main
```
