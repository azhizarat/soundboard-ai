# Soundboard AI — Design Specification

**Date:** 2026-10-28  
**Repository:** `D:\repos\soundboard-ai` (`github.com/azhizarat/soundboard-ai`, Private)  
**Target Platform:** Windows 10/11 (Python 3.10+, managed via `uv`)

---

## 1. Purpose & Success Criteria

Build a low-latency desktop digital soundboard designed to inject audio clips directly into **VB-Audio Virtual Cable (`CABLE Input`)** (so they are heard on **`CABLE Output`** in Discord/games) while simultaneously playing on the user's local headphones/speakers (**Monitor Output**).

Key differentiators:
1. **Dual-Output + Mic Passthrough**: Simultaneous playback to `CABLE Input` and local monitor device with independent volumes, plus optional real-time physical microphone passthrough to `CABLE Input`.
2. **Instant Call Replay Buffer ("Clipar Call")**: WASAPI loopback circular buffer capturing the last $N$ seconds (default 15s) of system/call audio so funny moments in Discord can be clipped, trimmed/diarized, analyzed by AI, and turned into a soundboard button immediately.
3. **Advanced Local AI Pipeline**:
   - **Speaker Identification (`sherpa-onnx` + NVIDIA TitaNet)**: Reuses existing voice models and reference centroids from `C:\Users\Usuario\Videos\.modelos_voz` (`referencias_vozes.npz`) so clips automatically identify *who* spoke, with customizable speaker names and colors in the UI.
   - **Speech Transcription (`faster-whisper`)**: Fast PT-BR transcription (GPU `float16` on RTX 3070 with automatic CPU `int8` fallback).
   - **Acoustic & Prosodic Emotion Analysis (`numpy`)**: Computes RMS energy, peak crest factor, zero-crossing rate, pitch ($F_0$) contour, and laughter/burst periodicity to classify vocal delivery (`grito/rage`, `risada`, `alta_energia`, `sussurro/calmo`, `normal`, `efeito_sonoro`).
   - **Smart Naming & Categorization (Local LLM + Heuristic Fallback)**: Uses a lightweight local GGUF model (`Qwen2.5-1.5B-Instruct` via `llama-cpp-python` or local Ollama if running, with deterministic PT-BR smart fallback) combining `{speaker_name, transcript, acoustic_mood, duration}` to generate a short catchy button title (2–4 words), category (`Rage`, `Risada`, `Meme`, `Call / Pérola`, `Efeito`, `Clássico`), emoji, and accent color.
4. **Desktop UI & Global Hotkeys**: Native lightweight desktop window via `pywebview` (Windows WebView2) featuring StreamDeck/Launchpad grid, drag-and-drop import (individual audio files or `separados` folders with `mapa_de_falas.json`), instant search, trim/volume editor, global keyboard shortcuts (`keyboard`), and a compact **Always-on-Top Mini-Overlay** mode for in-game use.

---

## 2. System Architecture & Modules

The project follows a flat, modular Python structure under `D:\repos\soundboard-ai`:

```text
D:\repos\soundboard-ai\
├── iniciar_soundboard.bat       # 1-click launcher via `uv run`
├── pyproject.toml               # Dependencies and project metadata
├── main.py                      # Entry point: starts AudioEngine, HotkeyManager, AIAnalyzer, PyWebView bridge
├── audio_engine.py              # Dual-stream playback, Mic Passthrough, WASAPI Loopback Replay Buffer, normalization/trim
├── ai_analyzer.py               # TitaNet Speaker ID, Faster-Whisper, Acoustic Mood Extractor, Local LLM namer/categorizer
├── store.py                     # JSON state persistence (clips, speakers, categories, settings) & folder importer
├── hotkeys.py                   # Global background keyboard listener (play clips, stop all, save replay, toggle overlay)
├── ui/
│   ├── index.html               # Single-page responsive StreamDeck/Gamer UI (normal + mini-overlay modes)
│   ├── styles.css               # Modern dark theme, animations, waveform/progress indicators
│   └── app.js                   # Frontend state, drag & drop, waveform trim preview, pywebview API bindings
├── data/
│   ├── soundboard.json          # Persisted clips, speaker mappings, settings
│   └── clips/                   # Normalized/imported audio files (.wav / .mp3)
└── tests/
    └── test_core.py             # Automated tests for store, acoustic analysis, audio normalization, and AI naming
```

---

## 3. Component Design & Data Flow

