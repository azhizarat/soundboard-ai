# Soundboard AI — Roadmap de evolução (produto + estrutura)

Data: 2026-10-28
Status: aprovado para implementação em pacotes sequenciais

## 1. Contexto

O app hoje funciona, mas tem três frentes de dívida:

1. **Uso diário** — abrir pelo `.bat` deixa um console aberto; fechar a janela mata os hotkeys; não há busca rápida; excluir é irreversível e sem confirmação.
2. **Qualidade de áudio** — normalização só por pico, um `OutputStream` por clipe (latência, drift, sem polyphony real), sem ducking.
3. **Portabilidade** — caminhos `C:\Users\Usuario\...` hardcoded, dependência implícita de ffmpeg/VB-Cable/modelos, sem diagnóstico quando algo falta.

Este spec cobre sete pacotes independentes, implementados em ordem, cada um em worktree própria, com revisão independente e merge na `main` só depois de testes verdes.

## 2. Objetivos

- Tornar o app utilizável por outra pessoa sem editar código.
- Reduzir atrito de execução (sem console, tray, instância única).
- Melhorar consistência percebida de áudio (LUFS + limiter) e permitir ducking.
- Aumentar confiança nas operações destrutivas (undo/confirmação).
- Facilitar compartilhar clipes (packs).
- Manter compatibilidade: instalações existentes continuam abrindo sem migração manual.

## 3. Não-objetivos (nesta rodada)

- PyInstaller/instalador empacotado (o doctor cobre o atrito; reavaliar depois).
- Banks/scenes, API local WebSocket/HTTP, overlay externo, gate/compressor no passthrough.
- SQLite: fica para depois do pacote C, com gatilho objetivo (ver §10).
- Reescrever a UI: as mudanças usam os componentes existentes.

## 4. Princípios

1. **Degradação graciosa** — falta de ffmpeg/modelos/VB-Cable desabilita só a feature afetada, com explicação e link.
2. **Nada bloqueia o boot** — diagnóstico é banner + painel, nunca tela morta.
3. **Backend valida, UI informa** — toda recusa tem `{ok: false, error}` com mensagem em pt-BR.
4. **Testável sem hardware** — áudio é testado com backend falso; device real só em smoke test manual.
5. **Flag para o risco** — mudanças de playback podem voltar ao motor antigo (`mixer_mode=legacy`).
6. **Operação destrutiva é reversível ou confirmada**.

## 5. Ordem de execução

| Ordem | Pacote | Escopo | Risco |
|---|---|---|---|
| 1 | **F1** | Busca fuzzy + Ctrl+K, undo/confirmação, ducking | Baixo |
| 2 | **F2** | Preview do replay, export/import de packs | Médio |
| 3 | **A** | Config portátil + doctor de ambiente | Baixo |
| 4 | **B** | Tray, instância única, logs, launcher sem console | Baixo |
| 5 | **C** | Persistência versionada + write-behind | Médio |
| 6 | **D** | Normalização por LUFS | Médio |
| 7 | **E** | Mixer persistente + limiter + ducking no mixer | Alto |

Cada pacote tem critérios de aceite próprios e entra na `main` isoladamente.

---

## 6. F1 — Busca, undo e ducking

### 6.1 Busca fuzzy + paleta Ctrl+K

**Arquivo novo `ui/fuzzy.js`** (script clássico, carregado antes de `app.js`), exposto em `window.SBFuzzy` e compatível com Node (`module.exports` quando disponível) para teste.

```js
normalizeText(s)            // NFD, remove diacríticos, lowercase, colapsa espaços
score(query, text)          // null | número (maior = melhor)
scoreClip(clip, query, speakers) // null | número ponderado
highlight(text, query)      // string HTML com <mark>, construída só a partir de segmentos escapados
```

Regras de `score` (após normalizar):

| Condição | Pontuação |
|---|---|
| igualdade | 1000 |
| prefixo | 900 − 0.5·len(texto) |
| início de palavra (após espaço, `-`, `_`, `/`) | 800 − 0.5·posição |
| substring | 700 − 0.5·posição |
| subsequência (todos os chars na ordem) | 300 + 200·(len(query)/len(texto)) − penalidade de lacunas |
| caso contrário | `null` |

