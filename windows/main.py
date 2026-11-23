"""Entry point for the GCS Shimeji Windows desktop companion application."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import traceback
from pathlib import Path

from shared.config_loader import PROJECT_ROOT, get_user_data_dir, load_avatar_config, load_protocol_config
from windows.camera import CameraBackend, create_camera, probe_cameras


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gcs-shimeji",
        description="GCS Shimeji — Cross-Device Pixel Desktop Companion (Windows)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to custom avatar_config.json",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=None,
        help="Integer pixel scale multiplier (nearest-neighbor, e.g. 3, 4, 5)",
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Run Windows Shimeji locally without starting or connecting to GCS Local Hub",
    )
    parser.add_argument(
        "--hub-url",
        default=None,
        help="Connect to an existing GCS Local Hub (e.g. ws://127.0.0.1:8765) instead of starting embedded Hub",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for the embedded GCS Local Hub (default: 8765)",
    )
    parser.add_argument(
        "--no-mdns",
        action="store_true",
        help="Disable mDNS/Zeroconf LAN broadcast on embedded Hub",
    )
    parser.add_argument(
        "--camera",
        choices=["none", "webcam", "ps3_eye"],
        default="none",
        help="Enable live camera backend for real-time gesture control & handoff confirmation",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=0,
        help="OpenCV camera device index (default: 0)",
    )
    parser.add_argument(
        "--ps3-mode",
        choices=["vga_60", "qvga_120", "vga_30"],
        default="vga_60",
        help="PS3 Eye resolution/FPS preset (default: vga_60)",
    )
    parser.add_argument(
        "--no-camera-preview",
        action="store_true",
        help="Start camera gesture tracking without initially showing the HUD preview window",
    )
    parser.add_argument(
        "--hand-model",
        type=Path,
        default=None,
        help="Optional path to local MediaPipe hand_landmarker.task model for 21-point neural hand tracking",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Do not attempt one-time download of local hand_landmarker.task if missing",
    )
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="Probe available camera indices and exit",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Verify bundled assets, MediaPipe model, protocol, and GUI imports, then exit 0",
    )
    parser.add_argument(
        "--self-test-log",
        type=Path,
        default=None,
        help="Optional file path to write self-test summary (useful for windowed Windows .exe)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging verbosity",
    )
    return parser.parse_args(argv)


def _run_self_test(log_path: Path | None = None) -> int:
    """Smoke-test bundled assets and runtime dependencies (used to verify PyInstaller .exe builds)."""
    cfg = load_avatar_config()
    proto = load_protocol_config()
    assert cfg.visual.sprite_sheet.exists(), f"Missing spritesheet: {cfg.visual.sprite_sheet}"
    web_index = PROJECT_ROOT / "web" / "index.html"
    assert web_index.exists(), f"Missing web client asset: {web_index}"
    assert "love" in proto.emotions, "Unified emotions missing from protocol"

    import cv2  # noqa: F401
    import websockets  # noqa: F401
    from PySide6.QtWidgets import QApplication  # noqa: F401
    from server.hub import GCSLocalHub  # noqa: F401
    from windows import desktop_pet, pet_profile, site_reactor, toy_physics, toy_windows  # noqa: F401
    from windows.hand_detection import DEFAULT_MEDIAPIPE_MODEL_PATH, create_best_hand_detector

    yt = site_reactor.classify_window_title("Funny Cats - YouTube - Google Chrome")
    assert yt is not None and yt.prop_name == "popcorn", f"YouTube reaction failed: {yt}"

    assert DEFAULT_MEDIAPIPE_MODEL_PATH.exists(), f"Missing bundled hand_landmarker.task at {DEFAULT_MEDIAPIPE_MODEL_PATH}"
    det = create_best_hand_detector(prefer_mediapipe=True, auto_download_model=False)
    try:
        assert det.name == "mediapipe_21pt", f"Expected mediapipe_21pt detector, got {det.name}"
    finally:
        det.close()

    summary = (
        f"SELF-TEST OK | frozen={getattr(sys, 'frozen', False)} | "
        f"root={PROJECT_ROOT} | model={DEFAULT_MEDIAPIPE_MODEL_PATH.name} | tracker={det.name}"
    )
    try:
        print(summary)
    except Exception:
        pass
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(summary + "\n", encoding="utf-8")
    return 0


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
                (get_user_data_dir() / "gcs_shimeji_error.log").write_text(tb, encoding="utf-8")
            except Exception:
                pass
            raise

    if args.list_cameras:
        found = probe_cameras(max_index=6)
        if found:
            print(f"Available OpenCV camera indices: {found}")
        else:
            print("No working cameras detected on indices 0..5.")
        return 0

    try:
        from PySide6.QtWidgets import QApplication
        from windows.desktop_pet import DesktopPetApp

        avatar_cfg = load_avatar_config(args.config)
        camera: CameraBackend | None = None
        if args.camera != "none":
            camera = create_camera(
                args.camera,
                device_index=args.camera_index,
                ps3_mode=args.ps3_mode,
            )

        qt_app = QApplication(sys.argv)
        qt_app.setQuitOnLastWindowClosed(False)

        pet_app = DesktopPetApp(
            qt_app,
            avatar_config=avatar_cfg,
            scale=args.scale,
            standalone=args.standalone,
            hub_url=args.hub_url,
            hub_port=args.port,
            enable_mdns=not args.no_mdns,
            camera=camera,
            show_camera_preview=not args.no_camera_preview,
            hand_model_path=args.hand_model,
            auto_download_hand_model=not args.offline,
        )

        def _handle_sigint(*_args: object) -> None:
            pet_app.shutdown()

        signal.signal(signal.SIGINT, _handle_sigint)
        return qt_app.exec()
    except Exception as exc:
        tb = traceback.format_exc()
        logging.getLogger(__name__).critical("Fatal startup error:\n%s", tb)
        log_path: Path | None = None
        try:
            log_path = get_user_data_dir() / "gcs_shimeji_error.log"
            log_path.write_text(tb, encoding="utf-8")
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox

                app = QApplication.instance() or QApplication(sys.argv)
                msg = f"GCS Shimeji encountered an error:\n\n{exc}"
                if log_path is not None:
                    msg += f"\n\nFull error log saved to:\n{log_path}"
                QMessageBox.critical(None, "GCS Shimeji — Startup Error", msg)
            except Exception:
                pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
