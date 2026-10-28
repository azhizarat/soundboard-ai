"""Pacote A API: initial state, config endpoints and capability gating.

Everything here is hardware-free and network-free: the doctor probes are
stubbed, no audio device is opened and every write stays inside ``tmp_path``.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

import config
import doctor
import main
from doctor import CheckResult
from main import SoundboardAPI

ENV_KEYS = (
    "SOUNDBOARD_DATA_DIR",
    "SOUNDBOARD_MODELS_DIR",
    "SOUNDBOARD_SEPARADOS_ROOT",
    "SOUNDBOARD_FFMPEG",
    "SOUNDBOARD_OLLAMA_URL",
    "SOUNDBOARD_OLLAMA_MODEL",
    "SOUNDBOARD_MIXER",
)
CHECK_IDS = ["ffmpeg", "vbcable", "models", "ollama", "data_writable", "webview2"]
ALL_CAPABILITIES = {"ai_analysis", "audio_convert", "cable_routing", "local_llm"}


def _check(check_id: str, label: str, status: str = "ok") -> CheckResult:
    return CheckResult(check_id, label, status, "stub")


def _caps(**overrides) -> dict:
    base = {"ai_analysis": True, "audio_convert": True, "cable_routing": True, "local_llm": True}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _doctor_isolation(monkeypatch, tmp_path):
    """Stubs every probe that could touch hardware, the registry or the network."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    doctor.invalidate_cache()
    # Deterministic default: no ffmpeg on this fake machine.
    monkeypatch.setattr(doctor, "_check_ffmpeg", lambda cfg: _check("ffmpeg", "FFmpeg", "error"))
    monkeypatch.setattr(doctor, "_check_vbcable", lambda cfg: _check("vbcable", "VB-Cable"))
    monkeypatch.setattr(doctor, "_check_ollama", lambda cfg: _check("ollama", "Ollama"))
    monkeypatch.setattr(doctor, "_check_webview2", lambda cfg: _check("webview2", "WebView2"))
    # Keep models_dir inside tmp_path regardless of the machine's legacy folder.
    monkeypatch.setenv("SOUNDBOARD_MODELS_DIR", str(tmp_path / "models"))
    yield
    doctor.invalidate_cache()