`scoreClip` usa peso por campo: título 3, interlocutor 2, transcrição 1, categoria/tags/mood/hotkey 1. Empate: favorito primeiro, depois `play_count`, depois `created_at`.

**Integração**
- `getFilteredClips()` passa a usar `SBFuzzy.scoreClip` no lugar de `includes`, mantendo filtros de categoria/interlocutor/favoritos e a ordenação atual como desempate.
- Título e transcrição do card recebem `<mark>` quando há busca ativa (via `highlight`, sempre escapado).
- `ui/index.html`: carrega `fuzzy.js` antes de `app.js`.

**Paleta (Ctrl+K / Cmd+K)**
- Overlay `#commandPalette` com input, lista (máx. 12), rodapé de atalhos.
- Abre com Ctrl+K em qualquer lugar, exceto com modal de edição aberto.
- `↑`/`↓` navegam, `Enter` toca, `Shift+Enter` ouve só no fone, `Esc` fecha, clique toca.
- Resultado mostra emoji, título, interlocutor e hotkey; sem resultado mostra dica.
- O campo de busca existente ganha placeholder "Buscar… (Ctrl+K)".

**Aceite**
- `nao` encontra `não`; `nvs` encontra `não vs sim`; busca vazia mantém o comportamento atual.
- Ctrl+K abre em <100 ms com 500 clipes; Enter toca e fecha a paleta.
- Nenhum HTML injetável via título/transcrição (usa `escapeHtml` já existente).

### 6.2 Undo de delete + confirmação

**Backend**
- `delete_clip(clip_id, delete_file=False)` passa a retornar `{ok, clip, can_undo}` com `clip` em `deepcopy` e `can_undo = not delete_file`.
- Nova `restore_clip(clip: dict) -> {ok, clip?, clips, speakers, error?}`:
  - valida `id` (str não vazia), `file_path` resolvido **dentro** de `clips_dir` e existente;
  - recusa id já existente (`error: "Este botão já existe."`);
  - `store.add_or_update_clip(clip)`, atualiza hotkeys, retorna snapshots.
- `delete_clip` continua **sem** apagar arquivo por padrão (comportamento atual da UI).

**UI**
- `showToast` ganha opção de ação: `showToast(msg, color, {actionLabel, onAction, duration})`; a ação renderiza um botão no toast.
- Delete do clipe: guarda o `clip` retornado, mostra "🗑️ Removido — Desfazer" por 8 s; undo chama `restore_clip`, re-insere no estado e re-renderiza.
- Se outro delete acontecer antes, o toast anterior é descartado (apenas um undo pendente).
- Confirmação explícita (2 cliques no próprio modal, sem `confirm()` nativo) para:
  - excluir interlocutor (`btnDeleteSpeakerModal`);
  - qualquer ação futura que apague arquivo permanentemente.
- O modal de edição não fecha antes da resposta do backend.

**Testes**
- delete → restore devolve clipe com mesmo id e aparece em `snapshot()`.
- restore recusa arquivo fora de `clips_dir`, arquivo inexistente e id duplicado.
- hotkeys são recalculados após delete e após restore.

### 6.3 Ducking (soundboard abaixa quando o mic fala)

**Semântica**: o passthrough do mic **não** é alterado; os clipes do soundboard é que abaixam.

**Engine (`audio_engine.py`)**
- `set_ducking(enabled, amount_db=-12.0, threshold_dbfs=-40.0)`:
  - `threshold_rms = 10 ** (threshold_dbfs / 20)`;
  - `duck_gain = 10 ** (amount_db / 20)` (0 < gain ≤ 1);
  - guarda estado sob lock e é idempotente.
- O callback do mic calcula RMS do bloco (mono) e atualiza o alvo (`duck_gain` ou `1.0`).
- Suavização one-pole por bloco: `alpha = 1 − exp(−block_s / tau)`, `tau_attack = 0.05 s`, `tau_release = 0.40 s`.
- O stream de cada clipe lê o ganho atual e aplica **rampa linear** ao longo do chunk (evita zipper noise), multiplicando o ganho do clipe e o volume ao vivo.
- Sem mic passthrough ativo ou ducking desligado, o ganho é 1.0 (com release, não corte seco).
- Nenhuma alocação por bloco no caminho de áudio.

