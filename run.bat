@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================
echo   GCS Shimeji - Pixel Desktop Companion
echo ============================================

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.11+ was not found in PATH.
    echo Please install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [2/3] Checking dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

if not exist "assets\avatar\placeholder_spritesheet.png" (
    echo Generating placeholder pixel sprite sheet...
    ".venv\Scripts\python.exe" assets\avatar\generate_placeholder_sprites.py
)

echo [3/3] Starting GCS Shimeji (Windows Desktop Pet + Local Hub)...
".venv\Scripts\python.exe" -m windows.main %*

endlocal
