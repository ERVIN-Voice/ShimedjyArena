# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for GCS-Shimeji-AI-Voice-Windows.exe (PC-only g4f AI + GS Voice edition)."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
REPO_ROOT = SPEC_DIR.parent

datas = [
    (str(REPO_ROOT / "shared" / "avatar_config.json"), "shared"),
    (str(REPO_ROOT / "shared" / "protocol.json"), "shared"),
    (str(REPO_ROOT / "assets" / "avatar"), "assets/avatar"),
    (str(REPO_ROOT / "web"), "web"),
]

hand_model = REPO_ROOT / "assets" / "models" / "hand_landmarker.task"
if hand_model.exists():
    datas.append((str(hand_model), "assets/models"))

binaries = []
hiddenimports = [
    "PIL._tkinter_finder",
    "qrcode",
    "qrcode.image.pil",
    "pyttsx3.drivers",
    "pyttsx3.drivers.sapi5",
    "comtypes",
    "comtypes.client",
    "win32com",
    "win32com.client",
]

for pkg in ("mediapipe", "g4f", "certifi", "sounddevice", "speech_recognition", "curl_cffi", "aiohttp"):
    try:
        p_datas, p_bins, p_hidden = collect_all(pkg)
        datas += p_datas
        binaries += p_bins
        hiddenimports += p_hidden
    except Exception:
        pass

for extra_data_pkg in ("_sounddevice_data",):
    try:
        datas += collect_data_files(extra_data_pkg)
    except Exception:
        pass

for sub_pkg in ("pyttsx3", "zeroconf", "websockets", "server", "shared", "windows", "windows_ai"):
    try:
        hiddenimports += collect_submodules(sub_pkg)
    except Exception:
        pass

excludes = [
    "pytest",
    "_pytest",
    "tkinter",
    "scipy",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtXml",
]

icon_file = SPEC_DIR / "gcs_shimeji_ai.ico"

a = Analysis(
    [str(SPEC_DIR / "launcher.py")],
    pathex=[str(REPO_ROOT)],
    binaries=binaries,
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
    name="GCS-Shimeji-AI-Voice-Windows",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if icon_file.exists() else None,
)
