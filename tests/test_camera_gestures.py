"""Tests for Stage 6: camera interfaces (PS3 Eye, webcam, Android stream),
non-depth 2D RGB hand detection, pinch & receive_pet gestures, and the
confirmation-only handoff safety gate.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

from windows.camera import (
    AndroidCameraStreamAdapter,
    OpenCVCamera,
    PS3EyeCamera,
    SyntheticCamera,
    create_camera,
)
from windows.hand_detection import (
    GestureRecognizer,
    GestureType,
    HandoffGestureConfirmationController,
    OpenCVHandDetector,
)


def test_camera_capabilities_explicitly_non_depth() -> None:
    ps3_vga = PS3EyeCamera(device_index=0, mode="vga_60")
    assert ps3_vga.capabilities.device_kind == "ps3_eye"
    assert ps3_vga.capabilities.is_depth_camera is False
    assert ps3_vga.capabilities.width == 640
    assert ps3_vga.capabilities.height == 480
    assert ps3_vga.capabilities.fps == 60.0
    assert ps3_vga.capabilities.supports_manual_exposure is True

    ps3_qvga = create_camera("ps3_eye", ps3_mode="qvga_120")
    assert ps3_qvga.capabilities.is_depth_camera is False
    assert ps3_qvga.capabilities.fps == 120.0

    webcam = OpenCVCamera(device_index=0)
    assert webcam.capabilities.is_depth_camera is False
    assert webcam.capabilities.device_kind == "webcam"

    android_cam = AndroidCameraStreamAdapter()
    assert android_cam.capabilities.is_depth_camera is False
    assert android_cam.capabilities.device_kind == "android"


def test_android_camera_stream_adapter_jpeg_roundtrip() -> None:
    adapter = AndroidCameraStreamAdapter()
    assert adapter.open() is True

    sample = np.full((120, 160, 3), (30, 80, 140), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", sample)
    assert ok
    assert adapter.push_jpeg_frame(encoded.tobytes(), timestamp=123.4) is True

    frame = adapter.read()
    assert frame is not None
    assert frame.source_kind == "android"
    assert frame.is_depth is False
    assert frame.frame_bgr.shape == (120, 160, 3)
    assert adapter.read() is None
    adapter.close()


def test_opencv_2d_hand_detector_recognizes_open_palm_and_pinch() -> None:
    cam = SyntheticCamera(width=320, height=240)
    detector = OpenCVHandDetector()
    recognizer = GestureRecognizer(detector=detector, hold_frames=3)

    with cam:
        # 1) Empty scene -> no hand
        cam.set_scene("empty")
        empty_frame = cam.read()
        assert empty_frame is not None
        empty_ev = recognizer.process_frame(empty_frame)
        assert not empty_ev.hand.detected
        assert empty_ev.gesture == GestureType.NONE

        # 2) Open palm -> RECEIVE_PET after dwell of 3 consecutive frames
        cam.set_scene("open_palm")
        ev1 = recognizer.process_frame(cam.read())  # type: ignore[arg-type]
        ev2 = recognizer.process_frame(cam.read())  # type: ignore[arg-type]
        ev3 = recognizer.process_frame(cam.read())  # type: ignore[arg-type]
        assert ev1.hand.detected and ev1.hand.depth_available is False
        assert ev1.gesture == GestureType.NONE  # dwell filter prevents single-frame trigger
        assert ev2.gesture == GestureType.NONE
        assert ev3.gesture == GestureType.RECEIVE_PET
        assert ev3.confidence >= 0.65

        # 3) Pinch silhouette -> PINCH after dwell
        recognizer.reset()
        cam.set_scene("pinch")
        p1 = recognizer.process_frame(cam.read())  # type: ignore[arg-type]
        p2 = recognizer.process_frame(cam.read())  # type: ignore[arg-type]
        p3 = recognizer.process_frame(cam.read())  # type: ignore[arg-type]
        assert p1.hand.detected
        assert p3.gesture == GestureType.PINCH
        assert p3.confidence > 0.5


def test_gesture_controller_only_confirms_armed_handoff_never_initiates() -> None:
    cam = SyntheticCamera(width=320, height=240)
    confirmations: list[tuple[str, str]] = []

    controller = HandoffGestureConfirmationController(
        recognizer=GestureRecognizer(hold_frames=2),
        min_confidence=0.60,
        on_confirm_handoff=lambda hid, src: confirmations.append((hid, src)),
    )

    with cam:
        cam.set_scene("open_palm")

        # Unarmed: camera MUST NEVER initiate a handoff even with sustained open palm
        for _ in range(5):
            assert controller.process_frame(cam.read()) is None  # type: ignore[arg-type]
        assert confirmations == []

        # Arm for incoming offer, show pinch -> must NOT confirm
        controller.arm_for_incoming_offer("ho-cam-001", expires_at=time.time() + 6.0)
        cam.set_scene("pinch")
        for _ in range(4):
            assert controller.process_frame(cam.read()) is None  # type: ignore[arg-type]
        assert confirmations == []

        # Show RECEIVE_PET -> confirms ONCE and immediately disarms
        cam.set_scene("open_palm")
        assert controller.process_frame(cam.read()) is None  # frame 1 (dwell)
        result = controller.process_frame(cam.read())  # frame 2 -> confirm!
        assert result == "ho-cam-001"
        assert confirmations == [("ho-cam-001", "gesture")]
        assert controller.pending_handoff_id is None

        # Subsequent frames cannot double-confirm
        for _ in range(4):
            assert controller.process_frame(cam.read()) is None  # type: ignore[arg-type]
        assert len(confirmations) == 1

        # Expired offer must be ignored and disarmed
        controller.arm_for_incoming_offer("ho-expired", expires_at=time.time() - 0.1)
        assert controller.process_frame(cam.read()) is None  # type: ignore[arg-type]
        assert controller.pending_handoff_id is None
        assert len(confirmations) == 1
