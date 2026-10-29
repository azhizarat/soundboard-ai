# Pacote A — Config portátil + doctor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Tirar os caminhos de máquina do código (env > `data/config.json` > default), diagnosticar o ambiente (FFmpeg, VB-Cable, modelos, Ollama, permissões, WebView2) e degradar com clareza em vez de quebrar.

**Architecture:** `config.py` é um módulo puro (dataclass congelada + load/save) usado por `main`, `ai_analyzer` e `audio_engine`. `doctor.py` roda probes com timeout curto, nunca levanta, e deriva *capabilities*. `SoundboardAPI` expõe o ambiente no estado inicial e aplica gating nas ações que dependem dele. A UI só reflete o que o backend informa.

**Spec:** `docs/superpowers/specs/2026-10-28-soundboard-roadmap-design.md` §8.

**Baseline:** `3e3c8d3` (F2 merged).

---

## Contratos

```python
# config.py
@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    models_dir: Path
    separados_root: Path
    ffmpeg_path: str = "ffmpeg"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:1.5b"
    mixer_mode: str = "legacy"

def load_config(data_dir=None) -> AppConfig
def save_config(config: AppConfig, updates: dict) -> AppConfig   # devolve nova instância
def config_dict(config: AppConfig) -> dict                        # JSON-safe, para a UI
```

```python
# doctor.py
@dataclass(frozen=True)
class CheckResult:
    id: str            # ffmpeg | vbcable | models | ollama | data_writable | webview2
    label: str
    status: str        # "ok" | "warn" | "error"
    detail: str = ""
    fix_url: str = ""
    fix_label: str = ""
    action: str = ""   # "" | "open_url" | "pick_models_dir" | "pick_separados_root"

def run_checks(config, refresh=False) -> list[CheckResult]
def capabilities(checks) -> dict[str, bool]   # ai_analysis, audio_convert, cable_routing, local_llm
def snapshot(config, refresh=False) -> dict    # {checks, capabilities, incomplete, checked_at}
def invalidate_cache() -> None
def safe_help_url(url) -> str | None          # https + whitelist
```
Snapshot shape (vai direto para o JS):
```json
{"checks": [{"id","label","status","detail","fix_url","fix_label","action"}],
 "capabilities": {"ai_analysis": true, "audio_convert": false, "cable_routing": true, "local_llm": false},
 "incomplete": 1, "checked_at": 1760000000.0}
```

**API nova em `SoundboardAPI`**
- `get_environment_status(refresh=False) -> snapshot`
- `refresh_environment() -> snapshot` (atalho com `refresh=True`)
- `save_app_config(updates: dict) -> {ok, config, environment, error?}`
- `pick_directory(kind: str) -> {ok, path?, config?, environment?, error?}` — `kind` ∈ `models_dir|separados_root`
- `open_help_url(url: str) -> {ok, error?}`

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `config.py` (novo) | AppConfig, precedência, load/save atômico |
| `doctor.py` (novo) | probes, cache 60 s, capabilities, whitelist de URL |
| `ai_analyzer.py` | runtime config (ffmpeg/ollama) sem globais hardcoded |
| `audio_engine.py` | `ffmpeg_path` injetável |
| `main.py` | wiring, ambiente no estado inicial, endpoints, gating |
| `tests/test_a_config.py` (novo) | precedência, legado, save |
| `tests/test_a_doctor.py` (novo) | probes, cache, capabilities, URL |
| `tests/test_a_api.py` (novo) | estado inicial, endpoints, gating |
| `ui/index.html` | botão ⚙️, modal Ambiente, banner |
| `ui/app.js` | render do ambiente, ações, gating |
| `ui/styles.css` | modal/banner |

---

### Task 1: `config.py`

**Files:** `config.py` (novo), `tests/test_a_config.py`

