"""Windows autostart integration via HKCU Run registry key."""

from __future__ import annotations

import os
from pathlib import Path
import sys

REG_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VALUE_NAME = "SoundboardAI"


def get_autostart_command(app_root: Path | None = None) -> str:
    """Returns the command line used to launch Soundboard silently on boot."""
    root = (app_root or Path(__file__).parent).resolve()
    vbs_path = root / "Soundboard AI.vbs"
    # Execute wscript.exe "<path_to_vbs>"
    return f'wscript.exe "{vbs_path}"'


def is_autostart_enabled() -> bool:
    """Checks whether the application is registered in Windows autostart."""
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, REG_VALUE_NAME)
            return bool(val)
    except OSError:
        return False


def set_autostart(enabled: bool, app_root: Path | None = None) -> bool:
    """Enables or disables Windows autostart for Soundboard AI."""
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                cmd = get_autostart_command(app_root)
                winreg.SetValueEx(key, REG_VALUE_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, REG_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
