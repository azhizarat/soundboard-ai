"""Pacote A config: precedence, legacy fallback and atomic save."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """No SOUNDBOARD_* leakage and a fake home so the dev machine never leaks in."""
    for name in list(config.ENV_KEYS.values()) + [config.ENV_DATA_DIR]:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


def _data_dir(tmp_path: Path) -> Path:
    return (tmp_path / "data").resolve()


def test_defaults_are_portable(tmp_path):
    data_dir = _data_dir(tmp_path)

    cfg = config.load_config(data_dir=str(data_dir))

    assert cfg.data_dir == data_dir
    assert data_dir.is_dir()
    assert cfg.models_dir == data_dir / "models"
    assert cfg.separados_root == (tmp_path / "home" / "Videos").resolve()
    assert cfg.ffmpeg_path == "ffmpeg"
    assert cfg.ollama_url == "http://127.0.0.1:11434"
    assert cfg.ollama_model == "qwen2.5:1.5b"
    assert cfg.mixer_mode == "mixer"


def test_legacy_models_dir_wins_when_present(tmp_path):
    legacy = tmp_path / "home" / "Videos" / ".modelos_voz"
    legacy.mkdir(parents=True)

    cfg = config.load_config(data_dir=str(_data_dir(tmp_path)))

    assert cfg.models_dir == legacy.resolve()


def test_file_config_loses_to_env(tmp_path, monkeypatch, isolated_env):
    data_dir = _data_dir(tmp_path)
    data_dir.mkdir(parents=True)
    (data_dir / "config.json").write_text(
        json.dumps({
            "ffmpeg_path": "C:/arquivo/ffmpeg.exe",
            "ollama_url": "http://arquivo:1234/",
            "ollama_model": "modelo-do-arquivo",
            "mixer_mode": "MIXER",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("SOUNDBOARD_FFMPEG", "C:/env/ffmpeg.exe")
    monkeypatch.setenv("SOUNDBOARD_OLLAMA_URL", "http://env:5678///")

    cfg = config.load_config(data_dir=str(data_dir))

    assert cfg.ffmpeg_path == "C:/env/ffmpeg.exe"
    assert cfg.ollama_url == "http://env:5678"
    assert cfg.ollama_model == "modelo-do-arquivo"
    assert cfg.mixer_mode == "mixer"


def test_file_config_is_used_without_env(tmp_path):
    data_dir = _data_dir(tmp_path)
    data_dir.mkdir(parents=True)
    (data_dir / "config.json").write_text(
        json.dumps({"ffmpeg_path": "C:/arquivo/ffmpeg.exe", "ollama_url": "http://arquivo:1234/"}),
        encoding="utf-8",
    )

    cfg = config.load_config(data_dir=str(data_dir))

    assert cfg.ffmpeg_path == "C:/arquivo/ffmpeg.exe"
    assert cfg.ollama_url == "http://arquivo:1234"


def test_invalid_mixer_mode_falls_back_to_default(tmp_path):
    data_dir = _data_dir(tmp_path)
    data_dir.mkdir(parents=True)
    (data_dir / "config.json").write_text(json.dumps({"mixer_mode": "turbo"}), encoding="utf-8")

    assert config.load_config(data_dir=str(data_dir)).mixer_mode == "mixer"


def test_data_dir_comes_only_from_arg_or_env(tmp_path, monkeypatch):
    data_dir = _data_dir(tmp_path)
    data_dir.mkdir(parents=True)
    (data_dir / "config.json").write_text(
        json.dumps({"data_dir": str(tmp_path / "from_file")}),
        encoding="utf-8",
    )

    assert config.load_config(data_dir=str(data_dir)).data_dir == data_dir

    from_env = (tmp_path / "from_env").resolve()
    monkeypatch.setenv(config.ENV_DATA_DIR, str(from_env))
    cfg = config.load_config()
    assert cfg.data_dir == from_env
    assert from_env.is_dir()


def test_relative_and_home_paths_are_resolved(tmp_path, monkeypatch):
    data_dir = _data_dir(tmp_path)
    monkeypatch.setenv("SOUNDBOARD_MODELS_DIR", "rel/models")
    monkeypatch.setenv("SOUNDBOARD_SEPARADOS_ROOT", "~/Clips")

    cfg = config.load_config(data_dir=str(data_dir))

    assert cfg.models_dir == data_dir / "rel" / "models"
    assert cfg.models_dir.is_absolute()
    assert cfg.separados_root == (tmp_path / "home" / "Clips").resolve()


def test_corrupt_or_non_dict_json_does_not_raise(tmp_path):
    data_dir = _data_dir(tmp_path)
    data_dir.mkdir(parents=True)

    (data_dir / "config.json").write_text("{not json", encoding="utf-8")
    cfg = config.load_config(data_dir=str(data_dir))
    assert cfg.models_dir == data_dir / "models"
    assert cfg.mixer_mode == "mixer"

    (data_dir / "config.json").write_text("[1, 2, 3]", encoding="utf-8")
    cfg = config.load_config(data_dir=str(data_dir))
    assert cfg.ffmpeg_path == "ffmpeg"


def test_save_config_merges_known_keys_and_leaves_no_tmp(tmp_path):
    data_dir = _data_dir(tmp_path)
    cfg = config.load_config(data_dir=str(data_dir))
    config.save_config(cfg, {"ollama_model": "primeiro"})

    saved = config.save_config(
        cfg,
        {"ffmpeg_path": "C:/novo/ffmpeg.exe", "data_dir": str(tmp_path / "hack"), "desconhecida": 1},
    )

    assert saved is not cfg
    assert isinstance(saved, config.AppConfig)
    assert saved.ffmpeg_path == "C:/novo/ffmpeg.exe"
    assert saved.ollama_model == "primeiro"
    assert saved.data_dir == data_dir

    raw = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))
    assert raw["ffmpeg_path"] == "C:/novo/ffmpeg.exe"
    assert raw["ollama_model"] == "primeiro"
    assert "data_dir" not in raw
    assert "desconhecida" not in raw
    assert not (data_dir / "config.json.tmp").exists()

    reloaded = config.load_config(data_dir=str(data_dir))
    assert reloaded.ffmpeg_path == "C:/novo/ffmpeg.exe"
    assert reloaded.ollama_model == "primeiro"


def test_save_config_normalizes_values(tmp_path):
    data_dir = _data_dir(tmp_path)
    cfg = config.load_config(data_dir=str(data_dir))

    saved = config.save_config(
        cfg,
        {
            "models_dir": "rel/models",
            "separados_root": "~/Clips",
            "ollama_url": "http://127.0.0.1:9999///",
            "mixer_mode": "MIXER",
        },
    )

    assert saved.models_dir == data_dir / "rel" / "models"
    assert saved.separados_root == (tmp_path / "home" / "Clips").resolve()
    assert saved.ollama_url == "http://127.0.0.1:9999"
    assert saved.mixer_mode == "mixer"


def test_save_config_from_relative_env_keeps_absolute_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "app_dir", lambda: tmp_path)
    monkeypatch.setenv(config.ENV_DATA_DIR, "rel_data")

    cfg = config.load_config()
    assert cfg.data_dir == (tmp_path / "rel_data").resolve()
    assert cfg.data_dir.is_absolute()

    saved = config.save_config(cfg, {"ollama_model": "outro"})

    assert saved.data_dir == cfg.data_dir
    assert saved.data_dir.is_absolute()


def test_default_data_dir_is_next_to_the_app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "app_dir", lambda: tmp_path)

    cfg = config.load_config()

    assert cfg.data_dir == (tmp_path / "data").resolve()
    assert cfg.data_dir.is_dir()


def test_config_dict_is_json_safe(tmp_path):
    cfg = config.load_config(data_dir=str(_data_dir(tmp_path)))

    data = config.config_dict(cfg)

    assert isinstance(data["data_dir"], str)
    assert isinstance(data["models_dir"], str)
    assert isinstance(data["separados_root"], str)
    assert json.loads(json.dumps(data))["data_dir"] == str(cfg.data_dir)