```python
"""Portable runtime configuration: env > data/config.json > defaults."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

ENV_DATA_DIR = "SOUNDBOARD_DATA_DIR"
KNOWN_KEYS = (
    "models_dir",
    "separados_root",
    "ffmpeg_path",
    "ollama_url",
    "ollama_model",
    "mixer_mode",
)
ENV_KEYS = {
    "models_dir": "SOUNDBOARD_MODELS_DIR",
    "separados_root": "SOUNDBOARD_SEPARADOS_ROOT",
    "ffmpeg_path": "SOUNDBOARD_FFMPEG",
    "ollama_url": "SOUNDBOARD_OLLAMA_URL",
    "ollama_model": "SOUNDBOARD_OLLAMA_MODEL",
    "mixer_mode": "SOUNDBOARD_MIXER",
}
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:1.5b"
MIXER_MODES = ("legacy", "mixer")


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    models_dir: Path
    separados_root: Path
    ffmpeg_path: str = "ffmpeg"
    ollama_url: str = DEFAULT_OLLAMA_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    mixer_mode: str = "legacy"


def app_dir() -> Path:
    return Path(__file__).parent.resolve()


def _as_path(value, base: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _read_file_config(data_dir: Path) -> dict:
    path = data_dir / "config.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in KNOWN_KEYS if raw.get(k) not in (None, "")}


def _default_models_dir(data_dir: Path) -> Path:
    # Legacy install: the models used to live next to the videos folder.
    legacy = Path.home() / "Videos" / ".modelos_voz"
    if legacy.is_dir():
        return legacy.resolve()
    return (data_dir / "models").resolve()


def load_config(data_dir=None) -> AppConfig:
    """Boots data_dir (env/arg only) and resolves every other value by precedence."""
    raw_dir = str(data_dir or os.environ.get(ENV_DATA_DIR) or (app_dir() / "data"))
    base = _as_path(raw_dir, app_dir())
    base.mkdir(parents=True, exist_ok=True)

    file_cfg = _read_file_config(base)
    values = {
        "models_dir": _default_models_dir(base),
        "separados_root": (Path.home() / "Videos").resolve(),
        "ffmpeg_path": "ffmpeg",
        "ollama_url": DEFAULT_OLLAMA_URL,
        "ollama_model": DEFAULT_OLLAMA_MODEL,
        "mixer_mode": "legacy",
    }
    for key in KNOWN_KEYS:
        if key in file_cfg:
            values[key] = file_cfg[key]

    for key, env_name in ENV_KEYS.items():
        env_value = os.environ.get(env_name)
        if env_value:
            values[key] = env_value

    for key in ("models_dir", "separados_root"):
        values[key] = _as_path(values[key], base)
    values["ffmpeg_path"] = str(values["ffmpeg_path"])
    values["ollama_url"] = str(values["ollama_url"]).rstrip("/")
    values["ollama_model"] = str(values["ollama_model"])
    mixer = str(values["mixer_mode"]).lower()
    values["mixer_mode"] = mixer if mixer in MIXER_MODES else "legacy"
    return AppConfig(data_dir=base, **values)


def save_config(config: AppConfig, updates: dict) -> AppConfig:
    """Persists only known keys (never data_dir) and returns the new config."""
    clean = {k: v for k, v in (updates or {}).items() if k in KNOWN_KEYS and v not in (None, "")}
    if not clean:
        return config
    path = config.data_dir / "config.json"
    current = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception:
            current = {}
    if not isinstance(current, dict):
        current = {}
    current.update(clean)
    persisted = {k: current[k] for k in KNOWN_KEYS if current.get(k) not in (None, "")}

    tmp = path.with_name("config.json.tmp")
    try:
        tmp.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise

    merged = asdict(config)
    merged.update(persisted)
    merged["data_dir"] = config.data_dir
    for key in ("models_dir", "separados_root"):
        merged[key] = _as_path(merged[key], config.data_dir)
    merged["mixer_mode"] = str(merged["mixer_mode"]).lower()
    if merged["mixer_mode"] not in MIXER_MODES:
        merged["mixer_mode"] = "legacy"
    return AppConfig(**merged)


def config_dict(config: AppConfig) -> dict:
    data = asdict(config)
    data["data_dir"] = str(config.data_dir)
    data["models_dir"] = str(config.models_dir)
    data["separados_root"] = str(config.separados_root)
    return data
```

**Testes** (`tests/test_a_config.py`, sempre com `monkeypatch.setenv/delenv` + `monkeypatch.setattr(Path, "home", ...)` quando precisar do legado):
1. default: sem env/arquivo → `data_dir/<app>/data`, `models_dir = data_dir/models`, `separados_root = home/Videos`, `mixer_mode == "legacy"`.
2. legado: `home/Videos/.modelos_voz` existe → vira `models_dir`.
3. precedência: arquivo define `ffmpeg_path`/`ollama_url`; env sobrescreve; `mixer_mode` inválido cai para `legacy`; `ollama_url` perde a barra final.
4. `data_dir` vem só de arg/env (um `data_dir` dentro do JSON é ignorado).
5. `save_config` ignora chaves desconhecidas e `data_dir`, grava atômico (sem `.tmp` sobrando), recarrega com `load_config` e reproduz os valores.
6. JSON corrompido não quebra `load_config` (usa defaults).

