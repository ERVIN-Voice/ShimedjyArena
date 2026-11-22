"""PyInstaller entrypoint for standalone GCS-Shimeji-AI-Voice-Windows.exe (g4f + GS Voice)."""
from __future__ import annotations

import multiprocessing


def _run() -> int:
    multiprocessing.freeze_support()
    from windows_ai.main import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_run())
