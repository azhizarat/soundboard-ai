"""Pacote A doctor: probes, cache, capabilities and the help-URL whitelist."""

from __future__ import annotations

import contextlib
import sys
import time
import types
import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest

import config as app_config
import doctor


@pytest.fixture(autouse=True)
def _fresh_cache():
    doctor.invalidate_cache()
    yield
    doctor.invalidate_cache()


@pytest.fixture
def cfg(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    return app_config.AppConfig(
        data_dir=data_dir.resolve(),
        models_dir=models_dir.resolve(),
        separados_root=(tmp_path / "Videos").resolve(),
    )


def _module(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _explode(*args, **kwargs):
    raise RuntimeError("boom")


def _install_winreg(monkeypatch, present_roots=(), version="120.0.2210.91", open_key=None):
    if open_key is None:
        def open_key(root, sub):
            if root in present_roots:
                assert sub == doctor.WEBVIEW2_REG_PATH
                return contextlib.nullcontext(object())
            raise FileNotFoundError(2, "chave ausente")
    monkeypatch.setitem(
        sys.modules,
        "winreg",
        _module(
            "winreg",
            HKEY_CURRENT_USER="HKCU",
            HKEY_LOCAL_MACHINE="HKLM",
            OpenKey=open_key,
            QueryValueEx=lambda key, name: (version, 1),
        ),
    )
    monkeypatch.setattr(doctor.os, "name", "nt")


def _stub_probes(monkeypatch, status="ok"):
    """Replaces every probe with a counter so tests never touch the real world."""
    counts: dict[str, int] = {}
    for check_id, label in doctor.CHECK_ORDER:
        def probe(config, check_id=check_id, label=label):
            counts[check_id] = counts.get(check_id, 0) + 1
            return doctor.CheckResult(check_id, label, status, "stub")

        monkeypatch.setattr(doctor, f"_check_{check_id}", probe)
    return counts


# Captured before any test can replace them: the "never raises" test needs the
# real probe under an exploding dependency, with every other probe stubbed.
_REAL_PROBES = {check_id: getattr(doctor, f"_check_{check_id}") for check_id, _ in doctor.CHECK_ORDER}


# --- ffmpeg -------------------------------------------------------------------


def test_ffmpeg_ok(monkeypatch, cfg):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    check = doctor._check_ffmpeg(cfg)

    assert check.status == "ok"
    assert check.ok is True
    assert calls[0][0] == [cfg.ffmpeg_path, "-version"]
    assert calls[0][1]["timeout"] == doctor.PROBE_TIMEOUT
    assert calls[0][1]["capture_output"] is True


@pytest.mark.parametrize("error", [FileNotFoundError("ffmpeg"), OSError("sem permissão")])
def test_ffmpeg_missing_or_unusable(monkeypatch, cfg, error):
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(error))

    check = doctor._check_ffmpeg(cfg)

    assert check.status == "error"
    assert check.fix_url == doctor.FFMPEG_FIX
    assert check.action == "open_url"


def test_ffmpeg_timeout_is_an_error(monkeypatch, cfg):
    def fake_run(argv, **kwargs):
        raise doctor.subprocess.TimeoutExpired(argv, doctor.PROBE_TIMEOUT)

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    assert doctor._check_ffmpeg(cfg).status == "error"


# --- vbcable ------------------------------------------------------------------


def test_vbcable_present(monkeypatch, cfg):
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        _module("sounddevice", query_devices=lambda: [{"name": "Alto-falantes"}, {"name": "CABLE Input (VB-Audio)"}]),
    )

    check = doctor._check_vbcable(cfg)

    assert check.status == "ok"
    assert "CABLE Input" in check.detail


def test_vbcable_absent(monkeypatch, cfg):
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        _module("sounddevice", query_devices=lambda: [{"name": "Microfone"}]),
    )

    check = doctor._check_vbcable(cfg)

    assert check.status == "error"
    assert check.fix_url == doctor.VBCABLE_FIX


# --- models -------------------------------------------------------------------


def _touch_models(cfg, seg=True, emb=True, ref=False):
    if seg:
        path = cfg.models_dir / "pyannote-segmentation-3-0" / "model.onnx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"0")
    if emb:
        (cfg.models_dir / "nemo_en_titanet_large.onnx").write_bytes(b"0")
    if ref:
        (cfg.models_dir / "referencias_vozes.npz").write_bytes(b"0")


def test_models_folder_missing(cfg, tmp_path):
    missing = replace(cfg, models_dir=(tmp_path / "nada").resolve())

    check = doctor._check_models(missing)

    assert check.status == "error"
    assert check.action == "pick_models_dir"
    assert check.fix_label == "Escolher pasta"


def test_models_one_missing_is_warn(cfg):
    _touch_models(cfg, emb=False)

    check = doctor._check_models(cfg)

    assert check.status == "warn"
    assert "nemo_en_titanet_large.onnx" in check.detail
    assert check.action == "pick_models_dir"


