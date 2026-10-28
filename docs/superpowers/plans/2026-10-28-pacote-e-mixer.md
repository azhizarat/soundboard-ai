# Pacote E — Mixer Persistente

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Implementar o motor de mixagem persistente `AudioMixer` com stream único por dispositivo (Cabo e Monitor) a 48 kHz / 1024 frames, callback de áudio em tempo real mixando múltiplas vozes simultâneas, crossfade de 10 ms no stop (anti-clique), rampa de transição de volume, limiter lookahead com teto de −1.0 dBTP, e preservação da compatibilidade com o modo `legacy` via `config.mixer_mode`.

**Architecture:**
- `mixer.py` (novo):
  - `MixerVoice`: estado de cada som ativo (`play_id`, `clip_id`, `data`, `pos_idx`, `live_volume`, `base_gain`, `loop`, `paused`, `stopping`, `fade_samples_remaining`, `stop_event`).
  - `AudioMixer`: gerencia streams persistentes por dispositivo (`OutputStream` com callback pull-based).
  - Callback de áudio:
    - Lê e interpola chunks de cada voz ativa.
    - Aplica ducking dinâmico via rampa suave.
    - Aplica crossfade suave de 10 ms em vozes sendo interrompidas.
    - Soma os buffers de vozes concorrentes.
    - Limiter com lookahead / saturação suave (`tanh`) prevenindo digital clipping no teto de −1.0 dBTP (~0.891).
    - Remove vozes concluídas e notifica callbacks de finalização.
  - Método `render_frames(frames, duck_gain)` para testes 100% determinísticos e hardware-free sem depender de PortAudio.
- `audio_engine.py`:
  - Integra `AudioMixer` como motor padrão quando `mixer_mode == "mixer"`.
  - Mantém o motor legado quando `mixer_mode == "legacy"`.
  - Mantém idêntica a interface pública: `play_clip`, `stop_all`, `get_session`, `set_live_clip_volume`, `pause_playback`, `resume_playback`, `seek_playback`, `status()`.
- Testes em `tests/test_e_mixer.py`.

**Spec:** `docs/superpowers/specs/2026-10-28-soundboard-roadmap-design.md` §12.

---

## Contratos

```python
# mixer.py
class MixerVoice:
    def __init__(self, play_id: str, clip_id: str, data: np.ndarray, base_gain: float = 1.0, live_volume: float = 1.0, loop: bool = False, monitor_only: bool = False, sr: int = 48000, title: str = "", emoji: str = ""): ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def seek(self, sec: float) -> None: ...
    def stop_with_crossfade(self, fade_ms: float = 10.0) -> None: ...
    def status(self) -> dict: ...

class DeviceMixer:
    def __init__(self, device_id: int | None, sr: int = 48000, blocksize: int = 1024): ...
    def add_voice(self, voice: MixerVoice) -> None: ...
    def remove_voice(self, play_id: str) -> None: ...
    def render_block(self, frames: int, duck_gain: float = 1.0) -> np.ndarray: ...

class AudioMixer:
    def __init__(self, sr: int = 48000, blocksize: int = 1024): ...
    def play(self, clip: dict, settings: dict, raw_audio: np.ndarray, monitor_only: bool = False) -> MixerVoice: ...
    def stop(self, play_id: str) -> None: ...
    def stop_all(self) -> None: ...
    def get_voice(self, play_id: str) -> MixerVoice | None: ...
```

---

## Tarefas de Implementação

### Task 1: `mixer.py` e `tests/test_e_mixer.py`
- Estrutura `MixerVoice` com todas as propriedades de playback e controle thread-safe (`pause`, `resume`, `seek`, `stop_with_crossfade`).
- `DeviceMixer`: soma de vozes com pesos independentes, rampa de ducking, crossfade de parada (10 ms) e limiter com teto de −1 dBTP.
- `AudioMixer`: orquestrador de saídas para Cabo e Monitor.
- Testes de renderização pura:
  - Duas vozes somam sem distorção.
  - Limiter limita soma de sinais acima de 1.0 ao teto de 0.891 (-1 dBTP).
  - Stop aplica crossfade decrescente de 10 ms sem clique.
  - Loop retorna ao início após atingir o final.
  - Pause emite silêncio sem avançar posição.
  - Seek altera posição da reprodução com precisão.

### Task 2: Integração com `audio_engine.py`
- Adicionar suporte a `mixer_mode` em `AudioEngine`.
- No modo `mixer`, delegar sessões e playback para `AudioMixer`.
- No modo `legacy`, manter execução dos streams dedicados por thread.
- Assegurar que `_sessions` e `get_session` continuem devolvendo objetos compatíveis com a interface esperada pela UI e pelos testes existentes.

### Task 3: Verificação, Revisão e Merge
- Executar `uv run pytest -q`.
- Executar `node --test tests/test_fuzzy.test.cjs`.
- Executar `node --check ui/app.js` e `python -m compileall`.
- `git diff --check`.
- Revisão independente.
- Merge `--ff-only` na `main`.
