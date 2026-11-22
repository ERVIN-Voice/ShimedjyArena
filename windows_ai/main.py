"""CLI / frozen entrypoint for the PC-only GCS Shimeji AI (g4f) + Voice Control (GS) app."""

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
        description="GCS Shimeji AI — Windows Desktop Pet with g4f AI & GS Voice Control"
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to avatar_config.json")
    parser.add_argument("--scale", type=int, default=None, help="Integer pixel scale factor (2..8)")
    parser.add_argument("--standalone", action="store_true", help="Run without Hub/Android networking")
    parser.add_argument("--hub-url", type=str, default=None, help="Connect to an existing Hub WebSocket URL")
    parser.add_argument("--port", type=int, default=8765, help="Embedded Hub WebSocket port (default: 8765)")
    parser.add_argument("--no-mdns", action="store_true", help="Disable mDNS LAN advertisement")
    parser.add_argument(
        "--camera",
        choices=["none", "webcam", "ps3_eye", "synthetic"],
        default="none",
        help="Optional 2D RGB camera backend",
    )
    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV camera device index")
    parser.add_argument("--ps3-mode", choices=["vga_60", "qvga_120", "vga_30"], default="vga_60")
    parser.add_argument("--no-camera-preview", action="store_true")
    parser.add_argument("--hand-model", type=Path, default=None)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--list-cameras", action="store_true")

    # AI (g4f) + GS Voice Control options
    parser.add_argument("--ai-model", type=str, default="default", help="g4f model name (default: 'default')")
    parser.add_argument("--voice-lang", type=str, default="ru-RU", help="Speech recognition language (default: ru-RU)")
    parser.add_argument("--continuous-voice", action="store_true", help="Start continuous GS voice listening immediately")
    parser.add_argument("--wake-word", action="store_true", help="Require wake word ('Чубрик') in continuous mode")
    parser.add_argument("--no-tts", action="store_true", help="Disable spoken TTS replies")
    parser.add_argument("--hide-ai-panel", action="store_true", help="Start with AI & GS panel minimized to tray")

    parser.add_argument("--self-test", action="store_true", help="Verify bundled assets, g4f, voice & MediaPipe, then exit 0")
    parser.add_argument("--self-test-log", type=Path, default=None, help="Optional file path for self-test output")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args(argv)


def _run_self_test(log_path: Path | None = None) -> int:
    """Smoke-test bundled assets, MediaPipe, g4f, SpeechRecognition, sounddevice & pyttsx3."""
    cfg = load_avatar_config()
    proto = load_protocol_config()
    assert cfg.visual.sprite_sheet.exists(), f"Missing spritesheet: {cfg.visual.sprite_sheet}"
    assert (PROJECT_ROOT / "web" / "index.html").exists(), "Missing web client asset"
    assert "auto" in proto.allowed_confirmation_sources, "Missing auto confirmation source"

    import cv2  # noqa: F401
    import g4f  # noqa: F401
    from g4f.client import Client  # noqa: F401
    import pyttsx3  # noqa: F401
    import speech_recognition as sr  # noqa: F401
    import websockets  # noqa: F401
    from PySide6.QtWidgets import QApplication  # noqa: F401
    from windows.hand_detection import DEFAULT_MEDIAPIPE_MODEL_PATH, create_best_hand_detector
    from windows_ai.ai_brain import ShimejiAIBrain, parse_local_voice_command
    from windows_ai.voice_engine import VoiceController  # noqa: F401

    if sys.platform == "win32":
        import sounddevice as sd

        _ = sd.query_devices()

    assert parse_local_voice_command("прыгни") is not None
    _ = ShimejiAIBrain(model="default")

    assert DEFAULT_MEDIAPIPE_MODEL_PATH.exists(), f"Missing hand_landmarker.task at {DEFAULT_MEDIAPIPE_MODEL_PATH}"
    det = create_best_hand_detector(prefer_mediapipe=True, auto_download_model=False)
    try:
        assert det.name == "mediapipe_21pt", f"Expected mediapipe_21pt detector, got {det.name}"
    finally:
        det.close()

    summary = (
        f"AI-VOICE SELF-TEST OK | frozen={getattr(sys, 'frozen', False)} | "
        f"g4f={getattr(g4f, '__version__', 'ok')} | sr={sr.__version__} | tracker={det.name}"
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
                (get_user_data_dir() / "gcs_shimeji_ai_error.log").write_text(tb, encoding="utf-8")
            except Exception:
                pass
            raise

    if args.list_cameras:
        found = probe_cameras(max_index=6)
        print(f"Available OpenCV camera indices: {found}" if found else "No working cameras detected.")
        return 0

    try:
        from PySide6.QtWidgets import QApplication
        from windows_ai.ai_desktop_pet import AIDesktopPetApp

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

        pet_app = AIDesktopPetApp(
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
            ai_model=args.ai_model,
            voice_language=args.voice_lang,
            continuous_voice=args.continuous_voice,
            require_wake_word=args.wake_word,
            tts_enabled=not args.no_tts,
            show_ai_panel=not args.hide_ai_panel,
        )

        def _handle_sigint(*_args: object) -> None:
            pet_app.shutdown()

        signal.signal(signal.SIGINT, _handle_sigint)
        return qt_app.exec()
    except Exception as exc:
        tb = traceback.format_exc()
        logging.getLogger(__name__).critical("Fatal AI startup error:\n%s", tb)
        log_path: Path | None = None
        try:
            log_path = get_user_data_dir() / "gcs_shimeji_ai_error.log"
            log_path.write_text(tb, encoding="utf-8")
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox

                app = QApplication.instance() or QApplication(sys.argv)
                msg = f"GCS Shimeji AI encountered an error:\n\n{exc}"
                if log_path is not None:
                    msg += f"\n\nFull error log saved to:\n{log_path}"
                QMessageBox.critical(None, "GCS Shimeji AI — Startup Error", msg)
            except Exception:
                pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