---

### Task 2: `doctor.py`

**Files:** `doctor.py` (novo), `tests/test_a_doctor.py`

```python
"""Environment diagnostics: short timeouts, never raises, 60s cache."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

CACHE_TTL = 60.0
PROBE_TIMEOUT = 2.0
ALLOWED_HELP_HOSTS = ("vb-audio.com", "ffmpeg.org", "gyan.dev", "ollama.com", "learn.microsoft.com")
_CACHE = {"at": 0.0, "key": None, "checks": []}

FFMPEG_FIX = "https://www.gyan.dev/ffmpeg/builds/"
VBCABLE_FIX = "https://vb-audio.com/Cable/"
OLLAMA_FIX = "https://ollama.com/download"
WEBVIEW2_FIX = "https://learn.microsoft.com/microsoft-edge/webview2/"


@dataclass(frozen=True)
class CheckResult:
    id: str
    label: str
    status: str
    detail: str = ""
    fix_url: str = ""
    fix_label: str = ""
    action: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"
```

Probes (todos `try/except Exception` → `error` com `detail=str(exc)[:200]`):

- `_check_ffmpeg(config)`: `subprocess.run([config.ffmpeg_path, "-version"], capture_output=True, timeout=PROBE_TIMEOUT)`; rc 0 → ok "FFmpeg encontrado"; `FileNotFoundError` → error "FFmpeg não encontrado (só formatos nativos)" + `FFMPEG_FIX`.
- `_check_vbcable(config)`: `import sounddevice as sd; names = [d["name"] for d in sd.query_devices()]`; any name contendo `CABLE Input` ou `CABLE Output` → ok; senão error + `VBCABLE_FIX`.
- `_check_models(config)`: `seg = models_dir/"pyannote-segmentation-3-0"/"model.onnx"`, `emb = models_dir/"nemo_en_titanet_large.onnx"`; pasta inexistente → error; ambos → ok; um faltando → warn com lista do que falta; `referencias_vozes.npz` ausente **não** rebaixa (detalhe em ok/warn); `action="pick_models_dir"`, `fix_label="Escolher pasta"`.
- `_check_ollama(config)`: `urllib.request.urlopen(config.ollama_url + "/api/tags", timeout=PROBE_TIMEOUT)`; 200 → ok; `URLError/OSError` → warn "Ollama offline — usando heurística local" + `OLLAMA_FIX`.
- `_check_data_writable(config)`: cria `data_dir/".doctor_probe"` escreve/lê/apaga; erro → error.
- `_check_webview2(config)`: fora do Windows → ok; Windows: `winreg` em HKCU/HKLM `Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`; ausente → error + `WEBVIEW2_FIX`.

`run_checks(config, refresh=False)`:
- chave do cache = `(str(config.data_dir), str(config.models_dir), str(config.separados_root), config.ffmpeg_path, config.ollama_url)`; se `not refresh` e mesma chave e `time.time()-at < CACHE_TTL` → devolve cache.
- roda os 6 probes na ordem da tabela do spec, grava cache, devolve lista.

`capabilities(checks)`:
```python
by_id = {c.id: c for c in checks}
return {
    "ai_analysis": by_id.get("models") is not None and by_id["models"].status != "error",
    "audio_convert": by_id.get("ffmpeg") is not None and by_id["ffmpeg"].status == "ok",
    "cable_routing": by_id.get("vbcable") is not None and by_id["vbcable"].status == "ok",
    "local_llm": by_id.get("ollama") is not None and by_id["ollama"].status == "ok",
}
```

`snapshot(config, refresh=False)` → `{"checks": [asdict(c) for c in run_checks(...)], "capabilities": ..., "incomplete": sum(status != "ok"), "checked_at": time.time()}`.

`invalidate_cache()` → zera `_CACHE`.

`safe_help_url(url)` → `None` se não for `https`, host fora da whitelist (comparar host e subdomínios: `host == h or host.endswith("." + h)`), ou se tiver usuário/senha.