def test_models_both_missing_is_error(cfg):
    check = doctor._check_models(cfg)

    assert check.status == "error"
    assert "pyannote-segmentation-3-0" in check.detail
    assert "nemo_en_titanet_large.onnx" in check.detail
    assert check.action == "pick_models_dir"


def test_models_both_present_is_ok(cfg):
    _touch_models(cfg)

    check = doctor._check_models(cfg)

    assert check.status == "ok"
    assert "referencias_vozes.npz" in check.detail


def test_models_missing_references_never_downgrades(cfg):
    _touch_models(cfg, seg=True, emb=True, ref=False)

    check = doctor._check_models(cfg)

    assert check.status == "ok"


# --- ollama -------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status=200, body=b"{}"):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def test_ollama_online(monkeypatch, cfg):
    seen = {}

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        seen["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(doctor.urllib.request, "urlopen", fake_urlopen)

    check = doctor._check_ollama(cfg)

    assert check.status == "ok"
    assert seen["url"] == f"{cfg.ollama_url}/api/tags"
    assert seen["timeout"] == doctor.PROBE_TIMEOUT


def test_ollama_offline_is_warn(monkeypatch, cfg):
    def fake_urlopen(url, timeout=None):
        raise urllib.error.URLError("recusado")

    monkeypatch.setattr(doctor.urllib.request, "urlopen", fake_urlopen)

    check = doctor._check_ollama(cfg)

    assert check.status == "warn"
    assert "heurística local" in check.detail
    assert check.fix_url == doctor.OLLAMA_FIX


def test_ollama_non_200_is_warn(monkeypatch, cfg):
    monkeypatch.setattr(doctor.urllib.request, "urlopen", lambda url, timeout=None: _FakeResponse(status=503))

    assert doctor._check_ollama(cfg).status == "warn"


# --- data_writable ------------------------------------------------------------


def test_data_writable_ok(cfg):
    check = doctor._check_data_writable(cfg)

    assert check.status == "ok"
    assert not (cfg.data_dir / ".doctor_probe").exists()


def test_data_writable_denied_removes_probe(monkeypatch, cfg):
    def fake_write(self, *args, **kwargs):
        raise PermissionError("acesso negado")

    monkeypatch.setattr(Path, "write_text", fake_write)
    (cfg.data_dir / ".doctor_probe").write_bytes(b"residuo")

    check = doctor._check_data_writable(cfg)

    assert check.status == "error"
    assert not (cfg.data_dir / ".doctor_probe").exists()


# --- webview2 -----------------------------------------------------------------


def test_webview2_non_windows_is_ok(monkeypatch, cfg):
    monkeypatch.setattr(doctor.os, "name", "posix")

    check = doctor._check_webview2(cfg)

    assert check.status == "ok"


def test_webview2_present(monkeypatch, cfg):
    _install_winreg(monkeypatch, present_roots=("HKLM",))

    check = doctor._check_webview2(cfg)

    assert check.status == "ok"
    assert "120.0.2210.91" in check.detail


def test_webview2_present_in_wow64_registry(monkeypatch, cfg):
    def open_key(root, sub, *args):
        if "WOW6432Node" in sub:
            return contextlib.nullcontext(object())
        raise FileNotFoundError(2, "chave ausente")

    _install_winreg(monkeypatch, open_key=open_key, version="156.0.4314.50")
    check = doctor._check_webview2(cfg)
    assert check.status == "ok"
    assert "156.0.4314.50" in check.detail


def test_webview2_missing(monkeypatch, cfg):
    _install_winreg(monkeypatch, present_roots=())

    check = doctor._check_webview2(cfg)

    assert check.status == "error"
    assert check.fix_url == doctor.WEBVIEW2_FIX


def test_webview2_registry_import_failure(monkeypatch, cfg):
    monkeypatch.setattr(doctor.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "winreg", None)

    check = doctor._check_webview2(cfg)

    assert check.status == "error"
    assert check.fix_url == doctor.WEBVIEW2_FIX


# --- run_checks / cache -------------------------------------------------------


def test_run_checks_has_six_checks_in_order(monkeypatch, cfg):
    _stub_probes(monkeypatch)

    checks = doctor.run_checks(cfg)

    assert [check.id for check in checks] == [
        "ffmpeg",
        "vbcable",
        "models",
        "ollama",
        "data_writable",
        "webview2",
    ]


def test_cache_runs_probes_once_and_refresh_bypasses(monkeypatch, cfg):
    counts = _stub_probes(monkeypatch)

    doctor.run_checks(cfg)
    doctor.run_checks(cfg)
    assert counts == {check_id: 1 for check_id, _ in doctor.CHECK_ORDER}

    doctor.run_checks(cfg, refresh=True)
    assert counts["ffmpeg"] == 2

    doctor.invalidate_cache()
    doctor.run_checks(cfg)
    assert counts["ffmpeg"] == 3


def test_cache_expires_after_ttl(monkeypatch, cfg):
    counts = _stub_probes(monkeypatch)
    doctor.run_checks(cfg)
    doctor._CACHE["at"] = time.time() - (doctor.CACHE_TTL + 1.0)

    doctor.run_checks(cfg)

    assert counts["ffmpeg"] == 2


def test_cache_key_tracks_models_dir(monkeypatch, cfg, tmp_path):
    counts = _stub_probes(monkeypatch)
    doctor.run_checks(cfg)

    doctor.run_checks(replace(cfg, models_dir=(tmp_path / "outra").resolve()))

    assert counts["ffmpeg"] == 2


def test_cache_lock_is_not_held_while_probes_run(monkeypatch, cfg):
    _stub_probes(monkeypatch)
    acquired = {}

    def probe(config):
        # A pywebview thread must be able to use the cache during a probe run.
        acquired["lock"] = doctor._CACHE_LOCK.acquire(timeout=1.0)
        if acquired["lock"]:
            doctor._CACHE_LOCK.release()
        return doctor.CheckResult("ffmpeg", "FFmpeg", "ok", "stub")

    monkeypatch.setattr(doctor, "_check_ffmpeg", probe)

    doctor.run_checks(cfg, refresh=True)

    assert acquired["lock"] is True


@pytest.mark.parametrize(
    "check_id, breaker",
    [
        ("ffmpeg", lambda monkeypatch: monkeypatch.setattr(doctor.subprocess, "run", _explode)),
        ("vbcable", lambda monkeypatch: monkeypatch.setitem(sys.modules, "sounddevice", _module("sounddevice", query_devices=_explode))),
        ("models", lambda monkeypatch: monkeypatch.setattr(Path, "is_dir", _explode)),
        ("ollama", lambda monkeypatch: monkeypatch.setattr(doctor.urllib.request, "urlopen", _explode)),
        ("data_writable", lambda monkeypatch: monkeypatch.setattr(Path, "write_text", _explode)),
        ("webview2", lambda monkeypatch: _install_winreg(monkeypatch, open_key=_explode)),
    ],
)
def test_probes_never_raise(monkeypatch, cfg, check_id, breaker):
    _stub_probes(monkeypatch)
    monkeypatch.setattr(doctor, f"_check_{check_id}", _REAL_PROBES[check_id])
    breaker(monkeypatch)

    checks = {check.id: check for check in doctor.run_checks(cfg, refresh=True)}

    assert checks[check_id].status == "error"
    assert "boom" in checks[check_id].detail


# --- capabilities / snapshot --------------------------------------------------


def _checks(**statuses):
    return [
        doctor.CheckResult(check_id, label, statuses.get(check_id, "ok"))
        for check_id, label in doctor.CHECK_ORDER
    ]


def test_capabilities_follow_statuses():
    caps = doctor.capabilities(_checks(models="warn", ffmpeg="error", vbcable="ok", ollama="warn"))

    assert caps == {
        "ai_analysis": True,
        "audio_convert": False,
        "cable_routing": True,
        "local_llm": False,
    }


def test_capabilities_without_checks_are_false():
    assert doctor.capabilities([]) == {
        "ai_analysis": False,
        "audio_convert": False,
        "cable_routing": False,
        "local_llm": False,
    }


def test_snapshot_shape(monkeypatch, cfg):
    _stub_probes(monkeypatch)
    monkeypatch.setattr(
        doctor,
        "_check_ollama",
        lambda config: doctor.CheckResult("ollama", "Ollama", "warn", "offline"),
    )

    snap = doctor.snapshot(cfg, refresh=True)

    assert set(snap) == {"checks", "capabilities", "incomplete", "checked_at"}
    assert len(snap["checks"]) == 6
    assert all(isinstance(check, dict) for check in snap["checks"])
    assert snap["checks"][3]["id"] == "ollama"
    assert snap["incomplete"] == 1
    assert snap["capabilities"]["local_llm"] is False
    assert isinstance(snap["checked_at"], float)


# --- safe_help_url ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://vb-audio.com/Cable/",
        "https://www.gyan.dev/ffmpeg/builds/",
        "https://ollama.com/download",
        "https://learn.microsoft.com/microsoft-edge/webview2/",
        "https://VB-AUDIO.com/Cable/",
    ],
)
def test_safe_help_url_accepts_whitelist(url):
    assert doctor.safe_help_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://vb-audio.com/Cable/",
        "https://evil.com/Cable/",
        "https://vb-audio.com.evil.com/Cable/",
        "https://vb-audio.com@evil.com/Cable/",
        "javascript:alert(1)",
        "ftp://ffmpeg.org/",
        "",
        None,
        "https:///sem-host",
    ],
)
def test_safe_help_url_rejects_everything_else(url):
    assert doctor.safe_help_url(url) is None
