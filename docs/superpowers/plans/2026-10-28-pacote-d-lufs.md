# Pacote D — Normalização de Loudness (EBU R128 / ITU-R BS.1770-4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Implementar medição e normalização de loudness integrada baseada em ITU-R BS.1770-4 e EBU R128, calibrando clipes para o alvo de −16 LUFS com ganho compensatório clampado em [−12 dB, +12 dB] e teto de pico em −1.0 dBFS, exibindo badge e controle de reanálise no modal de edição.

**Architecture:**
- `loudness.py` (novo):
  - K-weighting BS.1770-4 a 48 kHz (Stage 1 pre-filter + Stage 2 RLB weighting).
  - Blocos de 400 ms com 75% de sobreposição (hop de 100 ms).
  - Gating duplo: absoluto (−70 LUFS) e relativo (−10 LU da média pré-filtrada).
  - `integrated_lufs(samples_mono_48k) -> tuple[float | None, bool]`: retorna `(lufs, lufs_estimated)`.
  - Para clipes curtos (< 400 ms), cálculo por RMS ponderado com `lufs_estimated = True`.
  - `calculate_loudness_gain(lufs: float | None, peak_abs: float = 1.0, target_lufs: float = -16.0) -> float`: devolve ganho em dB clampado em `[-12.0, +12.0]` respeitando o teto de `-1.0 dBFS`.
- `main.py`:
  - Recalcula `lufs`, `loudness_gain_db`, `lufs_estimated` na importação de arquivos, na reanálise de IA e ao salvar edições de trim.
  - Endpoint `reanalyze_clip_loudness(clip_id: str) -> dict`.
  - Injeta `loudness_gain_db` no playback do `AudioEngine`.
- `audio_engine.py`:
  - Aplica o fator de escala de `loudness_gain_db` (`10 ** (gain_db / 20)`) na preparação dos buffers de playback da sessão.
- `ui/index.html`, `ui/app.js`, `ui/styles.css`:
  - No modal de edição: badge `#clipLufsBadge` ("Nível: −16,2 LUFS") e botão `#btnReanalyzeLufs` ("Reanalisar nível").
- Testes em `tests/test_d_loudness.py`.

**Spec:** `docs/superpowers/specs/2026-10-28-soundboard-roadmap-design.md` §11.

---

## Contratos

```python
# loudness.py
def integrated_lufs(samples: np.ndarray, sr: int = 48000) -> tuple[float | None, bool]: ...
def calculate_loudness_gain(lufs: float | None, peak_abs: float = 1.0, target_lufs: float = -16.0) -> float: ...
def analyze_audio_loudness(samples: np.ndarray, sr: int = 48000) -> dict:
    # {"lufs": float | None, "loudness_gain_db": float, "lufs_estimated": bool}
```

```python
# SoundboardAPI
def reanalyze_clip_loudness(self, clip_id: str) -> dict:
    # {"ok": bool, "clip"?: dict, "error"?: str}
```

---

## Tarefas de Implementação

### Task 1: `loudness.py` e `tests/test_d_loudness.py`
- Biquad filters em direct form II com coeficientes de BS.1770-4 para 48 kHz.
- Gating absoluto a -70 LUFS e relativo a -10 LU.
- Tratamento de áudio estéreo convertendo para mono médio.
- Estimativa para clipes < 400 ms com flag `lufs_estimated = True`.
- `calculate_loudness_gain` com clamp `[-12, +12]` e teto de pico `-1.0 dBFS`.
- Testes:
  - Seno de 1 kHz a -23 dBFS RMS produz -23 LUFS (±0.3 LUFS).
  - Silêncio puro devolve `None` e ganho 0 dB.
  - Áudio curto (< 400 ms) ativa `lufs_estimated = True`.
  - Gating descarta blocos de silêncio intercalados.
  - Ganho respeita teto de pico de -1.0 dBFS.

### Task 2: Integração com `audio_engine.py` e `main.py`
- Em `audio_engine.py`, aplicar `loudness_gain_db` no `play_clip`.
- Em `main.py`, calcular e persistir `lufs`, `loudness_gain_db`, `lufs_estimated` em:
  - `import_audio_files`
  - `import_folder_or_separados`
  - `save_clip_edits` (quando trim_start ou trim_end mudarem)
  - Novo endpoint `reanalyze_clip_loudness`.

### Task 3: Interface no Modal de Edição (`ui/index.html`, `ui/app.js`, `ui/styles.css`)
- Adicionar badge `#clipLufsBadge` e botão `#btnReanalyzeLufs` no modal de corte/edição.
- `renderEditModal`: preencher badge com o valor formatado (ex: "Nível: −16.2 LUFS" ou "Nível: −16.2 LUFS (est.)" ou "Nível: —").
- Event listener no `#btnReanalyzeLufs` chamando `api().reanalyze_clip_loudness(...)` e atualizando o badge em tempo real com toast de confirmação.

### Task 4: Verificação, Revisão e Merge
- Executar `uv run pytest -q`.
- Executar `node --test tests/test_fuzzy.test.cjs`.
- Executar `node --check ui/app.js` e `python -m compileall`.
- `git diff --check`.
- Revisão independente.
- Merge `--ff-only` na `main`.