**Testes** (`tests/test_a_doctor.py`) — monkeypatch em tudo, sem rede/dispositivo real:
1. ffmpeg ok (fake `subprocess.run` rc 0) / ausente (`FileNotFoundError`).
2. vbcable presente/ausente (fake `sounddevice.query_devices`).
3. models: pasta ausente → error; só um modelo → warn; ambos → ok.
4. ollama: `urlopen` fake 200 → ok; `URLError` → warn.
5. data_writable em pasta somente-leitura (monkeypatch `Path.write_text` raising) → error.
6. webview2 monkeypatched (winreg import falha / chave ausente / presente).
7. cache: duas chamadas contam probes uma vez; `refresh=True` roda de novo; `invalidate_cache` limpa.
8. cada probe com exceção inesperada vira `error` (nunca propaga).
9. `capabilities` para cada combinação relevante (sem modelos → `ai_analysis` False; sem ffmpeg → `audio_convert` False; ollama warn → `local_llm` False).
10. `safe_help_url`: aceita `https://vb-audio.com/...`, `https://www.gyan.dev/...`; recusa `http://`, `https://evil.com`, `https://vb-audio.com.evil.com`, `javascript:`.

---

### Task 3: `ai_analyzer.py` e `audio_engine.py`

**Files:** `ai_analyzer.py`, `audio_engine.py`

**`ai_analyzer.py`**
- Remover `VIDEOS_MODELS_DIR = Path(r"C:\Users\Usuario\Videos\.modelos_voz")` do topo (o fallback agora vem do `AppConfig`).
- Runtime mutável:
```python
_RUNTIME = {"ffmpeg": "ffmpeg", "ollama_url": "http://127.0.0.1:11434", "ollama_model": "qwen2.5:1.5b"}

def configure_runtime(ffmpeg_path: str = None, ollama_url: str = None, ollama_model: str = None) -> None:
    if ffmpeg_path: _RUNTIME["ffmpeg"] = str(ffmpeg_path)
    if ollama_url: _RUNTIME["ollama_url"] = str(ollama_url).rstrip("/")
    if ollama_model: _RUNTIME["ollama_model"] = str(ollama_model)
```
- `load_audio_mono_16k` usa `_RUNTIME["ffmpeg"]` no lugar de `"ffmpeg"`.
- `_query_local_ollama` usa host/port de `urllib.parse.urlsplit(_RUNTIME["ollama_url"])` (default 127.0.0.1:11434 quando o parse falhar) e `_RUNTIME["ollama_model"]` / `f"{_RUNTIME['ollama_url']}/api/generate"`.
- `AIAnalyzer.__init__(self, models_dir=None, ffmpeg_path=None, ollama_url=None, ollama_model=None)` chama `configure_runtime(...)` quando os valores vêm preenchidos e mantém o resto da lógica (models_dir explícito > default do config já resolvido > `data/models`).
- Nenhum `C:\Users\...` no arquivo.

**`audio_engine.py`**
- `AudioEngine.__init__(..., ffmpeg_path: str = "ffmpeg")` → `self.ffmpeg_path = ffmpeg_path or "ffmpeg"`; no fallback de decode usar `self.ffmpeg_path`.

---

### Task 4: `main.py` — wiring, ambiente e gating

**Files:** `main.py`, `tests/test_a_api.py`

**Wiring**
- Imports: `import config`, `import doctor`, `import webbrowser`.
- `__init__`: antes do store,
```python
        self._config = config.load_config(data_dir)
        data_dir = str(self._config.data_dir)
```
  e o store passa a usar esse `data_dir`.
- `AIAnalyzer(str(self._config.models_dir), self._config.ffmpeg_path, self._config.ollama_url, self._config.ollama_model)`.
- `AudioEngine(..., ffmpeg_path=self._config.ffmpeg_path)` (ver assinatura atual: `replay_seconds`, `start_loopback`).
- `get_initial_state`: `videos_root = self._config.separados_root` (some o `C:\Users\Usuario`); adicionar
```python
            "environment": doctor.snapshot(self._config),
            "config": config.config_dict(self._config),
```
- Calcular `env = doctor.snapshot(self._config)` uma vez por chamada (cache de 60 s evita custo).

