"""Environment diagnostics: short timeouts, never raises, 60 s cache."""

from __future__ import annotations

import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

CACHE_TTL = 60.0
PROBE_TIMEOUT = 2.0
ALLOWED_HELP_HOSTS = ("vb-audio.com", "ffmpeg.org", "gyan.dev", "ollama.com", "learn.microsoft.com")
CHECK_ORDER = (
    ("ffmpeg", "FFmpeg"),
    ("vbcable", "VB-Cable"),
    ("models", "Modelos de IA"),
    ("ollama", "Ollama"),
    ("data_writable", "Pasta de dados"),
    ("webview2", "WebView2"),
)

FFMPEG_FIX = "https://www.gyan.dev/ffmpeg/builds/"
VBCABLE_FIX = "https://vb-audio.com/Cable/"
OLLAMA_FIX = "https://ollama.com/download"
WEBVIEW2_FIX = "https://learn.microsoft.com/microsoft-edge/webview2/"
WEBVIEW2_REG_PATH = r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_REG_PATHS = (
    WEBVIEW2_REG_PATH,
    r"Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    r"Software\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    r"Software\WOW6432Node\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
)

_CACHE = {"at": 0.0, "key": None, "checks": []}
_CACHE_LOCK = threading.Lock()


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


def _check_ffmpeg(config) -> CheckResult:
    try:
        proc = subprocess.run(
            [config.ffmpeg_path, "-version"],
            capture_output=True,
            timeout=PROBE_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return CheckResult(
            "ffmpeg",
            "FFmpeg",
            "error",
            "FFmpeg não encontrado (só formatos nativos)",
            FFMPEG_FIX,
            "Instalar FFmpeg",
            "open_url",
        )
    if getattr(proc, "returncode", 1) == 0:
        return CheckResult("ffmpeg", "FFmpeg", "ok", "FFmpeg encontrado")
    return CheckResult(
        "ffmpeg",
        "FFmpeg",
        "error",
        f"FFmpeg respondeu com código {proc.returncode} (só formatos nativos)",
        FFMPEG_FIX,
        "Instalar FFmpeg",
        "open_url",
    )


def _check_vbcable(config) -> CheckResult:
    import sounddevice as sd

    names = [str(device.get("name", "")) for device in sd.query_devices()]
    found = [name for name in names if "CABLE Input" in name or "CABLE Output" in name]
    if found:
        return CheckResult("vbcable", "VB-Cable", "ok", f"Dispositivo encontrado: {found[0]}")
    return CheckResult(
        "vbcable",
        "VB-Cable",
        "error",
        "VB-Cable não encontrado (sem rota para o cabo virtual)",
        VBCABLE_FIX,
        "Instalar VB-Cable",
        "open_url",
    )


def _check_models(config) -> CheckResult:
    models_dir = Path(config.models_dir)
    seg = models_dir / "pyannote-segmentation-3-0" / "model.onnx"
    emb = models_dir / "nemo_en_titanet_large.onnx"
    ref = models_dir / "referencias_vozes.npz"
    action = "pick_models_dir"
    fix_label = "Escolher pasta"

    if not models_dir.is_dir():
        return CheckResult(
            "models",
            "Modelos de IA",
            "error",
            f"Pasta de modelos não encontrada: {models_dir}",
            "",
            fix_label,
            action,
        )

    missing = [
        name
        for name, path in (("pyannote-segmentation-3-0", seg), ("nemo_en_titanet_large.onnx", emb))
        if not path.is_file()
    ]
    if missing:
        detail = "Faltando: " + ", ".join(missing)
        if not ref.is_file():
            detail += " · referencias_vozes.npz será criado no primeiro cadastro"
        status = "error" if len(missing) == 2 else "warn"
        return CheckResult("models", "Modelos de IA", status, detail, "", fix_label, action)

    detail = "Modelos de diarização encontrados"
    if not ref.is_file():
        detail += " · referencias_vozes.npz será criado no primeiro cadastro"
    return CheckResult("models", "Modelos de IA", "ok", detail, "", fix_label, action)


def _check_ollama(config) -> CheckResult:
    url = f"{config.ollama_url}/api/tags"
    status = None
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as response:
            status = getattr(response, "status", 200)
    except (urllib.error.URLError, OSError, ValueError):
        status = None
    if status == 200:
        return CheckResult("ollama", "Ollama", "ok", "Ollama online em /api/tags (opcional)")
    return CheckResult(
        "ollama",
        "Ollama",
        "ok",
        "Motor de decisão local ativo (Ollama offline/opcional)",
        OLLAMA_FIX,
        "Baixar Ollama",
        "open_url",
    )


def _check_data_writable(config) -> CheckResult:
    data_dir = Path(config.data_dir)
    probe = data_dir / ".doctor_probe"
    error = ""
    try:
        probe.write_text("soundboard", encoding="utf-8")
        if probe.read_text(encoding="utf-8") != "soundboard":
            error = "Leitura de teste devolveu conteúdo inesperado."
    except Exception as exc:
        error = str(exc) or "Não foi possível escrever na pasta de dados."
    finally:
        try:
            probe.unlink(missing_ok=True)
        except Exception:
            pass
    if error:
        return CheckResult("data_writable", "Pasta de dados", "error", f"Sem permissão de escrita: {error}")
    return CheckResult("data_writable", "Pasta de dados", "ok", f"Escrita permitida em {data_dir}")


def _check_webview2(config) -> CheckResult:
    if os.name != "nt":
        return CheckResult("webview2", "WebView2", "ok", "Verificação não se aplica fora do Windows")
    try:
        import winreg
    except Exception:
        return CheckResult(
            "webview2",
            "WebView2",
            "error",
            "Não foi possível verificar o WebView2 (winreg indisponível)",
            WEBVIEW2_FIX,
            "Instalar WebView2",
            "open_url",
        )

    # 1. Registry probe (checking 64-bit and 32-bit registry views)
    access_masks = [None]
    if hasattr(winreg, "KEY_READ") and hasattr(winreg, "KEY_WOW64_32KEY"):
        access_masks.append(winreg.KEY_READ | winreg.KEY_WOW64_32KEY)
    if hasattr(winreg, "KEY_READ") and hasattr(winreg, "KEY_WOW64_64KEY"):
        access_masks.append(winreg.KEY_READ | winreg.KEY_WOW64_64KEY)

    found_version = ""
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for subpath in WEBVIEW2_REG_PATHS:
            for mask in access_masks:
                try:
                    if mask is not None:
                        try:
                            key_ctx = winreg.OpenKey(root, subpath, 0, mask)
                        except TypeError:
                            key_ctx = winreg.OpenKey(root, subpath)
                    else:
                        key_ctx = winreg.OpenKey(root, subpath)
                    with key_ctx as key:
                        val, _ = winreg.QueryValueEx(key, "pv")
                        ver = str(val).strip()
                        if ver and ver != "0.0.0.0":
                            found_version = ver
                            break
                except OSError:
                    continue
            if found_version:
                break
        if found_version:
            break

    if found_version:
        return CheckResult("webview2", "WebView2", "ok", f"WebView2 Runtime encontrado ({found_version})")

    # 2. Runtime fallback: if pywebview has an active window, WebView2 is confirmed working
    try:
        import webview
        if getattr(webview, "windows", None) and len(webview.windows) > 0:
            return CheckResult("webview2", "WebView2", "ok", "WebView2 Runtime ativo na sessão atual")
    except Exception:
        pass

    return CheckResult(
        "webview2",
        "WebView2",
        "error",
        "WebView2 Runtime não encontrado (a janela não abre)",
        WEBVIEW2_FIX,
        "Instalar WebView2",
        "open_url",
    )


def _guard(check_id: str, label: str, probe, config) -> CheckResult:
    """A probe must never take the app down: any unexpected error becomes a check result."""
    try:
        return probe(config)
    except Exception as exc:
        return CheckResult(check_id, label, "error", (str(exc) or "Falha inesperada.")[:200])


def _cache_key(config) -> tuple:
    return (
        str(config.data_dir),
        str(config.models_dir),
        str(config.separados_root),
        config.ffmpeg_path,
        config.ollama_url,
    )


def run_checks(config, refresh: bool = False) -> list[CheckResult]:
    """Runs every probe (cached for 60 s unless ``refresh``)."""
    key = _cache_key(config)
    if not refresh:
        with _CACHE_LOCK:
            if _CACHE["key"] == key and (time.time() - _CACHE["at"]) < CACHE_TTL:
                return list(_CACHE["checks"])

    results = []
    for check_id, label in CHECK_ORDER:
        # Read the attribute at call time so tests can replace a single probe.
        probe = globals().get(f"_check_{check_id}")
        if probe is None:
            continue
        results.append(_guard(check_id, label, probe, config))

    with _CACHE_LOCK:
        # Re-check the key: another thread may have stored a newer result meanwhile.
        if not refresh and _CACHE["key"] == key and (time.time() - _CACHE["at"]) < CACHE_TTL:
            return list(_CACHE["checks"])
        _CACHE["at"] = time.time()
        _CACHE["key"] = key
        _CACHE["checks"] = list(results)
    return list(results)


def capabilities(checks) -> dict[str, bool]:
    by_id = {check.id: check for check in checks}
    models = by_id.get("models")
    ffmpeg = by_id.get("ffmpeg")
    vbcable = by_id.get("vbcable")
    ollama = by_id.get("ollama")
    return {
        "ai_analysis": models is not None and models.status != "error",
        "audio_convert": ffmpeg is not None and ffmpeg.status == "ok",
        "cable_routing": vbcable is not None and vbcable.status == "ok",
        "local_llm": ollama is not None and ollama.status == "ok" and "offline" not in (ollama.detail or "").lower(),
    }


def snapshot(config, refresh: bool = False) -> dict:
    checks = run_checks(config, refresh=refresh)
    return {
        "checks": [asdict(check) for check in checks],
        "capabilities": capabilities(checks),
        "incomplete": sum(1 for check in checks if check.status != "ok"),
        "checked_at": time.time(),
    }


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE["at"] = 0.0
        _CACHE["key"] = None
        _CACHE["checks"] = []


def safe_help_url(url) -> str | None:
    """Only https links to whitelisted hosts (or their subdomains) are allowed."""
    if not isinstance(url, str) or not url.strip():
        return None
    candidate = url.strip()
    try:
        parts = urllib.parse.urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() != "https":
        return None
    if parts.username or parts.password:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    for allowed in ALLOWED_HELP_HOSTS:
        if host == allowed or host.endswith(f".{allowed}"):
            return candidate
    return None
