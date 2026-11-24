"""Browser Shimeji — cross-tab & cross-PC browser companion with standalone Windows EXE server."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Shimeji Browser Server"
DEFAULT_PORT = 8787


def get_asset_root() -> Path:
    """Return read-only bundled project root (supports PyInstaller ``_MEIPASS``)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        return Path(meipass).resolve()
    return Path(__file__).resolve().parent.parent


def get_static_dir() -> Path:
    return get_asset_root() / "browser_pet" / "static"


def get_spritesheet_path() -> Path:
    return get_asset_root() / "assets" / "avatar" / "placeholder_spritesheet.png"


def get_avatar_config_path() -> Path:
    return get_asset_root() / "shared" / "avatar_config.json"


def get_user_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA") or Path.home())
            data_dir = base / "Shimeji-Browser-Server"
        else:
            data_dir = Path.home() / ".shimeji-browser-server"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir
    return Path.cwd()
