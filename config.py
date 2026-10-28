"""Portable runtime configuration: env > data/config.json > defaults."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
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
DEFAULT_MIXER_MODE = "mixer"
MIXER_MODES = ("legacy", "mixer")


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    models_dir: Path
    separados_root: Path
    ffmpeg_path: str = "ffmpeg"
    ollama_url: str = DEFAULT_OLLAMA_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    mixer_mode: str = DEFAULT_MIXER_MODE


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
    # Legacy install: the models used to live next to the user's videos folder.
    legacy = Path.home() / "Videos" / ".modelos_voz"
    if legacy.is_dir():
        return legacy.resolve()
    return (data_dir / "models").resolve()


def _finalize(values: dict, base: Path) -> AppConfig:
    """Applies the same post-processing to loaded and saved values."""
    clean = {key: values[key] for key in KNOWN_KEYS}
    for key in ("models_dir", "separados_root"):
        clean[key] = _as_path(clean[key], base)
    clean["ffmpeg_path"] = str(clean["ffmpeg_path"])
    clean["ollama_url"] = str(clean["ollama_url"]).rstrip("/")
    clean["ollama_model"] = str(clean["ollama_model"])
    mixer = str(clean["mixer_mode"]).lower()
    if mixer == "v2":
        mixer = "mixer"
    clean["mixer_mode"] = mixer if mixer in MIXER_MODES else DEFAULT_MIXER_MODE
    return AppConfig(data_dir=base, **clean)


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
        "mixer_mode": DEFAULT_MIXER_MODE,
    }
    values.update(file_cfg)
    for key, env_name in ENV_KEYS.items():
        env_value = os.environ.get(env_name)
        if env_value:
            values[key] = env_value

    return _finalize(values, base)


def save_config(config: AppConfig, updates: dict) -> AppConfig:
    """Persists only known keys (never data_dir) and returns a new config instance."""
    clean = {k: v for k, v in (updates or {}).items() if k in KNOWN_KEYS and v not in (None, "")}

    persisted = {}
    path = config.data_dir / "config.json"
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            persisted = {k: raw[k] for k in KNOWN_KEYS if raw.get(k) not in (None, "")}
    persisted.update(clean)

    if clean:
        tmp = path.with_name("config.json.tmp")
        try:
            tmp.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    merged = asdict(config)
    merged.update(persisted)
    merged["data_dir"] = config.data_dir
    return _finalize(merged, config.data_dir)


def config_dict(config: AppConfig) -> dict:
    """JSON-safe view of the config for the UI."""
    data = asdict(config)
    for key in ("data_dir", "models_dir", "separados_root"):
        data[key] = str(data[key])
    return data
