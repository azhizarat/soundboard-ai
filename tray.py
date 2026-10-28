"""System tray icon and context menu management using pystray."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import threading
from typing import Callable

from PIL import Image, ImageDraw
import pystray

import autostart

logger = logging.getLogger(__name__)


def ensure_icon_image(icon_path: Path) -> Image.Image:
    """Loads ui/icon.png or creates a clean 64x64 fallback icon if missing."""
    icon_path = Path(icon_path)
    if icon_path.is_file():
        try:
            return Image.open(str(icon_path))
        except Exception as exc:
            logger.warning(f"Não foi possível abrir o ícone {icon_path}: {exc}")

    # Generate a sleek 64x64 soundboard logo
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Circle background with indigo gradient vibe
    draw.ellipse((4, 4, 60, 60), fill=(99, 102, 241, 255))
    # Soundboard faders / equalizer bars
    bars = [
        (16, 36, 22, 48),
        (26, 20, 32, 48),
        (36, 14, 42, 48),
        (46, 28, 52, 48),
    ]
    for bar in bars:
        draw.rectangle(bar, fill=(255, 255, 255, 255))

    icon_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        img.save(str(icon_path), "PNG")
    except Exception as exc:
        logger.warning(f"Não foi possível salvar {icon_path}: {exc}")

    return img


class SoundboardTray:
    """Manages the system tray icon, notifications and background thread."""

    def __init__(
        self,
        on_open: Callable[[], None],
        on_exit: Callable[[], None],
        on_open_data_dir: Callable[[], None],
        app_root: Path | None = None,
        icon_path: Path | None = None,
    ):
        self.on_open = on_open
        self.on_exit = on_exit
        self.on_open_data_dir = on_open_data_dir
        self.app_root = app_root or Path(__file__).parent.resolve()
        self.icon_path = icon_path or (self.app_root / "ui" / "icon.png")
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def _build_menu(self) -> pystray.Menu:
        def toggle_autostart(icon, item):
            current = autostart.is_autostart_enabled()
            autostart.set_autostart(not current, self.app_root)

        return pystray.Menu(
            pystray.MenuItem("Abrir Soundboard", lambda icon, item: self.on_open(), default=True),
            pystray.MenuItem(
                "Iniciar com o Windows",
                toggle_autostart,
                checked=lambda item: autostart.is_autostart_enabled(),
            ),
            pystray.MenuItem(
                "Abrir pasta de dados", lambda icon, item: self.on_open_data_dir()
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda icon, item: self.on_exit()),
        )

    def run_detached(self) -> None:
        """Starts the tray event loop in a daemon thread."""
        img = ensure_icon_image(self.icon_path)
        menu = self._build_menu()
        self._icon = pystray.Icon(
            name="SoundboardAI",
            icon=img,
            title="Soundboard AI",
            menu=menu,
        )

        def _runner():
            try:
                self._icon.run()
            except Exception as exc:
                logger.error(f"Erro no loop do tray: {exc}", exc_info=True)

        self._thread = threading.Thread(target=_runner, daemon=True, name="TrayThread")
        self._thread.start()

    def stop(self) -> None:
        """Stops and removes the tray icon."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