**Settings** (`store.DEFAULT_SETTINGS`)
```python
"duck_enabled": False,
"duck_amount_db": -12.0,
"duck_threshold_dbfs": -40.0,
```
- `_apply_mic_passthrough()` passa a chamar `set_ducking` com esses valores.
- Nova API `set_ducking(enabled=None, amount_db=None, threshold_dbfs=None)` valida faixas (`amount_db` em [−24, 0], `threshold_dbfs` em [−60, −10]) e persiste via `update_settings`.
- UI: seção "Ducking" no popover de roteamento — toggle, slider de intensidade (0 a −24 dB) e nota "só funciona com o microfone ativo".

**Testes**
- envelope: degrau de entrada atinge o ganho alvo em ~3–5 blocos; release volta a 1.0 em ~0.4 s; desligado = 1.0.
- ganho aplicado no stream (backend falso) sem alterar posição/loop.
- `set_ducking` valida faixas e persiste; valores inválidos não derrubam a aplicação.

---

## 7. F2 — Preview do replay e packs

### 7.1 Preview antes de salvar

**Backend** (`main.py`)
- `capture_replay_draft(last_seconds=None) -> {ok, clip, clips, speakers?, warning?}`:
  - grava em `data/tmp/replay_<ts>.wav`;
  - roda a análise de IA (se habilitada) como hoje;
  - cria o clipe com `pending: True` e `file_path` no tmp, **sem** notificar `replay_captured`;
  - `clips` retornado **exclui** pendentes.
- `commit_replay_draft(clip_id) -> {ok, clip, clips, speakers}`:
  - exige `pending == True`;
  - move o WAV do tmp para `clips_dir` (nome definitivo), atualiza `file_path`, `pending = False`, `created_at`;
  - `add_or_update_clip`, hotkeys, snapshot.
- `discard_replay_draft(clip_id) -> {ok}`:
  - remove o clipe pendente e apaga o arquivo **somente** se ele estiver dentro de `data/tmp` (validação por `resolve()`).
- `get_initial_state` filtra `pending`; `hotkeys.refresh` também.
- **Purga no boot**: todo clipe `pending` é removido e seu arquivo apagado (drafts não sobrevivem a restart). Isso evita lixo se o app fechar no meio.

**UI**
- `btnCliparCall` chama `capture_replay_draft`, abre o modal de edição existente em modo **draft**:
  - título do modal "⚡ Replay — revise antes de salvar";
  - rodapé com **Salvar no Soundboard** e **Descartar**;
  - waveform, trim, volume e preview no fone funcionam normalmente (o clipe existe no store como pendente).
- Salvar → `commit_replay_draft` → fecha modal e mostra toast.
- Descartar → `discard_replay_draft` → fecha sem toast de erro.
- Fechar com Esc/clique fora em modo draft equivale a **Descartar** (com confirmação implícita no ato).

**Testes**
- draft não aparece em `get_initial_state` nem em hotkeys; arquivo existe no tmp.
- commit move o arquivo, limpa `pending` e aparece no estado.
- discard apaga o arquivo e o clipe; caminho fora do tmp é recusado sem apagar nada.
- boot remove pendentes antigos.

### 7.2 Packs (export/import)

**Formato** (`.zip`)
```
manifest.json
audio/<clip_id>.<ext>
```
`manifest.json`:
```json
{
  "format": "soundboard-pack",
  "version": 1,
  "created_at": 1760000000,
  "clips": [ { "title", "emoji", "category", "color", "hotkey", "trim_start",
               "trim_end", "volume", "transcript", "acoustic_mood", "tags",
               "duration", "favorite", "file": "audio/xxx.wav",
               "speaker_names": ["Lucas"] } ],
  "speakers": [ { "name": "Lucas", "color": "#3b82f6" } ]
}
```
Ids **não** são exportados: interlocutores viajam por nome; clipes recebem id novo no import.

**API**
- `export_pack(clip_ids: list[str] | None = None, path: str | None = None) -> {ok, path?, count, skipped, error?}`
  - `clip_ids=None` exporta a biblioteca; a UI passa os ids do filtro atual para "exportar visão".
  - `path=None` abre `SAVE_DIALOG` nativo; sem caminho escolhido → `{ok: false, error: "Exportação cancelada."}`.
  - Áudio ausente é pulado e contado em `skipped`; zip é gravado num `.tmp` e movido com `os.replace`.
