"""Pacote A runtime wiring: ffmpeg path and Ollama endpoint are configurable."""

from __future__ import annotations

import contextlib
import json
import types
from pathlib import Path

import numpy as np
import pytest
import soundfile

import ai_analyzer
import audio_engine


@pytest.fixture(autouse=True)
def _reset_runtime():
    runtime = dict(ai_analyzer._RUNTIME)
    ai_analyzer._OLLAMA_CACHE.update({"checked_at": 0.0, "available": False})
    yield
    ai_analyzer._RUNTIME.clear()
    ai_analyzer._RUNTIME.update(runtime)
    ai_analyzer._OLLAMA_CACHE.update({"checked_at": 0.0, "available": False})


# --- configure_runtime --------------------------------------------------------


def test_configure_runtime_only_updates_provided_keys():
    ai_analyzer.configure_runtime(ollama_url="http://127.0.0.1:9999///")

    assert ai_analyzer._RUNTIME["ollama_url"] == "http://127.0.0.1:9999"
    assert ai_analyzer._RUNTIME["ffmpeg"] == "ffmpeg"
    assert ai_analyzer._RUNTIME["ollama_model"] == "qwen2.5:1.5b"

    ai_analyzer.configure_runtime(ffmpeg_path="C:/tools/ffmpeg.exe", ollama_model="llama3")

    assert ai_analyzer._RUNTIME["ffmpeg"] == "C:/tools/ffmpeg.exe"
    assert ai_analyzer._RUNTIME["ollama_model"] == "llama3"
    assert ai_analyzer._RUNTIME["ollama_url"] == "http://127.0.0.1:9999"


def test_configure_runtime_ignores_empty_values():
    before = dict(ai_analyzer._RUNTIME)

    ai_analyzer.configure_runtime(ffmpeg_path=None, ollama_url="", ollama_model=None)

    assert ai_analyzer._RUNTIME == before


# --- load_audio_mono_16k ------------------------------------------------------


def test_load_audio_fallback_uses_configured_ffmpeg(monkeypatch, tmp_path):
    ai_analyzer.configure_runtime(ffmpeg_path="C:/tools/ffmpeg.exe")
    calls = {"reads": 0, "run": []}

    def fake_read(path, dtype=None):
        calls["reads"] += 1
        if calls["reads"] == 1:
            raise RuntimeError("formato não suportado")
        return np.zeros(16000, dtype=np.float32), 16000

    def fake_run(argv, **kwargs):
        calls["run"].append((argv, kwargs))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(soundfile, "read", fake_read)
    monkeypatch.setattr(ai_analyzer.subprocess, "run", fake_run)

    data, duration = ai_analyzer.load_audio_mono_16k(str(tmp_path / "clip.m4a"))

    assert calls["run"], "ffmpeg fallback was not used"
    assert calls["run"][0][0][0] == "C:/tools/ffmpeg.exe"
    assert calls["run"][0][1]["check"] is True
    assert data.shape == (16000,)
    assert duration == pytest.approx(1.0)


