"""CLI & GUI entry point for Shimeji Browser Server (.exe)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import traceback
import urllib.request
from pathlib import Path

from browser_pet import (
    APP_NAME,
    DEFAULT_PORT,
    get_spritesheet_path,
    get_static_dir,
    get_user_data_dir,
)
from browser_pet.server import BrowserPetServer

logger = logging.getLogger("browser_pet.main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="shimeji-browser-server",
        description="Standalone server for Browser Shimeji (throw pets between browser tabs & different PCs).",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP + WebSocket port (default: {DEFAULT_PORT})")
    parser.add_argument("--headless", action="store_true", help="Run server in console without PySide6 GUI window")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open a local browser tab on startup")
    parser.add_argument("--self-test", action="store_true", help="Run end-to-end HTTP + 2-PC WebSocket throw self-test")
    parser.add_argument("--self-test-log", type=Path, default=None, help="Optional file path to write self-test summary")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


async def _async_self_test() -> str:
    """Verify bundled assets, HTTP serving, and real 2-tab WebSocket pet throwing."""
    import websockets

    static_dir = get_static_dir()
    sprite = get_spritesheet_path()
    assert (static_dir / "index.html").is_file(), f"Missing index.html in {static_dir}"
    assert (static_dir / "app.js").is_file(), f"Missing app.js in {static_dir}"
    assert (static_dir / "style.css").is_file(), f"Missing style.css in {static_dir}"
    assert sprite.is_file(), f"Missing spritesheet at {sprite}"

    # Verify GUI module imports cleanly without instantiating QApplication (headless CI safe)
    import qrcode  # noqa: F401

    if sys.platform == "win32" or getattr(sys, "frozen", False):
        from browser_pet import gui  # noqa: F401
    else:
        import importlib.util

        assert importlib.util.find_spec("browser_pet.gui") is not None

    server = BrowserPetServer(host="127.0.0.1", port=0)
    port = await server.start()
    try:
        # 1) HTTP checks (index.html + spritesheet.png + /api/info)
        def _fetch(path: str) -> tuple[int, bytes]:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
                return resp.status, resp.read()

        st_html, body_html = await asyncio.to_thread(_fetch, "/")
        assert st_html == 200 and b"petCanvas" in body_html
        st_png, body_png = await asyncio.to_thread(_fetch, "/assets/spritesheet.png")
        assert st_png == 200 and body_png[:8] == b"\x89PNG\r\n\x1a\n"
        st_info, body_info = await asyncio.to_thread(_fetch, "/api/info")
        assert st_info == 200 and json.loads(body_info.decode("utf-8"))["port"] == port

        # 2) Two real WebSocket clients ("PC-1" and "PC-2") throw pet across right edge!
        ws_url = f"ws://127.0.0.1:{port}/ws"
        async with websockets.connect(ws_url) as ws1, websockets.connect(ws_url) as ws2:
            await ws1.send(json.dumps({"type": "hello", "tab_id": "pc-1", "label": "PC-1"}))
            await ws2.send(json.dumps({"type": "hello", "tab_id": "pc-2", "label": "PC-2"}))

            pet_id = None
            for _ in range(10):
                msg = json.loads(await asyncio.wait_for(ws1.recv(), timeout=3.0))
                if msg.get("type") == "room_state" and msg.get("pets"):
                    pet_id = msg["pets"][0]["pet_id"]
                    if len(msg.get("tabs", [])) == 2:
                        break
            assert pet_id is not None, "Initial pet was not spawned for PC-1"

            # Throw pet from PC-1 right edge -> PC-2 left edge with note!
            await ws1.send(
                json.dumps(
                    {
                        "type": "throw_pet",
                        "pet_id": pet_id,
                        "edge": "right",
                        "ny": 0.45,
                        "vx": 820.0,
                        "vy": -200.0,
                        "note_text": "Привет с ПК-1!",
                    }
                )
            )

            arrived = None
            for _ in range(10):
                msg = json.loads(await asyncio.wait_for(ws2.recv(), timeout=3.0))
                if msg.get("type") == "pet_arrive":
                    arrived = msg
                    break
            assert arrived is not None, "PC-2 never received pet_arrive!"
            assert arrived["entry_edge"] == "left"
            assert arrived["pet"]["owner_tab_id"] == "pc-2"
            assert arrived["note_text"] == "Привет с ПК-1!"
    finally:
        await server.stop()

    return (
        f"SELF-TEST OK | app={APP_NAME} | frozen={getattr(sys, 'frozen', False)} | "
        f"http+ws_port={port} | cross_pc_throw=OK"
    )


def _run_self_test(log_path: Path | None = None) -> int:
    summary = asyncio.run(_async_self_test())
    try:
        print(summary)
    except Exception:
        pass
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(summary + "\n", encoding="utf-8")
    return 0


async def _run_headless(host: str, port: int) -> None:
    server = BrowserPetServer(host=host, port=port)
    bound = await server.start()
    print(f"{APP_NAME} listening on port {bound}")
    for u in server.share_urls:
        print(f"  -> {u}")
    stop_evt = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_evt.set)
        except NotImplementedError:
            pass
    try:
        await stop_evt.wait()
    finally:
        await server.stop()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.self_test:
        try:
            return _run_self_test(log_path=args.self_test_log)
        except Exception:
            tb = traceback.format_exc()
            try:
                (get_user_data_dir() / "browser_pet_error.log").write_text(tb, encoding="utf-8")
            except Exception:
                pass
            raise

    if args.headless:
        asyncio.run(_run_headless(args.host, args.port))
        return 0

    try:
        from PySide6.QtWidgets import QApplication
        from browser_pet.gui import ServerControlWindow

        qt_app = QApplication(sys.argv)
        qt_app.setQuitOnLastWindowClosed(False)
        win = ServerControlWindow(
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
        )
        win.show()
        return qt_app.exec()
    except Exception:
        tb = traceback.format_exc()
        logger.critical("Fatal error:\n%s", tb)
        try:
            (get_user_data_dir() / "browser_pet_error.log").write_text(tb, encoding="utf-8")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
