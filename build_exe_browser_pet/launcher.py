"""PyInstaller launcher for Shimeji-Browser-Server-Windows.exe."""

from __future__ import annotations

import multiprocessing
import sys

from browser_pet.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