- `import_pack(path: str | None = None) -> {ok, imported, skipped, warnings, clips, speakers, error?}`
  - `path=None` → `OPEN_DIALOG`.
  - Validação: zip legível, `manifest.json` presente, `format` correto, `version <= 1`, nenhuma entrada absoluta ou com `..`, apenas `audio/*` com extensões suportadas, tamanho descompactado ≤ 2 GB.
  - Dedupe por SHA-256 do áudio contra os clipes existentes → `skipped += 1`, warning "já existe".
  - Interlocutor: casa por nome `casefold`; cria com cor da paleta se não existir.
  - Hotkey conflitante: importa com `hotkey: ""` e warning.
  - `trim_end` limitado à duração real; `volume` em [0, 3]; categoria inexistente mantém a original (não quebra o filtro).
  - Emite `progress` durante a cópia.
- Cópia sempre para `clips_dir` com nome `<novo_id><ext>`; nada é escrito fora de `data/`.

**UI**
- Menu "📦 Pack" no topo: **Exportar biblioteca**, **Exportar filtro atual**, **Importar pack…**.
- Resultado do import vira toast + resumo no console de progresso; warnings listados no modal quando houver.

**Testes**
- round-trip: exportar 2 clipes → importar em biblioteca vazia → mesmos títulos/trims/interlocutores, ids novos, áudio idêntico (hash).
- zip-slip (`../evil.wav`), zip com entrada absoluta, manifesto inválido, versão futura e zip bomb (>2 GB declarado) são rejeitados sem escrever nada.
- dedupe: importar o mesmo pack duas vezes → segunda vez `imported: 0, skipped: N`.
- hotkey duplicada → clipe importado sem hotkey + warning.
- interlocutor inexistente é criado e remapeado.

---

## 8. Pacote A — Config portátil + doctor

### 8.1 `config.py`

```python
@dataclass(frozen=True)
class AppConfig:
    data_dir: Path          # SOUNDBOARD_DATA_DIR   (default: <app>/data)
    models_dir: Path        # SOUNDBOARD_MODELS_DIR (default: legacy Videos/.modelos_voz se existir, senão data/models)
    separados_root: Path    # SOUNDBOARD_SEPARADOS_ROOT (default: %USERPROFILE%/Videos)
    ffmpeg_path: str        # SOUNDBOARD_FFMPEG      (default: "ffmpeg")
    ollama_url: str         # SOUNDBOARD_OLLAMA_URL  (default: http://127.0.0.1:11434)
    ollama_model: str       # SOUNDBOARD_OLLAMA_MODEL(default: qwen2.5:1.5b)
    mixer_mode: str         # SOUNDBOARD_MIXER       (default: "legacy")
```
Precedência: env > `data/config.json` > default. `load_config(data_dir)` faz bootstrap do `data_dir` (esse vem só de env/arg). Nada de `C:\Users\Usuario` no código.

`save_config(updates)` grava apenas chaves conhecidas e aplica em memória; reinício não é necessário para `models_dir`/`separados_root` (o `AIAnalyzer` é reconstruído sob demanda).

### 8.2 `doctor.py`

`run_checks(config) -> list[CheckResult]` com `CheckResult(id, label, status, detail, fix_url, fix_label, action)`.

| Check | ok | warn | error |
|---|---|---|---|
| `ffmpeg` | `ffmpeg -version` responde | — | não encontrado (importa só formatos nativos) |
| `vbcable` | device `CABLE Input/Output` presente | — | ausente (sem rota para cabo) |
| `models` | pyannote + titanet presentes | falta `referencias_vozes.npz`/whisper (baixa no 1º uso) | pasta ausente |
| `ollama` | `/api/tags` responde | offline (usa heurística) | — |
| `data_writable` | escrita/remoção de arquivo teste | — | sem permissão |
| `webview2` | chave de registro presente | — | ausente (não abre janela) |

Cache de 60 s; `refresh=True` ignora. Probes têm timeout ≤ 2 s e nunca levantam.

