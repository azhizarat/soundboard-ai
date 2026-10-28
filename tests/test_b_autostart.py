"""Pacote B: Autostart registry integration tests."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import sys
import types
import pytest

import autostart


def test_get_autostart_command(tmp_path):
    cmd = autostart.get_autostart_command(tmp_path)
    assert "wscript.exe" in cmd
    assert "Soundboard AI.vbs" in cmd


def test_autostart_non_windows_returns_false(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert autostart.is_autostart_enabled() is False
    assert autostart.set_autostart(True) is False


def test_autostart_enabled_and_disabled_with_mocked_winreg(monkeypatch, tmp_path):
    store = {}

    class MockKey:
        pass

    def open_key(root, sub, reserved, access):
        assert sub == autostart.REG_KEY_PATH
        return contextlib.nullcontext(MockKey())

    def query_value_ex(key, name):
        if name in store:
            return store[name], 1
        raise FileNotFoundError(2, "No such value")

    def set_value_ex(key, name, reserved, reg_type, value):
        store[name] = value

    def delete_value(key, name):
        store.pop(name, None)

    fake_winreg = types.SimpleNamespace(
        HKEY_CURRENT_USER="HKCU",
        KEY_READ=1,
        KEY_SET_VALUE=2,
        REG_SZ=1,
        OpenKey=open_key,
        QueryValueEx=query_value_ex,
        SetValueEx=set_value_ex,
        DeleteValue=delete_value,
    )

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    assert autostart.is_autostart_enabled() is False

    res = autostart.set_autostart(True, app_root=tmp_path)
    assert res is True
    assert autostart.is_autostart_enabled() is True
    assert autostart.REG_VALUE_NAME in store
    assert "Soundboard AI.vbs" in store[autostart.REG_VALUE_NAME]

    res = autostart.set_autostart(False, app_root=tmp_path)
    assert res is True
    assert autostart.is_autostart_enabled() is False
    assert autostart.REG_VALUE_NAME not in store
