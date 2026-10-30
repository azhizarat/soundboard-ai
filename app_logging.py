"""Application logging configuration: rotating file logs and uncaught error capture."""

from __future__ import annotations

import logging
import json
import math
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading

MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s"


def log_analysis_metric(code, **metrics):
    """Allowlist explícita: transcrição, prompt, nome e caminho nunca são logados."""
    allowed = {'duration_ms', 'cache_hits', 'cache_misses', 'cache_bytes', 'queue_depth'}
    clean = {k:v for k,v in metrics.items() if k in allowed and isinstance(v,(int,float)) and math.isfinite(v)}
    clean['code'] = code if code in ('ready','no_speech','failed','cancelled') else 'analysis_metric'
    logging.getLogger('soundboard.analysis').info(json.dumps(clean,sort_keys=True))


def setup_logging(data_dir: Path, debug: bool = False) -> Path:
    """Configures application-wide logging and installs unhandled exception hooks."""
    logs_dir = Path(data_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "soundboard.log"

    root_logger = logging.getLogger()
    level = logging.DEBUG if debug else logging.INFO
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates on re-setup
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT)

    # Rotating file handler
    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root_logger.addHandler(file_handler)

    # Console handler only when debug is active
    if debug:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(console_handler)

    # Suppress verbose third-party debug spam in console and log files
    for noisy in ("PIL", "urllib3", "pystray", "comtypes"):
        logging.getLogger(noisy).setLevel(logging.INFO)
    for personal in ('faster_whisper', 'sherpa_onnx'):
        logging.getLogger(personal).setLevel(logging.WARNING)

    _install_exception_hooks(root_logger)
    return log_file


def _install_exception_hooks(logger: logging.Logger) -> None:
    def handle_sys_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Exceção não tratada capturada em sys.excepthook:", exc_info=(exc_type, exc_value, exc_traceback))

    def handle_thread_exception(args):
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        logger.critical(
            f"Exceção não tratada capturada na thread {args.thread.name}:",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = handle_sys_exception
    if hasattr(threading, "excepthook"):
        threading.excepthook = handle_thread_exception