@pytest.fixture
def api(tmp_path, monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SOUNDBOARD_MODELS_DIR", str(tmp_path / "models"))
    doctor.invalidate_cache()
    api = SoundboardAPI(data_dir=str(tmp_path / "data"), start_background_services=False)
    api._hotkeys.enabled = False
    monkeypatch.setattr(api._hotkeys, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(api._audio, "set_mic_passthrough", lambda *a, **k: None)
    return api


# --- initial state ------------------------------------------------------------


def test_initial_state_exposes_environment_and_json_safe_config(api, tmp_path):
    state = api.get_initial_state()

    env = state["environment"]
    assert [check["id"] for check in env["checks"]] == CHECK_IDS
    assert set(env["capabilities"]) == ALL_CAPABILITIES
    assert isinstance(env["incomplete"], int)
    assert isinstance(env["checked_at"], float)

    data_dir = (tmp_path / "data").resolve()
    models_dir = (tmp_path / "models").resolve()
    assert state["config"]["data_dir"] == str(data_dir)
    assert state["config"]["models_dir"] == str(models_dir)
    assert state["models_dir"] == str(models_dir)
    assert json.loads(json.dumps(state["config"])) == state["config"]


def test_data_dir_env_moves_store_and_config(tmp_path, monkeypatch):
    target = tmp_path / "custom-data"
    monkeypatch.setenv("SOUNDBOARD_DATA_DIR", str(target))

    api = SoundboardAPI(start_background_services=False)
    api._hotkeys.enabled = False
    api._audio.set_mic_passthrough = lambda *a, **k: None

    assert api._config.data_dir == target.resolve()
    assert api._store.data_dir == target.resolve()
    assert (target / "clips").is_dir()


# --- save_app_config ----------------------------------------------------------


def test_save_app_config_persists_only_known_keys(api, tmp_path, monkeypatch):
    new_models = tmp_path / "models2"
    new_models.mkdir()
    events = []
    monkeypatch.setattr(api, "_notify_ui", lambda name, payload=None: events.append((name, payload)))

    result = api.save_app_config({"models_dir": str(new_models), "foo": 1})

    assert result["ok"] is True
    assert result["config"]["models_dir"] == str(new_models.resolve())
    assert api._config.models_dir == new_models.resolve()
    assert api._ai.models_dir == new_models.resolve()

    on_disk = json.loads((tmp_path / "data" / "config.json").read_text(encoding="utf-8"))
    assert "foo" not in on_disk
    assert set(on_disk) <= set(config.KNOWN_KEYS)
    assert on_disk["models_dir"] == str(new_models)

    assert [name for name, _ in events] == ["environment_updated"]
    assert [check["id"] for check in events[0][1]["checks"]] == CHECK_IDS


def test_save_app_config_keeps_missing_models_dir_and_reports_it(api, tmp_path):
    missing = tmp_path / "does_not_exist"

    result = api.save_app_config({"models_dir": str(missing)})

    assert result["ok"] is True
    assert api._config.models_dir == missing.resolve()
    assert api._ai.models_dir == missing.resolve()
    assert not missing.exists()
    models_check = next(c for c in result["environment"]["checks"] if c["id"] == "models")
    assert models_check["status"] in ("warn", "error")
    assert result["environment"]["capabilities"]["ai_analysis"] is False


def test_save_app_config_returns_error_instead_of_raising(api, monkeypatch):
    def boom(current, updates):
        raise OSError("disco cheio")

    monkeypatch.setattr(config, "save_config", boom)

    result = api.save_app_config({"models_dir": "x"})

    assert result["ok"] is False
    assert "Não foi possível salvar a configuração" in result["error"]


# --- open_help_url / pick_directory ------------------------------------------


def test_open_help_url_enforces_whitelist(api, monkeypatch):
    opened = []
    monkeypatch.setattr(main.webbrowser, "open", lambda url: opened.append(url) or True)

    for url in (
        "http://vb-audio.com/Cable/",
        "https://evil.com",
        "https://vb-audio.com.evil.com",
        "https://vb-audio.com@evil.com",
        "javascript:alert(1)",
        "",
    ):
        result = api.open_help_url(url)
        assert result["ok"] is False
        assert result["error"] == "Link não permitido."
    assert opened == []

    assert api.open_help_url("https://vb-audio.com/Cable/") == {"ok": True}
    assert opened == ["https://vb-audio.com/Cable/"]


def test_open_help_url_never_raises(api, monkeypatch):
    def boom(url):
        raise RuntimeError("sem navegador")

    monkeypatch.setattr(main.webbrowser, "open", boom)

    result = api.open_help_url("https://ffmpeg.org/")

    assert result["ok"] is False
    assert "Não foi possível abrir o link" in result["error"]


def test_pick_directory_requires_known_kind_and_window(api):
    assert api.pick_directory("ffmpeg_path") == {"ok": False, "error": "Pasta desconhecida."}
    assert api.pick_directory("models_dir") == {"ok": False, "error": "Janela indisponível."}


def test_pick_directory_saves_the_chosen_folder(api, tmp_path, monkeypatch):
    chosen = tmp_path / "escolhida"
    chosen.mkdir()
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(FOLDER_DIALOG=object()))
    api._window = types.SimpleNamespace(create_file_dialog=lambda *a, **k: [str(chosen)])
    monkeypatch.setattr(api, "_notify_ui", lambda *a, **k: None)

    result = api.pick_directory("separados_root")

    assert result["ok"] is True
    assert api._config.separados_root == chosen.resolve()


# --- gating -------------------------------------------------------------------


def test_ai_endpoints_blocked_without_models(api, monkeypatch):
    monkeypatch.setattr(doctor, "capabilities", lambda checks: _caps(ai_analysis=False))

    results = (
        api.reanalyze_clip_ai("qualquer"),
        api.split_clip_by_speakers("qualquer"),
        api.enroll_speaker_from_clip("qualquer", "interlocutor_1"),
    )

    for result in results:
        assert result["ok"] is False
        assert "IA" in result["error"]
        assert result["error"] == (
            "Análise de IA indisponível: coloque os modelos em data/models ou abra ⚙️ Ambiente."
        )


def test_ai_gating_keeps_pending_rejection_after_it(api, monkeypatch):
    monkeypatch.setattr(doctor, "capabilities", lambda checks: _caps(ai_analysis=True))
    api._store.add_or_update_clip({"id": "draft", "title": "Draft", "pending": True})

    result = api.reanalyze_clip_ai("draft")

    assert result["ok"] is False
    assert "Rascunhos" in result["error"]


def test_mic_passthrough_is_disabled_once_without_cable(api, monkeypatch):
    calls = []
    toasts = []
    monkeypatch.setattr(api._audio, "set_mic_passthrough", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(doctor, "capabilities", lambda checks: _caps(cable_routing=False))
    monkeypatch.setattr(api, "_notify_ui", lambda name, payload=None: toasts.append((name, payload)))
    api._store.state["settings"]["mic_passthrough"] = True

    api._apply_mic_passthrough()
    api._apply_mic_passthrough()

    assert len(calls) == 2
    assert all(call["enabled"] is False for call in calls)
    warnings = [
        payload for name, payload in toasts if name == "toast" and payload.get("type") == "warning"
    ]
    assert len(warnings) == 1
    assert "VB-Cable" in warnings[0]["message"]


def test_mic_passthrough_stays_on_with_cable(api, monkeypatch):
    calls = []
    monkeypatch.setattr(api._audio, "set_mic_passthrough", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(doctor, "capabilities", lambda checks: _caps(cable_routing=True))
    monkeypatch.setattr(api, "_notify_ui", lambda *a, **k: None)
    api._store.state["settings"]["mic_passthrough"] = True

    api._apply_mic_passthrough()

    assert calls and calls[-1]["enabled"] is True


def test_import_requires_ffmpeg_for_convert_only_formats(api, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "capabilities", lambda checks: _caps(audio_convert=False))
    monkeypatch.setattr(api._ai, "analyze_file", lambda *a, **k: {
        "title": "Importado", "duration": 1.0, "category": "Call", "speaker_ids": [],
    })
    m4a = tmp_path / "voz.m4a"
    m4a.write_bytes(b"nao-e-audio")
    wav = tmp_path / "som.wav"
    wav.write_bytes(b"nao-e-audio")

    blocked = api.import_audio_files([str(m4a)])

    assert blocked["ok"] is False
    assert "FFmpeg" in blocked["error"]
    assert "voz.m4a" in blocked["error"]
    assert blocked["imported"] == []
    assert list(api._store.clips_dir.glob("*.m4a")) == []

    allowed = api.import_audio_files([str(wav)])

    assert allowed["ok"] is True
    assert len(allowed["imported"]) == 1
    assert "warnings" not in allowed

    mixed = api.import_audio_files([str(m4a), str(wav)])

    assert mixed["ok"] is True
    assert len(mixed["imported"]) == 1
    assert mixed["warnings"]
    assert "voz.m4a" in mixed["warnings"][0]


# --- doctor cache -------------------------------------------------------------


def test_environment_status_is_cached_and_refresh_reruns_probes(api, monkeypatch):
    calls = {"n": 0}

    def fake_ffmpeg(cfg):
        calls["n"] += 1
        return _check("ffmpeg", "FFmpeg")

    monkeypatch.setattr(doctor, "_check_ffmpeg", fake_ffmpeg)
    doctor.invalidate_cache()

    first = api.get_environment_status()
    second = api.get_environment_status()

    assert calls["n"] == 1
    assert first["capabilities"]["audio_convert"] is True
    assert second["checks"] == first["checks"]

    third = api.refresh_environment()

    assert calls["n"] == 2
    assert third["capabilities"]["audio_convert"] is True


def test_invalidate_and_save_force_fresh_probes(api, tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_ffmpeg(cfg):
        calls["n"] += 1
        return _check("ffmpeg", "FFmpeg")

    monkeypatch.setattr(doctor, "_check_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(api, "_notify_ui", lambda *a, **k: None)
    doctor.invalidate_cache()

    api.get_environment_status()
    assert calls["n"] == 1

    doctor.invalidate_cache()
    api.get_environment_status()
    assert calls["n"] == 2

    api.get_environment_status()
    assert calls["n"] == 2

    result = api.save_app_config({"separados_root": str(tmp_path / "videos")})

    assert result["ok"] is True
    assert calls["n"] == 3