**Endpoints**
```python
    def get_environment_status(self, refresh=False) -> dict:
        return doctor.snapshot(self._config, refresh=bool(refresh))

    def refresh_environment(self) -> dict:
        doctor.invalidate_cache()
        return doctor.snapshot(self._config, refresh=True)

    def save_app_config(self, updates: dict) -> dict:
        try:
            self._config = config.save_config(self._config, updates or {})
        except Exception as exc:
            return {"ok": False, "error": f"Não foi possível salvar a configuração: {exc}"}
        self._apply_config()
        doctor.invalidate_cache()
        return {
            "ok": True,
            "config": config.config_dict(self._config),
            "environment": doctor.snapshot(self._config, refresh=True),
        }

    def _apply_config(self):
        """Rebuilds everything that depends on config paths without restarting."""
        self._ai = AIAnalyzer(
            str(self._config.models_dir),
            self._config.ffmpeg_path,
            self._config.ollama_url,
            self._config.ollama_model,
        )
        self._audio.ffmpeg_path = self._config.ffmpeg_path

    def pick_directory(self, kind: str) -> dict:
        if kind not in ("models_dir", "separados_root"):
            return {"ok": False, "error": "Pasta desconhecida."}
        if self._window is None:
            return {"ok": False, "error": "Janela indisponível."}
        try:
            import webview
            chosen = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            path = chosen if isinstance(chosen, str) else (chosen[0] if chosen else None)
        except Exception as exc:
            return {"ok": False, "error": f"Não foi possível abrir o seletor: {exc}"}
        if not path:
            return {"ok": False, "error": "Seleção cancelada."}
        return self.save_app_config({kind: path})

    def open_help_url(self, url: str) -> dict:
        safe = doctor.safe_help_url(url)
        if not safe:
            return {"ok": False, "error": "Link não permitido."}
        try:
            webbrowser.open(safe)
        except Exception as exc:
            return {"ok": False, "error": f"Não foi possível abrir o link: {exc}"}
        return {"ok": True}
```

**Gating**
- Helper:
```python
    def _capability_error(self, name: str) -> dict | None:
        caps = doctor.capabilities(doctor.run_checks(self._config))
        if caps.get(name):
            return None
        messages = {
            "ai_analysis": "Análise de IA indisponível: coloque os modelos em data/models ou abra ⚙️ Ambiente.",
            "audio_convert": "Conversão de áudio indisponível: instale o FFmpeg para importar este formato.",
            "cable_routing": "VB-Cable não encontrado: sem rota para o cabo virtual.",
        }
        return {"ok": False, "error": messages.get(name, "Recurso indisponível.")}
```
- `reanalyze_clip_ai`, `split_clip_by_speakers`, `enroll_speaker_from_clip`: no topo, `blocked = self._capability_error("ai_analysis"); if blocked: return blocked`.
- `import_audio_files`: para cada arquivo, se sufixo em `(".m4a", ".webm", ".aac", ".wma", ".opus")` e `not caps["audio_convert"]` → `continue` (ou pular com aviso no retorno) e, se **nenhum** foi importado, retornar `{"ok": False, "error": "Instale o FFmpeg para importar este formato.", "imported": [], "clips": ..., "speakers": ...}`.
- `_apply_mic_passthrough`: se `enabled` e `not caps["cable_routing"]` → aplica `enabled=False`, e emite `toast` de aviso uma vez (flag `self._cable_warned`).

**Testes** (`tests/test_a_api.py`, hardware-free)
1. `get_initial_state()` traz `environment` com as 6 checks e `config` com `data_dir` igual ao `tmp_path`.
2. `SOUNDBOARD_DATA_DIR` (monkeypatch) muda o `data_dir` do store.
3. `save_app_config({"models_dir": str(tmp/"models"), "foo": 1})` persiste só a chave conhecida, devolve `ok` e `config.models_dir` atualizado, e recria `self._ai` apontando para a nova pasta.
4. `save_app_config` com `models_dir` inválido (não é diretório) ainda salva (config não valida existência) e o snapshot marca `models` como warn/error.
5. `open_help_url` recusa `http://`, host fora da whitelist e aceita `https://vb-audio.com/Cable/` (monkeypatch `webbrowser.open` para registrar).
6. gating: monkeypatch `doctor.capabilities` → `ai_analysis` False faz `reanalyze_clip_ai`/`split_clip_by_speakers`/`enroll_speaker_from_clip` retornarem `ok: False` com mensagem mencionando "IA".
7. `get_environment_status` respeita o cache (duas chamadas → probes uma vez) e `refresh_environment` invalida.
8. `_apply_mic_passthrough` com `cable_routing` False não chama `audio.set_mic_passthrough(enabled=True)`.

---

### Task 5: UI — botão ⚙️, modal Ambiente, banner e gating

**Files:** `ui/index.html`, `ui/app.js`, `ui/styles.css`