### 3.1. `audio_engine.py` — Audio Routing, Mixing & Replay Buffer
- **Device Discovery**:
  - Scans PortAudio devices via `sounddevice.query_devices()`.
  - Auto-selects the primary output matching `"CABLE Input"` (VB-Audio Virtual Cable) as `cable_device_id`.
  - Auto-selects the system default output device as `monitor_device_id`.
  - Auto-selects the system default input device as `mic_device_id`.
- **Playback Mixing & Dual Output**:
  - Clips are decoded once to `float32` stereo at `48000 Hz` (via `soundfile` / `ffmpeg` fallback) and cached in memory when played.
  - Supports **Trim (`start_s`, `end_s`)**, per-clip **Gain (`volume` 0.0–2.0)**, and optional **LUFS/Peak Normalization** on import.
  - Supports two playback modes: **Overlap (Polyphonic)** or **Singleton (Cut Previous)**.
  - Plays simultaneously on `cable_device_id` (scaled by `cable_volume`) and `monitor_device_id` (scaled by `monitor_volume`) using independent non-blocking worker threads/streams so a disconnected secondary device never blocks playback.
  - `stop_all()` immediately halts all active streams.
- **Mic Passthrough**:
  - Optional background `sounddevice` input-to-output stream forwarding `mic_device_id` → `cable_device_id` with configurable gain and mute toggle.
- **WASAPI Loopback Replay Buffer ("Clipar Call")**:
  - Uses `pyaudiowpatch` to open the default WASAPI loopback speaker device in a background daemon thread.
  - Maintains a thread-safe circular `collections.deque` storing the last `replay_seconds` (configurable: 10s, 15s, 30s; default 15s) of system audio at 48 kHz.
  - Calling `save_replay_clip(last_seconds=15)` snapshots the ring buffer, trims leading/trailing silence, writes a `.wav` file to `data/clips/`, and hands it off to the AI Analyzer or interactive trimmer.

### 3.2. `ai_analyzer.py` — Multi-Stage Local AI Pipeline
1. **Model Reuse & Caching**:
   - Checks `C:\Users\Usuario\Videos\.modelos_voz` first for `pyannote-segmentation-3-0/model.onnx`, `nemo_en_titanet_large.onnx`, and `referencias_vozes.npz`. Falls back to `data/models/` and auto-downloads if missing.
2. **Stage 1: Speaker Identification (`identify_speaker`)**:
   - Extracts a 16 kHz mono waveform, computes the normalized TitaNet speaker embedding via `sherpa_onnx`, and computes cosine similarity against all centroids in `referencias_vozes.npz`.
   - If similarity $\ge 0.55$, maps the clip to `interlocutor_N` (and its user-assigned friendly name in `store.py`, e.g., `"Amigo 1"`). Also allows enrolling new reference voices directly from any clip in the soundboard.
3. **Stage 2: Acoustic & Prosodic Mood Analysis (`analyze_acoustics`)**:
   - Analyzes the raw waveform using `numpy`:
     - `rms_db` & `peak_db`: overall loudness and dynamic burst.
     - `f0_mean` & `f0_std`: fundamental frequency (pitch) mean and variability via autocorrelation.
     - `burst_rate`: envelope modulation rate (3–6 Hz rhythmic bursts characteristic of laughter).
   - Outputs an acoustic tag:
     - `Rage / Grito`: high RMS/peak + elevated pitch or clipping energy.
     - `Risada`: rhythmic amplitude bursts (3.5–6.5 Hz) + high pitch variance.
     - `Calmo / Sussurro`: low RMS + low pitch variance.
     - `Efeito / Meme`: very short duration (< 0.8s) or non-vocal spectral profile.
     - `Fala Animada` / `Normal`: standard conversational speech.
4. **Stage 3: Speech Transcription (`transcribe_audio`)**:
   - Uses `faster-whisper` (`base` or `small`, CUDA `float16` if available, else CPU `int8`) to transcribe Portuguese speech.
   - If the clip is imported from a `separados/` folder that already contains `mapa_de_falas.json`, reuses the existing transcription and speaker ID immediately unless re-analysis is requested.
