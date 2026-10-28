"""Pacote B: CLI argument parsing tests."""

from __future__ import annotations

import main


def test_parse_args_defaults():
    opts = main.parse_args([])
    assert opts.minimized is False
    assert opts.debug is False
    assert opts.no_tray is False
    assert opts.data_dir is None


def test_parse_args_custom_flags():
    opts = main.parse_args(["--minimized", "--debug", "--no-tray", "--data-dir", "test_data"])
    assert opts.minimized is True
    assert opts.debug is True
    assert opts.no_tray is True
    assert opts.data_dir == "test_data"
