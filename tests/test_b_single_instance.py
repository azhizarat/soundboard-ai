"""Pacote B: SingleInstance tests (mutex on Windows and fallback)."""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

from single_instance import SingleInstance


def test_single_instance_acquire_and_release(tmp_path):
    inst = SingleInstance(name=r"Local\SoundboardTestMutex1", data_dir=tmp_path)
    assert inst.acquire() is True
    assert inst._acquired is True

    # Re-acquiring same instance returns True
    assert inst.acquire() is True

    inst.release()
    assert inst._acquired is False


def test_single_instance_second_fails_while_first_held(tmp_path):
    inst1 = SingleInstance(name=r"Local\SoundboardTestMutex2", data_dir=tmp_path)
    inst2 = SingleInstance(name=r"Local\SoundboardTestMutex2", data_dir=tmp_path)

    try:
        assert inst1.acquire() is True
        assert inst2.acquire() is False
    finally:
        inst1.release()
        inst2.release()


def test_single_instance_second_succeeds_after_release(tmp_path):
    inst1 = SingleInstance(name=r"Local\SoundboardTestMutex3", data_dir=tmp_path)
    inst2 = SingleInstance(name=r"Local\SoundboardTestMutex3", data_dir=tmp_path)

    try:
        assert inst1.acquire() is True
        inst1.release()
        assert inst2.acquire() is True
    finally:
        inst1.release()
        inst2.release()


def test_single_instance_non_windows_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    inst1 = SingleInstance(data_dir=tmp_path)
    assert inst1.acquire() is True
    assert (tmp_path / ".soundboard.lock").exists()

    inst1.release()
    assert inst1._acquired is False


def test_notify_already_running_does_not_raise(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    inst = SingleInstance()
    inst.notify_already_running()
