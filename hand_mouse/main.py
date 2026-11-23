"""CLI & frozen Windows entrypoint for HandMouse PC Control."""

from __future__ import annotations

import argparse
import logging
import platform
import sys
from pathlib import Path
from typing import Sequence

from hand_mouse.cursor_engine import HandMouseConfig, HandMouseController, PointerMode
from hand_mouse.win_input import ScreenBounds, VirtualPointerBackend, enable_windows_dpi_awareness
from windows.hand_detection import (
    DEFAULT_MEDIAPIPE_MODEL_PATH,
    GestureEvent,
    GestureType,
    HandDetectionResult,
    Point2D,
    create_best_hand_detector,
    get_last_mediapipe_status,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hand-mouse",
        description="HandMouse PC Control — anti-misclick PC gesture mouse using Shimeji 21-pt hand tracking",
    )
    parser.add_argument(
        "--camera",
        choices=("webcam", "ps3_eye"),
        default="webcam",
        help="Camera backend (default: webcam)",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=0,
        help="OpenCV camera index (default: 0)",
    )
    parser.add_argument(
        "--paused",
        action="store_true",
        help="Launch with gesture pointer paused (press F8 to start)",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="Start minimized to the system tray",
    )
    parser.add_argument(
        "--primary-only",
        action="store_true",
        help="Map gestures to the primary monitor only instead of full virtual desktop",
    )
    parser.add_argument(
        "--no-download-model",
        action="store_true",
        help="Do not attempt to download hand_landmarker.task if missing",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Verify bundled MediaPipe, OpenCV, Win32 pointer backend, and anti-misclick engine, then exit",
    )
    parser.add_argument(
        "--self-test-log",
        type=str,
        default="",
        help="Optional path to write self-test report (useful for windowed frozen .exe)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args(argv)


def _run_self_test(log_path: str = "") -> int:
    lines: list[str] = ["[HandMouse self-test] starting..."]
    try:
        import cv2  # noqa: F401
        import numpy as np  # noqa: F401
        import PySide6  # noqa: F401

        lines.append(f"OK PySide6={PySide6.__version__} OpenCV={cv2.__version__}")

        model_exists = DEFAULT_MEDIAPIPE_MODEL_PATH.exists()
        lines.append(f"MediaPipe model path={DEFAULT_MEDIAPIPE_MODEL_PATH} exists={model_exists}")
        detector = create_best_hand_detector(prefer_mediapipe=True, auto_download_model=False)
        lines.append(f"OK detector={detector.name} status='{get_last_mediapipe_status()}'")

        if platform.system() == "Windows":
            from hand_mouse.win_input import WindowsPointerBackend

            win_ptr = WindowsPointerBackend()
            bounds = win_ptr.get_screen_bounds()
            pos = win_ptr.get_cursor_pos()
            lines.append(f"OK WindowsPointerBackend bounds={bounds} cursor={pos}")

        # Run a quick deterministic anti-misclick simulation on VirtualPointerBackend
        vptr = VirtualPointerBackend(ScreenBounds(0, 0, 1920, 1080))
        cfg = HandMouseConfig(mirror_x=False, left_click_confirm_sec=0.10, yield_to_physical_mouse=False)
        ctrl = HandMouseController(vptr, cfg)
        hand_aim = HandDetectionResult(
            detected=True,
            confidence=0.95,
            palm_center=Point2D(0.5, 0.5),
            pinch_ratio=0.85,
            tracker_name="mediapipe",
        )
        s1 = ctrl.update(GestureEvent(GestureType.POINT, 0.95, 1.0, hand_aim), now=1.0)
        hand_pinch = HandDetectionResult(
            detected=True,
            confidence=0.95,
            palm_center=Point2D(0.52, 0.52),
            pinch_ratio=0.30,
            tracker_name="mediapipe",
        )
        ctrl.update(GestureEvent(GestureType.PINCH, 0.95, 1.02, hand_pinch), now=1.02)
        s2 = ctrl.update(GestureEvent(GestureType.PINCH, 0.95, 1.15, hand_pinch), now=1.15)
        assert s1.mode == PointerMode.AIM
        assert s2.mode == PointerMode.LEFT_HOLD
        assert s2.target_x == s1.target_x and s2.target_y == s1.target_y
        assert s2.last_click is not None and s2.last_click.kind == "left"
        lines.append(f"OK anti-misclick lock verified at ({s2.target_x}, {s2.target_y})")

        # Verify ThreadedCameraReader + GestureRecognizer + draw_hand_overlay end-to-end
        from windows.camera import ThreadedCameraReader, create_camera
        from windows.hand_detection import GestureRecognizer, draw_hand_overlay

        syn_cam = create_camera("synthetic")
        reader = ThreadedCameraReader(syn_cam, max_poll_hz=60.0)
        assert reader.start()
        try:
            frame = None
            import time as _time

            for _ in range(30):
                frame, _ = reader.get_latest(0)
                if frame is not None:
                    break
                _time.sleep(0.01)
            assert frame is not None
            rec = GestureRecognizer(detector=detector, hold_frames=1)
            ev = rec.process_frame(frame)
            st = ctrl.update(ev, now=2.0)
            canvas = draw_hand_overlay(
                frame.frame_bgr,
                ev,
                active_action=f"{st.mode.value.upper()} ({st.target_x}, {st.target_y})",
                fps=reader.fps,
                control_zone_norm=cfg.active_zone_rect(),
            )
            assert canvas is not None and canvas.size > 0
            lines.append("OK ThreadedCameraReader + GestureRecognizer + overlay verified")
        finally:
            reader.stop()

        # Verify GUI modules & actual widget instantiation on Windows desktop
        from hand_mouse import control_panel, target_overlay  # noqa: F401

        if platform.system() == "Windows":
            from PySide6 import QtWidgets

            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            overlay = target_overlay.ClickTargetOverlayWindow()
            overlay.update_state(s2, use_qt_cursor_pos=False)
            overlay.close()
            win = control_panel.HandMouseMainWindow(
                camera_kind="webcam",
                camera_index=0,
                start_enabled=False,
                minimized_to_tray=False,
                auto_download_model=False,
            )
            win._cleanup()
            win.close()
            app.processEvents()
            lines.append("OK GUI overlay & control panel instantiated cleanly")
        else:
            lines.append("OK GUI overlay & control panel modules imported")
        lines.append("SELF-TEST PASSED")
        report = "\n".join(lines) + "\n"
        if log_path:
            Path(log_path).write_text(report, encoding="utf-8")
        print(report, end="")
        return 0
    except Exception as exc:
        lines.append(f"SELF-TEST FAILED: {exc!r}")
        report = "\n".join(lines) + "\n"
        if log_path:
            Path(log_path).write_text(report, encoding="utf-8")
        print(report, file=sys.stderr, end="")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.self_test:
        return _run_self_test(args.self_test_log)

    enable_windows_dpi_awareness()

    from PySide6 import QtWidgets
    from hand_mouse.control_panel import HandMouseMainWindow

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("HandMouse PC Control")
    app.setQuitOnLastWindowClosed(False)

    win = HandMouseMainWindow(
        camera_kind=args.camera,
        camera_index=args.camera_index,
        start_enabled=not args.paused,
        minimized_to_tray=args.tray,
        primary_monitor_only=args.primary_only,
        auto_download_model=not args.no_download_model,
    )
    app.aboutToQuit.connect(win._cleanup)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
