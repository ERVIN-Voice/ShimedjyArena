"""PyInstaller entrypoint for standalone GCS-Shimeji-Windows.exe."""
from __future__ import annotations

import multiprocessing
import sys


def _run() -> int:
    multiprocessing.freeze_support()
    from windows.main import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_run())