5. **Stage 4: Smart Title & Category Generation (`generate_metadata`)**:
   - Prompt-driven local LLM inference:
     - First checks if local Ollama or `llama-cpp-python` (with auto-downloaded `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` in `data/models/`) is available.
     - Feeds structured prompt: Speaker name, Transcription, Acoustic Mood, and Duration.
     - Produces JSON: `{"title": "...", "category": "Rage|Risada|Meme|Call|Efeito|Clássico", "emoji": "🔥", "color": "#ef4444", "tags": [...]}`.
     - Includes an instant deterministic PT-BR smart analyzer (combining keyword sentiment, acoustic mood, speaker name, and punchy phrase extraction) so imports are 100% functional with zero wait even before/without GGUF download.

### 3.3. `store.py` — Persistence & Batch Importer
- Stores all state atomically in `data/soundboard.json`:
  - `settings`: selected audio devices, volumes, mic passthrough state, replay buffer duration, global hotkeys (`stop_all_hotkey`, `replay_hotkey`, `overlay_hotkey`), overlap mode.
  - `speakers`: map of `spk_id` → `{"id": "interlocutor_1", "name": "Interlocutor 1", "color": "#3b82f6", "avatar": "🎙️"}`.
  - `categories`: default and custom categories with colors/icons.
  - `clips`: list of clip objects (`id`, `title`, `file_path`, `category`, `speaker_id`, `transcript`, `acoustic_mood`, `emoji`, `color`, `hotkey`, `volume`, `trim_start`, `trim_end`, `duration`, `favorite`, `play_count`, `created_at`).
- **Batch Folder Importer (`import_separados_folder`)**:
  - Can point directly to `C:\Users\Usuario\Videos\B\separados` (or any folder with audio files).
  - Automatically reads `mapa_de_falas.json` if present, imports the clips, runs Acoustic Mood + Smart Naming on each clip, and populates speaker profiles and categories in seconds.

### 3.4. `ui/` + `main.py` — Desktop Experience & Mini-Overlay
- **Top Control Bar**:
  - Output selector (`CABLE Input`), Monitor selector (`Headphones`), Volume sliders, Mic Passthrough toggle, Overlap toggle.
  - **⚡ Clipar Últimos 15s** button (with hotkey badge), **📂 Importar Áudios / Pasta `separados`**, **🛑 Stop All**, and **📌 Mini-Overlay** toggle (resizes window to `360x480` always-on-top compact deck).
- **Sidebar / Filter Bar**:
  - Filter by Category (`Todos`, `⭐ Favoritos`, `🔥 Rage`, `😂 Risada`, `🤡 Meme`, `🎙️ Call`, `🔊 Efeito`), filter by **Speaker/Interlocutor** (with quick rename button for each speaker), and real-time search bar (`Ctrl+F` matches title, transcript text, speaker, or tags).
- **Soundpad Grid**:
  - StreamDeck-style cards with accent glow, emoji, title, speaker badge, mood badge, transcript preview tooltip, duration, assigned hotkey badge, and animated progress bar while playing.
  - Right-click or edit button opens the **Clip Studio Modal**:
    - Edit title, emoji, color, category, speaker, and global hotkey (press-to-bind).
    - Interactive waveform/range trim (`Início` / `Fim`), individual volume boost, **"✨ Re-analisar com IA"**, and **"🎙️ Salvar como Voz de Referência deste Amigo"**.

---

## 4. Error Handling & Edge Cases
- **Missing VB-Audio Cable**: If `CABLE Input` is not detected on startup, shows a non-blocking banner in the UI and defaults safely to the monitor output until selected.
- **PortAudio / Loopback Stream Errors**: Audio streams catch `PortAudioError` per device so a headset unplug/replug does not crash the app.
- **Hotkeys in Fullscreen Games**: Uses the `keyboard` library in a low-level hook thread; gracefully degrades if administrator privileges are not present for elevated games.
- **AI Model Fallbacks**: If CUDA DLLs are missing on the host Python environment, `faster-whisper` automatically falls back to `cpu` + `int8`. If LLM GGUF is not yet downloaded, smart heuristic + acoustic + Whisper naming runs immediately with an optional 1-click download button for the GGUF LLM.

---

## 5. Verification & Testing Plan
- Automated test suite (`tests/test_core.py`) covering:
  1. `store.py`: CRUD operations on clips/speakers, importing `mapa_de_falas.json` structure, search/filter logic.
  2. `ai_analyzer.py`: Acoustic mood classification on synthetic sine/burst/high-energy waveforms and smart title/category generation from PT-BR transcripts + moods.
  3. `audio_engine.py`: Waveform trimming, peak normalization, and circular replay buffer snapshot extraction.
- End-to-end smoke test verifying `main.py` initialization, device enumeration on Windows, and GitHub private repo push.
