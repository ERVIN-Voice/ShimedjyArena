"""PyInstaller entrypoint for standalone HandMouse-PC-Control-Windows.exe."""

from __future__ import annotations

import multiprocessing
import os
import traceback
from pathlib import Path


def _log_fatal(exc: BaseException) -> None:
    try:
        base = Path(os.environ.get("APPDATA", Path.home())) / "HandMouse-PC-Control"
        base.mkdir(parents=True, exist_ok=True)
        (base / "handmouse_error.log").write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


def _run() -> int:
    multiprocessing.freeze_support()
    try:
        from hand_mouse.main import main

        return main()
    except Exception as exc:
        _log_fatal(exc)
        raise


if __name__ == "__main__":
    raise SystemExit(_run())
