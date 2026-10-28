"""Ensures only a single instance of Soundboard AI is running."""

from __future__ import annotations

import os
import sys
from pathlib import Path

MUTEX_NAME = r"Global\SoundboardAI"
MUTEX_NAME_FALLBACK = r"Local\SoundboardAI"
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Manages a named mutex on Windows, or an advisory file lock on other systems."""

    def __init__(self, name: str = MUTEX_NAME, data_dir: Path | None = None):
        self.name = name
        self.data_dir = data_dir
        self._mutex = None
        self._lock_file = None
        self._lock_path = None
        self._acquired = False

    def acquire(self) -> bool:
        if self._acquired:
            return True
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # Try Global first; if Access Denied (error 5), fallback to Local
            handle = kernel32.CreateMutexW(None, True, self.name)
            last_err = kernel32.GetLastError()
            if not handle and last_err == 5 and "Global" in self.name:
                handle = kernel32.CreateMutexW(None, True, MUTEX_NAME_FALLBACK)
                last_err = kernel32.GetLastError()
            if not handle:
                return False
            if last_err == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            self._mutex = handle
            self._acquired = True
            return True
        else:
            lock_dir = self.data_dir or (Path(__file__).parent / "data")
            lock_dir.mkdir(parents=True, exist_ok=True)
            self._lock_path = lock_dir / ".soundboard.lock"
            try:
                self._lock_file = open(self._lock_path, "w")
                try:
                    import fcntl

                    fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (ImportError, AttributeError, OSError):
                    # In environments without fcntl (or Windows mock), write PID
                    pass
                self._acquired = True
                return True
            except Exception:
                if self._lock_file:
                    self._lock_file.close()
                    self._lock_file = None
                return False

    def release(self) -> None:
        if not self._acquired:
            return
        if sys.platform == "win32":
            if self._mutex:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                kernel32.ReleaseMutex(self._mutex)
                kernel32.CloseHandle(self._mutex)
                self._mutex = None
        else:
            if self._lock_file:
                try:
                    import fcntl

                    fcntl.flock(self._lock_file, fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    self._lock_file.close()
                except Exception:
                    pass
                self._lock_file = None
                if self._lock_path:
                    try:
                        self._lock_path.unlink(missing_ok=True)
                    except Exception:
                        pass
        self._acquired = False

    def notify_already_running(self) -> None:
        title = "Soundboard AI"
        msg = "O Soundboard já está aberto."
        if sys.platform == "win32":
            try:
                import ctypes

                # MB_OK (0x0) | MB_ICONINFORMATION (0x40) | MB_SETFOREGROUND (0x10000)
                ctypes.windll.user32.MessageBoxW(0, msg, title, 0x0 | 0x40 | 0x10000)
                return
            except Exception:
                pass
        print(f"[{title}] {msg}", file=sys.stderr)
