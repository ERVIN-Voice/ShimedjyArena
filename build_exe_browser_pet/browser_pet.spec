# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Shimeji-Browser-Server-Windows.exe (lightweight, no OpenCV/MediaPipe)."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
ICON_PATH = ROOT / "build_exe_browser_pet" / "browser_pet.ico"

datas = [
    (str(ROOT / "browser_pet" / "static"), "browser_pet/static"),
    (str(ROOT / "assets" / "avatar" / "placeholder_spritesheet.png"), "assets/avatar"),
    (str(ROOT / "shared" / "avatar_config.json"), "shared"),
]

hiddenimports = (
    collect_submodules("browser_pet")
    + collect_submodules("websockets")
    + [
        "qrcode",
        "qrcode.image.pil",
        "PIL.Image",
        "PIL.ImageDraw",
    ]
)

excludes = [
    "cv2",
    "mediapipe",
    "numpy",
    "matplotlib",
    "scipy",
    "tkinter",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtQuick",
    "PySide6.QtQml",
]

a = Analysis(
    [str(ROOT / "build_exe_browser_pet" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Shimeji-Browser-Server-Windows",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
)