# --- _query_local_ollama ------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def test_query_ollama_uses_configured_host_port_and_model(monkeypatch):
    ai_analyzer.configure_runtime(ollama_url="http://127.0.0.1:9999", ollama_model="meu-modelo")
    seen = {}

    def fake_create_connection(address, timeout=None):
        seen["address"] = address
        seen["connect_timeout"] = timeout
        return contextlib.nullcontext(object())

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["payload"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        body = json.dumps({"response": json.dumps({"title": "Oi", "category": "Call"})})
        return _FakeResponse(body.encode("utf-8"))

    monkeypatch.setattr(ai_analyzer.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(ai_analyzer.urllib.request, "urlopen", fake_urlopen)

    result = ai_analyzer._query_local_ollama("prompt de teste")

    assert seen["address"] == ("127.0.0.1", 9999)
    assert seen["connect_timeout"] == 0.12
    assert seen["url"] == "http://127.0.0.1:9999/api/generate"
    assert seen["payload"]["model"] == "meu-modelo"
    assert seen["payload"]["prompt"] == "prompt de teste"
    assert result == {"title": "Oi", "category": "Call"}


def test_query_ollama_offline_skips_the_request(monkeypatch):
    ai_analyzer.configure_runtime(ollama_url="http://127.0.0.1:9999")
    requested = []

    def fake_create_connection(address, timeout=None):
        raise ConnectionRefusedError("recusado")

    monkeypatch.setattr(ai_analyzer.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(ai_analyzer.urllib.request, "urlopen", lambda *a, **k: requested.append(a))

    assert ai_analyzer._query_local_ollama("prompt") is None
    assert requested == []


def test_query_ollama_falls_back_to_default_host_on_bad_url(monkeypatch):
    ai_analyzer.configure_runtime(ollama_url="http://")
    seen = {}

    def fake_create_connection(address, timeout=None):
        seen["address"] = address
        return contextlib.nullcontext(object())

    monkeypatch.setattr(ai_analyzer.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(ai_analyzer.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b"{}"))

    assert ai_analyzer._query_local_ollama("prompt") is None
    assert seen["address"] == ("127.0.0.1", 11434)


# --- AIAnalyzer ---------------------------------------------------------------


def test_ai_analyzer_uses_explicit_models_dir(tmp_path):
    models = tmp_path / "models"
    models.mkdir()

    analyzer = ai_analyzer.AIAnalyzer(models_dir=str(models))

    assert analyzer.models_dir == models.resolve()
    assert analyzer.seg_model_path == models / "pyannote-segmentation-3-0" / "model.onnx"
    assert analyzer.emb_model_path == models / "nemo_en_titanet_large.onnx"
    assert analyzer.ref_voices_path == models / "referencias_vozes.npz"


def test_ai_analyzer_adopts_missing_explicit_models_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "nao-existe"

    analyzer = ai_analyzer.AIAnalyzer(models_dir=str(missing))

    assert analyzer.models_dir == missing.resolve()
    assert not missing.exists(), "an explicit models_dir must not be created"
    assert analyzer.models_dir != (tmp_path / "data" / "models").resolve()


def test_ai_analyzer_creates_data_models_when_folder_is_omitted(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    analyzer = ai_analyzer.AIAnalyzer()

    assert analyzer.models_dir == (tmp_path / "data" / "models").resolve()
    assert analyzer.models_dir.is_dir()


def test_ai_analyzer_applies_runtime_config(tmp_path):
    models = tmp_path / "models"
    models.mkdir()

    ai_analyzer.AIAnalyzer(
        models_dir=str(models),
        ffmpeg_path="C:/tools/ffmpeg.exe",
        ollama_url="http://127.0.0.1:9999/",
        ollama_model="llama3",
    )

    assert ai_analyzer._RUNTIME == {
        "ffmpeg": "C:/tools/ffmpeg.exe",
        "ollama_url": "http://127.0.0.1:9999",
        "ollama_model": "llama3",
    }


# --- AudioEngine --------------------------------------------------------------


def test_audio_engine_exposes_ffmpeg_path():
    engine = audio_engine.AudioEngine(start_loopback=False, ffmpeg_path="C:/tools/ffmpeg.exe")

    assert engine.ffmpeg_path == "C:/tools/ffmpeg.exe"


def test_audio_engine_defaults_to_ffmpeg():
    engine = audio_engine.AudioEngine(start_loopback=False)

    assert engine.ffmpeg_path == "ffmpeg"


def test_audio_engine_decode_fallback_uses_ffmpeg_path(monkeypatch, tmp_path):
    engine = audio_engine.AudioEngine(start_loopback=False, ffmpeg_path="C:/tools/ffmpeg.exe")
    source = tmp_path / "clip.m4a"
    source.write_bytes(b"nao-e-audio")
    calls = {"reads": 0, "run": []}

    def fake_read(path, dtype=None, always_2d=False):
        calls["reads"] += 1
        if calls["reads"] == 1:
            raise RuntimeError("formato não suportado")
        return np.zeros((4800, 2), dtype=np.float32), engine.TARGET_SR

    def fake_run(argv, **kwargs):
        calls["run"].append((argv, kwargs))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(soundfile, "read", fake_read)
    monkeypatch.setattr(audio_engine.subprocess, "run", fake_run)

    data, duration = engine.load_clip_stereo_48k(str(source))

    assert calls["run"], "ffmpeg fallback was not used"
    assert calls["run"][0][0][0] == "C:/tools/ffmpeg.exe"
    assert data.shape == (4800, 2)
    assert duration == pytest.approx(0.1, abs=1e-3)
