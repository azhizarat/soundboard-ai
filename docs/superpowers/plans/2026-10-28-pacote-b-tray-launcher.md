# Pacote B — Tray, Instância Única, Logs e Launcher

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Prover inicialização em background sem janela de prompt aberta (`Soundboard AI.vbs`), ícone na bandeja do sistema (`pystray`), garantia de instância única (`ctypes` mutex no Windows com fallback), logs rotativos em disco (`data/logs/soundboard.log`), integração de autostart no registro do Windows e controle de flags de CLI (`--minimized`, `--debug`, `--no-tray`).

**Architecture:**
- `single_instance.py`: Mutex nomeado via `ctypes` (`Global\SoundboardAI` com fallback para `Local\SoundboardAI`), aviso de instância já em execução e encerramento limpo.
- `app_logging.py`: Configuração de `RotatingFileHandler` (5 MB × 3 backups) em `data/logs/soundboard.log`, captura de `sys.excepthook` e `threading.excepthook`, console habilitado apenas com `--debug`.
- `autostart.py`: Consulta e alteração da chave de registro `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\SoundboardAI` apontando para o launcher `.vbs`.
- `tray.py`: Criação e gerenciamento do ícone na bandeja com `pystray` e `Pillow` (geração de ícone fallback em `ui/icon.png` se ausente); menu de contexto: Abrir, Iniciar com o Windows (toggle), Abrir pasta de dados, Sair.
- `main.py`: Integração dos argumentos CLI via `argparse`, interceptação do fechamento da janela para minimizar no tray (a menos que `--no-tray` esteja ativo), e wiring de saída limpa.
- `Soundboard AI.vbs`: Launcher silencioso WScript.
- `iniciar_soundboard.bat`: Atualizado para modo diagnóstico (`--debug`).

**Spec:** `docs/superpowers/specs/2026-10-28-soundboard-roadmap-design.md` §9.

---

## Contratos

```python
# single_instance.py
class SingleInstance:
    def __init__(self, name: str = "SoundboardAI", data_dir: Path = None): ...
    def acquire(self) -> bool: ...
    def release(self) -> None: ...
    def notify_already_running(self) -> None: ...
```

```python
# app_logging.py
def setup_logging(data_dir: Path, debug: bool = False) -> Path: ...
# Retorna o caminho do arquivo de log criado (data/logs/soundboard.log)
```

```python
# autostart.py
def get_autostart_command(app_root: Path) -> str: ...
def is_autostart_enabled() -> bool: ...
def set_autostart(enabled: bool, app_root: Path = None) -> bool: ...
```

```python
# tray.py
class SoundboardTray:
    def __init__(self, on_open, on_exit, on_open_data_dir, config, icon_path: Path): ...
    def run_detached(self) -> None: ...
    def stop(self) -> None: ...
```

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `single_instance.py` (novo) | Mutex de instância única e notificação |
| `app_logging.py` (novo) | Rotação de logs em disco e handlers de exceção |
| `autostart.py` (novo) | Integração com registro do Windows para boot |
| `tray.py` (novo) | Ícone na bandeja, menu e ação de minimizar |
| `main.py` | Argumentos CLI, wiring de logging/tray/mutex/window events |
| `Soundboard AI.vbs` (novo) | Launcher silencioso WScript |
| `iniciar_soundboard.bat` | Launcher diagnóstico com `--debug` |
| `tests/test_b_cli.py` (novo) | Testes de argumentos CLI |
| `tests/test_b_single_instance.py` (novo) | Testes de acquire, release e colisão de instâncias |
| `tests/test_b_logging.py` (novo) | Testes de rotação de arquivos de log e hooks |
| `tests/test_b_autostart.py` (novo) | Testes de leitura e escrita no registro (mock) |

---

## Tarefas de Implementação

### Task 1: `single_instance.py` e `tests/test_b_single_instance.py`
- Mutex via `ctypes.windll.kernel32.CreateMutexW` no Windows.
- Fallback em plataforma não-Windows usando lockfile em arquivo.
- `acquire()` retorna `True` se adquiriu o lock com exclusividade; `False` se já existe outra instância.
- `release()` fecha o handle adequadamente.
- `notify_already_running()` exibe mensagem informativa via MessageBox ou console sem travar o processo.
- Testes cobrindo aquisição, liberação e recusa de segunda instância concorrente.

### Task 2: `app_logging.py` e `tests/test_b_logging.py`
- Criar diretório `logs/` dentro de `data_dir`.
- Configurar `RotatingFileHandler` (`soundboard.log`, 5 MB, 3 backups, UTF-8).
- StreamHandler para console condicionado a `debug=True`.
- Hooks `sys.excepthook` e `threading.excepthook` para logar erros não capturados.
- Testes de criação do arquivo de log, escrita de mensagens e acionamento dos hooks.

### Task 3: `autostart.py` e `tests/test_b_autostart.py`
- Usar `winreg` para ler e alterar `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
- Valor: comando para executar `Soundboard AI.vbs` via `wscript.exe`.
- Em ambiente não-Windows ou de teste sem registro, retornar `False` graciosamente.
- Testes com monkeypatch de `winreg` cobrindo ativação, desativação e consulta de status.

### Task 4: `tray.py` e geração de ícone
- Criar imagem RGBA 64x64 em `ui/icon.png` caso o arquivo não exista no disco.
- Integrar com `pystray.Icon`:
  - Menu: "Abrir Soundboard" (default), "Iniciar com o Windows" (checkbox acionando `autostart`), "Abrir pasta de dados", separador, "Sair".
- Executar em thread separada (`run_detached`).
- `stop()` limpa e remove o ícone.

### Task 5: `main.py`, CLI flags, launchers e interceptação de fechamento
- `parse_args()`: `--minimized`, `--debug`, `--no-tray`, `--data-dir`.
- Verificar `single_instance`: se falhar `acquire()`, notifica e encerra com código 0.
- Configurar logging antes de qualquer serviço.
- Configurar tray (a menos que `--no-tray`).
- pywebview `closing` event: se tray ativo, cancelar fechamento e invocar `window.hide()`. Se `--no-tray` ou saída pelo tray, fechar janela e desativar background services.
- Criar `Soundboard AI.vbs` para execução oculta.
- Atualizar `iniciar_soundboard.bat` para adicionar `--debug`.
- Testes em `tests/test_b_cli.py`.

### Task 6: Verificação, revisão e integração
- Rodar suíte completa de testes (`uv run pytest -q`).
- `node --test tests/test_fuzzy.test.cjs`.
- `python -m compileall -q ...` e `git diff --check`.
- Revisão independente.
- Merge `--ff-only` na `main`.