**Capabilities** derivadas: `ai_analysis`, `audio_convert`, `cable_routing`, `local_llm`.
- `get_initial_state` inclui `environment: {checks, capabilities}`.
- `get_environment_status(refresh=False)`.
- `open_help_url(url)` aceita apenas `https` + hosts da whitelist (`vb-audio.com`, `ffmpeg.org`, `gyan.dev`, `ollama.com`, `learn.microsoft.com`).
- `pick_directory(kind)` usa `FOLDER_DIALOG` do pywebview para `models_dir`/`separados_root`.

**Gating**
- `reanalyze_clip_ai`/`split`/`enroll` → erro claro se `ai_analysis` falso.
- Import de formato não nativo exige `audio_convert`.
- Rota para cabo exige `cable_routing`.
- UI desabilita botões com `title` explicando e mostra banner "Ambiente incompleto — 2 pendências" com botão **Configurar**.

**UI**
- Botão ⚙️ no topo → modal "Ambiente" com lista de checks (✓/⚠/✗), detalhe, botão de ação, "Verificar novamente" e campos de pasta com "Alterar".
- Primeira execução com erro abre o modal automaticamente.

**Testes**: precedência de config, fallback legado, cada probe com monkeypatch, capabilities, whitelist de URL, contrato do estado inicial.

---

## 9. Pacote B — Tray, instância única, logs, launcher