**Markup**
- No header, ao lado do badge de status: `<button type="button" id="btnEnvironment" class="btn btn-icon" title="Diagnóstico do ambiente (⚙️)">⚙️</button>` e `<span id="envBadge" class="status-pill status-warn hidden"></span>`.
- Banner abaixo do header: `<div id="envBanner" class="env-banner hidden"><span id="envBannerText"></span><button type="button" id="btnEnvBanner" class="btn btn-secondary">Configurar</button></div>`.
- Modal `#envModal` (`modal-backdrop hidden`, `modal-card` largo) com:
  - header "⚙️ Ambiente" + `#btnCloseEnvModal`;
  - `#envChecks` (lista renderizada);
  - campos de pasta: `#envModelsDir` (readonly) + `#btnPickModelsDir`; `#envSeparadosRoot` + `#btnPickSeparadosRoot`;
  - rodapé: `#btnRefreshEnv` ("🔄 Verificar novamente").

**JS**
```js
function renderEnvironment(env) {
  if (!env) return;
  state.environment = env;
  const caps = env.capabilities || {};
  const pending = Number(env.incomplete || 0);
  const badge = document.getElementById("envBadge");
  if (badge) {
    badge.textContent = `${pending} pendência(s)`;
    badge.classList.toggle("hidden", pending === 0);
    badge.classList.toggle("status-warn", pending > 0);
    badge.classList.toggle("status-ok", pending === 0);
  }
  const banner = document.getElementById("envBanner");
  if (banner) {
    banner.classList.toggle("hidden", pending === 0);
    const text = document.getElementById("envBannerText");
    if (text) text.textContent = `Ambiente incompleto — ${pending} pendência(s)`;
  }
  renderEnvChecks(env.checks || []);
  applyCapabilityGating(caps);
}
```
- `renderEnvChecks`: para cada check, `<li class="env-check ${status}">` com ícone (`✓/⚠/✗`), label, detail (escapeHtml), e botão de ação quando `fix_url` (chama `api().open_help_url`) ou `action === "pick_models_dir"` (chama `api().pick_directory("models_dir")`).
- `applyCapabilityGating(caps)`:
  - `ai_analysis` false → `disabled` + `title` em `#btnReanalyzeAI`, `#btnSplitSpeakers`, `#btnEnrollVoice`;
  - `audio_convert` false → `#btnImportFiles.title` avisa sobre formatos que precisam de FFmpeg;
  - `cable_routing` false → `#micPassthroughToggle.disabled = true`, `title` explicando.
  Nunca mexer em funções existentes além de `disabled`/`title`.
- `openEnvModal()` → mostra modal, chama `api().get_environment_status(false)` e renderiza.
- `#btnRefreshEnv` → `api().refresh_environment()` → renderiza.
- `#btnPickModelsDir` / `#btnPickSeparadosRoot` → `api().pick_directory(kind)`; em `ok`, atualizar `#envModelsDir`/`#envSeparadosRoot` com `res.config.*` e re-renderizar `renderEnvironment(res.environment)`.
- `initApp`: depois do estado inicial, `renderEnvironment(initial.environment)`; se `incomplete > 0` **e** ainda não abriu nesta sessão (`state.envAutoOpened`), abrir o modal automaticamente.
- Evento `environment_updated` (se o backend emitir) → `renderEnvironment(payload)`.

**CSS**: `.env-banner`, `.env-check` (ok/warn/error), `.env-check .env-detail`, `.env-paths`, `#envModal .modal-card { width: min(720px, 94vw); }`.

**Verificação**: `node --check ui/app.js`; todos os ids novos existem no HTML; sem ids duplicados.

---

### Task 6: Verificação e revisão

- [ ] `uv run pytest -q`
- [ ] `node --test tests/test_fuzzy.test.cjs`
- [ ] `node --check ui/app.js` e `python -m compileall -q main.py config.py doctor.py ai_analyzer.py audio_engine.py`
- [ ] `git diff --check`
- [ ] Revisão independente: precedência de config e vazamento de env entre testes, cache do doctor (chave/`refresh`/thread safety), probes que podem travar (timeout real), `safe_help_url` (typosquatting, subdomínio, credenciais), rebuild do `AIAnalyzer` (perda de estado em análise em andamento), gating que possa bloquear demo legítima, e regressão de F1/F2.
- [ ] Smoke test: abrir sem FFmpeg → banner + modal com ✗ e link; com FFmpeg → ✓; trocar `models_dir` pelo modal e reanalisar sem reiniciar; importar `.m4a` sem FFmpeg → erro claro; importar `.wav` continua funcionando.
