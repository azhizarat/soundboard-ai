"""Pacote B: Logging configuration and unhandled exception hook tests."""

from __future__ import annotations

import logging
from pathlib import Path
import sys
import threading
import pytest

from app_logging import setup_logging


def test_setup_logging_creates_file_and_logs(tmp_path):
    log_file = setup_logging(tmp_path, debug=False)
    assert log_file.is_file()
    assert log_file.parent.name == "logs"

    logger = logging.getLogger("test_logger")
    logger.info("Teste de mensagem informativa")

    content = log_file.read_text(encoding="utf-8")
    assert "Teste de mensagem informativa" in content
    assert "[INFO]" in content


def test_setup_logging_debug_adds_console_handler(tmp_path):
    setup_logging(tmp_path, debug=True)
    root = logging.getLogger()
    assert any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers)


def test_setup_logging_no_debug_omits_console_handler(tmp_path):
    setup_logging(tmp_path, debug=False)
    root = logging.getLogger()
    stream_handlers = [h for h in root.handlers if type(h) is logging.StreamHandler]
    assert len(stream_handlers) == 0


def test_sys_excepthook_logs_critical(tmp_path):
    log_file = setup_logging(tmp_path, debug=False)
    try:
        raise ValueError("Erro não tratado de teste")
    except ValueError as exc:
        sys.excepthook(type(exc), exc, exc.__traceback__)

    content = log_file.read_text(encoding="utf-8")
    assert "Exceção não tratada capturada em sys.excepthook" in content
    assert "Erro não tratado de teste" in content


def test_thread_excepthook_logs_critical(tmp_path):
    log_file = setup_logging(tmp_path, debug=False)
    err = RuntimeError("Erro de thread de teste")
    args = type("Args", (), {
        "exc_type": type(err),
        "exc_value": err,
        "exc_traceback": None,
        "thread": threading.current_thread(),
    })
    if hasattr(threading, "excepthook"):
        threading.excepthook(args)
        content = log_file.read_text(encoding="utf-8")
        assert "Exceção não tratada capturada na thread" in content
        assert "Erro de thread de teste" in content