- **Dependências**: `pystray`, `Pillow`; ícone gerado em `ui/icon.png` se ausente.
- **CLI**: `--minimized` (abre no tray), `--debug` (console + log nível DEBUG), `--no-tray`.
- **Tray**: menu Abrir, Iniciar com o Windows (check), Abrir pasta de dados, Sair. Fechar a janela → esconde (não encerra). Sair só pelo menu ou `--no-tray`.
- **Instância única**: mutex nomeado `Global\SoundboardAI` (`ctypes`); segunda instância mostra "O Soundboard já está aberto" e encerra.
- **Logs**: `logging` com `RotatingFileHandler` em `data/logs/soundboard.log` (5 MB × 3), captura `sys.excepthook` e `threading.excepthook`; console só com `--debug`.
- **Autostart**: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\SoundboardAI` → launcher; toggle no tray.
- **Launcher**: `Soundboard AI.vbs` roda `uv run --python 3.13 main.py --minimized` com janela oculta (`WScript.Shell.Run(..., 0, False)`); `iniciar_soundboard.bat` vira modo diagnóstico (`--debug`), mantendo o console apenas para troubleshooting.
- **Testes**: parsing de CLI, toggle de autostart com registro monkeypatched, single-instance acquire/release, caminho do log criado; tray é fino (sem teste de UI).

## 10. Pacote C — Persistência

- `state["schema_version"] = 1`; `_load` migra versões antigas (v0 = sem chave) e grava `.bak` antes de migrar.
- `save()` mantém escrita atômica atual (tmp + `os.replace`) e `_dirty` só limpa após sucesso.
- **Write-behind**: um único worker (fila + `Condition`) substitui os `threading.Timer`; `mark_dirty()` coalesce; `flush()` drena e grava; `atexit` usa `flush()`.
- `increment_play_count` deixa de gravar síncrono: marca dirty.
- Gatilho para SQLite (documentado, não implementado): >2.000 clipes **ou** save >150 ms **ou** JSON >10 MB. O formato versionado permite migrar sem perder dados.

**Testes**: migração v0→v1, `.bak` criado, coalescência (N marks → 1 write), flush grava, falha mantém dirty e não deixa `.tmp`.

## 11. Pacote D — LUFS

- Novo `loudness.py`: K-weighting BS.1770-4 (biquads a 48 kHz), blocos de 400 ms com 75% de sobreposição, gating absoluto −70 LUFS e relativo −10 LU.
- `integrated_lufs(mono_48k) -> float | None`; clipes < 400 ms usam RMS→LUFS aproximado com flag `lufs_estimated`.
- Ganho por clipe: `gain_db = clamp(-16 - lufs, -12, +12)`; pico (aproximado) limitado a −1 dBFS.
- Persistido em `clip["lufs"]`, `clip["loudness_gain_db"]`, `clip["lufs_estimated"]`; recalculado em import, trim e reanálise.
- Playback aplica `gain_db` além do volume do usuário; `normalize_on_import` continua controlando o pico para exibição.
- UI: no modal de edição, badge "Nível: −16,2 LUFS" + botão "Reanalisar nível".
- **Testes**: seno 1 kHz a −23 dBFS ≈ −23 LUFS (±0.3); silêncio → `None`; gating ignora trecho silencioso; ganho clampado; recálculo após trim.

## 12. Pacote E — Mixer persistente

- `AudioMixer` com **um** `OutputStream` por device (cabo e monitor) a 48 kHz/1024 frames; callback mistura vozes ativas.
- `Voice`: `play_id, clip_id, data (read-only), pos, gain, trim, loop, paused`; estado sob lock; operações `start/stop/pause/seek`.
- Crossfade de 10 ms no stop (evita clique) e transição de volume por rampa.
- Ducking por envelope já do F1, aplicado por voz/grupo no mix.
- **Limiter**: lookahead de 5 ms + ceiling −1 dBTP (`tanh` como último recurso); sem clipping digital.
- `_sessions` e `get_session` mantêm o contrato público; `stop_all`, `set_live_clip_volume`, `pause/resume/seek` inalterados para a UI.
- **Fallback**: `mixer_mode=legacy` usa o motor atual por um release; setting exposto no doctor/UI avançada.
- **Testes**: backend de saída falso (pull manual do callback) — duas vozes somam, ganho/duck aplicam, loop retorna ao início, seek/pause, limiter no teto, stop com crossfade; nenhum device real.

---

## 13. Contratos transversais

- Toda API retorna `{ok: bool, error?: str}`; erros em pt-BR.
- Listas/objetos devolvidos à UI são snapshots (`copy.deepcopy`/`store.snapshot()`).
- Nenhum caminho fora de `data/` é escrito; validação por `Path.resolve()` + `is_relative_to`.
- `pytest` cobre tudo sem hardware; smoke test manual documentado em cada pacote.
- Flags: `SOUNDBOARD_MIXER=legacy|v2`, `duck_enabled=false` por padrão.

## 14. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| Mixer introduzir clicks/latência | flag legacy, testes de callback, smoke test com hardware antes do merge |
| Ducking com mic ruidoso bombear | threshold ajustável, attack/release conservadores, default desligado |
| Import de pack trazer lixo | validação rígida de zip, limites, dedupe e warnings |
| Migração de schema perder dados | `.bak` + migração testada + versão explícita |
| Doctor dar falso negativo | probes com timeout, cache curto, botão "verificar novamente", link em vez de bloqueio |
| Undo restaurar arquivo errado | validação de path dentro de `clips_dir` |

## 15. Decisões registradas

| Decisão | Escolha | Por quê |
|---|---|---|
| Persistência | JSON versionado + write-behind agora; SQLite só no gatilho do §10 | evita migração grande sem necessidade comprovada; o schema versionado deixa a porta aberta |
| Draft do replay | clipe `pending` no store, oculto da UI e dos hotkeys | reusa play/trim/waveform sem API nova de "tocar arquivo arbitrário" |
| Ducking | default desligado, −12 dB, threshold −40 dBFS | ligar sem o usuário pedir surpreende; valores conservadores |
| Identidade de pack | nome do interlocutor, nunca id | ids são locais; nome é o que o usuário reconhece |
| Undo | soft delete, arquivo preservado, 8 s | hoje o arquivo já é preservado; custo quase zero |
| Mixer | flag `legacy` por um release | permite reverter sem hotfix se o hardware se comportar mal |
| Empacotamento | não fazer PyInstaller agora | não resolve driver/admin e adiciona centenas de MB; o doctor cobre o atrito |

## 16. Deploy e verificação

Por pacote:
1. worktree + branch `feat/<pacote>`.
2. implementação por subagentes com arquivos disjuntos.
3. `uv run pytest -q`, `node --check ui/app.js`, `python -m compileall`, `git diff --check`.
4. revisor independente (read-only) — corrigir BLOCKER/HIGH/MEDIUM.
5. merge na `main`, remover worktree/branch.
6. smoke test manual do pacote (quando envolver áudio/UI).

## 17. Critérios de aceite globais

- Instalação existente abre sem migração manual e sem perder clipes.
- Suíte verde em todos os pacotes; nenhum teste depende de device real.
- UI nunca mostra stack trace; toda falha vira toast/mensagem com ação.
- Nenhum `C:\Users\...` no código após o pacote A.
