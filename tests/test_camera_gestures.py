"""Tests for Stage 6 + Real Gesture Control:
- Camera interfaces (PS3 Eye non-depth, webcam, Android stream, ThreadedCameraReader)
- 2D RGB OpenCV hand detection (open_palm, pinch, point, fist, background calibration)
- 21-landmark MediaPipe geometry classifier (open_palm, pinch, point, fist)
- Real-time GesturePetController driving PetEngine (pinch-drag, point-call, palm-wake/jump, fist-sleep)
- Confirmation-only handoff safety gate (camera NEVER initiates handoff autonomously)
- Live HUD debug overlay rendering
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from shared.config_loader import load_avatar_config
from windows.camera import (
    AndroidCameraStreamAdapter,
    CameraFrame,
    OpenCVCamera,
    PS3EyeCamera,
    SyntheticCamera,
    ThreadedCameraReader,
    create_camera,
)
from windows.gesture_controller import GesturePetController
from windows.hand_detection import (
    GestureRecognizer,
    GestureType,
    HandoffGestureConfirmationController,
    OpenCVHandDetector,
    Point2D,
    analyze_21_landmarks,
    draw_hand_overlay,
    enhance_low_quality_frame,
    get_last_mediapipe_status,
)
from windows.pet_engine import DesktopBounds, PetEngine, ScreenRect


def _make_21_skeleton(pose: str, cx: float = 0.5, cy: float = 0.6) -> list[Point2D]:
    """Construct a geometrically accurate 21-point normalized hand skeleton for unit testing."""
    wrist = Point2D(cx, cy + 0.16)
    # Palm MCP joints
    thumb_cmc = Point2D(cx - 0.06, cy + 0.11)
    thumb_mcp = Point2D(cx - 0.09, cy + 0.06)
    index_mcp = Point2D(cx - 0.06, cy)
    middle_mcp = Point2D(cx - 0.01, cy - 0.01)
    ring_mcp = Point2D(cx + 0.04, cy)
    pinky_mcp = Point2D(cx + 0.08, cy + 0.02)

    if pose == "open_palm":
        thumb = [thumb_cmc, thumb_mcp, Point2D(cx - 0.14, cy + 0.01), Point2D(cx - 0.19, cy - 0.04)]
        index = [index_mcp, Point2D(cx - 0.07, cy - 0.08), Point2D(cx - 0.08, cy - 0.14), Point2D(cx - 0.09, cy - 0.20)]
        middle = [middle_mcp, Point2D(cx - 0.01, cy - 0.10), Point2D(cx - 0.01, cy - 0.16), Point2D(cx - 0.01, cy - 0.22)]
        ring = [ring_mcp, Point2D(cx + 0.05, cy - 0.09), Point2D(cx + 0.06, cy - 0.15), Point2D(cx + 0.07, cy - 0.20)]
        pinky = [pinky_mcp, Point2D(cx + 0.10, cy - 0.06), Point2D(cx + 0.12, cy - 0.11), Point2D(cx + 0.14, cy - 0.16)]
    elif pose == "pinch":
        pinch_pt = Point2D(cx - 0.06, cy - 0.10)
        thumb = [thumb_cmc, thumb_mcp, Point2D(cx - 0.09, cy - 0.04), Point2D(pinch_pt.x - 0.01, pinch_pt.y)]
        index = [index_mcp, Point2D(cx - 0.07, cy - 0.06), Point2D(cx - 0.06, cy - 0.09), pinch_pt]
        middle = [middle_mcp, Point2D(cx - 0.01, cy - 0.03), Point2D(cx - 0.01, cy + 0.02), Point2D(cx - 0.01, cy + 0.05)]
        ring = [ring_mcp, Point2D(cx + 0.04, cy - 0.02), Point2D(cx + 0.04, cy + 0.03), Point2D(cx + 0.04, cy + 0.06)]
        pinky = [pinky_mcp, Point2D(cx + 0.08, cy), Point2D(cx + 0.08, cy + 0.04), Point2D(cx + 0.08, cy + 0.07)]
    elif pose == "point":
        thumb = [thumb_cmc, thumb_mcp, Point2D(cx - 0.06, cy + 0.03), Point2D(cx - 0.04, cy + 0.04)]
        index = [index_mcp, Point2D(cx - 0.06, cy - 0.09), Point2D(cx - 0.06, cy - 0.16), Point2D(cx - 0.06, cy - 0.23)]
        middle = [middle_mcp, Point2D(cx - 0.01, cy - 0.02), Point2D(cx - 0.01, cy + 0.03), Point2D(cx - 0.01, cy + 0.06)]
        ring = [ring_mcp, Point2D(cx + 0.04, cy - 0.01), Point2D(cx + 0.04, cy + 0.04), Point2D(cx + 0.04, cy + 0.07)]
        pinky = [pinky_mcp, Point2D(cx + 0.08, cy + 0.01), Point2D(cx + 0.08, cy + 0.05), Point2D(cx + 0.08, cy + 0.08)]
    elif pose == "peace":
        thumb = [thumb_cmc, thumb_mcp, Point2D(cx - 0.06, cy + 0.03), Point2D(cx - 0.04, cy + 0.04)]
        index = [index_mcp, Point2D(cx - 0.07, cy - 0.09), Point2D(cx - 0.09, cy - 0.16), Point2D(cx - 0.11, cy - 0.23)]
        middle = [middle_mcp, Point2D(cx + 0.01, cy - 0.10), Point2D(cx + 0.03, cy - 0.17), Point2D(cx + 0.05, cy - 0.24)]
        ring = [ring_mcp, Point2D(cx + 0.04, cy - 0.01), Point2D(cx + 0.04, cy + 0.04), Point2D(cx + 0.04, cy + 0.07)]
        pinky = [pinky_mcp, Point2D(cx + 0.08, cy + 0.01), Point2D(cx + 0.08, cy + 0.05), Point2D(cx + 0.08, cy + 0.08)]
    elif pose == "tight_fist":
        # Thumb pressed tightly against curled index (tricky real-world case that used to misfire PINCH)
        thumb = [thumb_cmc, thumb_mcp, Point2D(cx - 0.06, cy + 0.04), Point2D(cx - 0.052, cy + 0.065)]
        index = [index_mcp, Point2D(cx - 0.06, cy - 0.02), Point2D(cx - 0.06, cy + 0.03), Point2D(cx - 0.05, cy + 0.07)]
        middle = [middle_mcp, Point2D(cx - 0.01, cy - 0.02), Point2D(cx - 0.01, cy + 0.03), Point2D(cx - 0.01, cy + 0.07)]
        ring = [ring_mcp, Point2D(cx + 0.04, cy - 0.01), Point2D(cx + 0.04, cy + 0.04), Point2D(cx + 0.04, cy + 0.07)]
        pinky = [pinky_mcp, Point2D(cx + 0.08, cy + 0.01), Point2D(cx + 0.08, cy + 0.05), Point2D(cx + 0.07, cy + 0.08)]
    else:  # fist
        thumb = [thumb_cmc, thumb_mcp, Point2D(cx - 0.04, cy + 0.04), Point2D(cx - 0.01, cy + 0.05)]
        index = [index_mcp, Point2D(cx - 0.06, cy - 0.02), Point2D(cx - 0.06, cy + 0.03), Point2D(cx - 0.05, cy + 0.07)]
        middle = [middle_mcp, Point2D(cx - 0.01, cy - 0.02), Point2D(cx - 0.01, cy + 0.03), Point2D(cx - 0.01, cy + 0.07)]
        ring = [ring_mcp, Point2D(cx + 0.04, cy - 0.01), Point2D(cx + 0.04, cy + 0.04), Point2D(cx + 0.04, cy + 0.07)]
        pinky = [pinky_mcp, Point2D(cx + 0.08, cy + 0.01), Point2D(cx + 0.08, cy + 0.05), Point2D(cx + 0.07, cy + 0.08)]

    return [wrist, *thumb, *index, *middle, *ring, *pinky]


def _rotate_skeleton(pts: list[Point2D], angle_deg: float) -> list[Point2D]:
    import math

    rad = math.radians(angle_deg)
    ca, sa = math.cos(rad), math.sin(rad)
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    return [
        Point2D(
            x=cx + (p.x - cx) * ca - (p.y - cy) * sa,
            y=cy + (p.x - cx) * sa + (p.y - cy) * ca,
        )
        for p in pts
    ]


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


def test_threaded_camera_reader_captures_frames_non_blocking() -> None:
    cam = SyntheticCamera(width=160, height=120, fps=60.0)
    cam.set_scene("open_palm")
    reader = ThreadedCameraReader(cam, max_poll_hz=60.0)
    assert reader.start() is True
    try:
        frame = None
        seq = 0
        for _ in range(30):
            frame, seq = reader.get_latest(seq)
            if frame is not None:
                break
            time.sleep(0.01)
        assert frame is not None
        assert seq >= 1
        assert frame.frame_bgr.shape == (120, 160, 3)
    finally:
        reader.stop()
    assert reader.is_running is False


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


def test_opencv_2d_hand_detector_recognizes_all_four_gestures_and_bg_calibration() -> None:
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
        assert ev1.gesture == GestureType.NONE
        assert ev2.gesture == GestureType.NONE
        assert ev3.gesture == GestureType.RECEIVE_PET
        assert ev3.confidence >= 0.65

        # Verify overlay renders both RGB and mask modes cleanly
        annotated = draw_hand_overlay(cam.read().frame_bgr, ev3, active_action="Test", fps=60.0, show_mask=False)  # type: ignore[union-attr]
        annotated_mask = draw_hand_overlay(cam.read().frame_bgr, ev3, active_action="Test", fps=60.0, show_mask=True)  # type: ignore[union-attr]
        assert annotated.shape == (240, 320, 3)
        assert annotated_mask.shape == (240, 320, 3)

        # 3) Pinch -> PINCH
        recognizer.reset()
        cam.set_scene("pinch")
        for _ in range(3):
            p_ev = recognizer.process_frame(cam.read())  # type: ignore[arg-type]
        assert p_ev.gesture == GestureType.PINCH
        assert p_ev.confidence > 0.5

        # 4) Point -> POINT
        recognizer.reset()
        cam.set_scene("point")
        for _ in range(3):
            pt_ev = recognizer.process_frame(cam.read())  # type: ignore[arg-type]
        assert pt_ev.gesture == GestureType.POINT

        # 5) Fist -> FIST
        recognizer.reset()
        cam.set_scene("fist")
        for _ in range(3):
            f_ev = recognizer.process_frame(cam.read())  # type: ignore[arg-type]
        assert f_ev.gesture == GestureType.FIST

        # 6) Background calibration suppresses static skin-colored wall
        wall_bg = np.full((240, 320, 3), (128, 168, 224), dtype=np.uint8)
        assert detector.calibrate_background(wall_bg) is True
        wall_frame = CameraFrame(frame_bgr=wall_bg.copy(), timestamp=time.time(), frame_index=99, source_kind="synthetic")
        assert detector.detect(wall_frame).detected is False
        detector.clear_background()


def test_mediapipe_21_landmark_geometry_classifier() -> None:
    recognizer = GestureRecognizer(hold_frames=1)

    for pose, expected in (
        ("open_palm", GestureType.RECEIVE_PET),
        ("pinch", GestureType.PINCH),
        ("point", GestureType.POINT),
        ("fist", GestureType.FIST),
    ):
        hand = analyze_21_landmarks(_make_21_skeleton(pose), confidence=0.95)
        assert hand.detected is True
        assert hand.depth_available is False
        assert len(hand.landmarks_21) == 21
        gesture, conf = recognizer.classify_hand(hand)
        assert gesture == expected, f"Expected {expected} for pose={pose}, got {gesture} ({hand})"
        assert conf >= 0.60


def test_gesture_pet_controller_real_time_interactions(tmp_path: Path) -> None:
    import random

    cfg = load_avatar_config()
    bounds = DesktopBounds([ScreenRect(0, 0, 1280, 720, is_primary=True)])
    engine = PetEngine(
        cfg,
        bounds,
        pet_width=128,
        pet_height=128,
        state_file=tmp_path / "state.json",
        rng=random.Random(42),
    )

    cam = SyntheticCamera(width=320, height=240)
    recognizer = GestureRecognizer(detector=OpenCVHandDetector(), hold_frames=2)
    confirmations: list[tuple[str, str]] = []
    handoff_gate = HandoffGestureConfirmationController(
        recognizer=recognizer,
        min_confidence=0.60,
        on_confirm_handoff=lambda hid, src: confirmations.append((hid, src)),
    )
    ctrl = GesturePetController(
        engine,
        recognizer,
        handoff_gate,
        release_grace_frames=2,
        fist_sleep_seconds=0.15,
    )

    with cam:
        # 1) PINCH grabs pet and drags it right as hand moves right
        cam.set_scene("pinch", hand_nx=0.25, hand_ny=0.45)
        for _ in range(3):
            ctrl.process_frame(cam.read())  # type: ignore[arg-type]
            engine.tick(1.0 / 60.0)
        assert ctrl.is_gesture_dragging is True
        assert engine.state == "DRAGGING"
        assert engine.animation == "grab"
        x_left = engine.x

        cam.set_scene("pinch", hand_nx=0.75, hand_ny=0.45)
        for _ in range(15):
            ctrl.process_frame(cam.read())  # type: ignore[arg-type]
            engine.tick(1.0 / 60.0)
        assert engine.x > x_left + 150.0

        # 2) Open hand releases drag cleanly
        cam.set_scene("open_palm", hand_nx=0.75, hand_ny=0.55)
        for _ in range(3):
            ctrl.process_frame(cam.read())  # type: ignore[arg-type]
        assert ctrl.is_gesture_dragging is False
        assert engine.state == "ACTIVE"

        # Let pet land on floor
        for _ in range(120):
            engine.tick(1.0 / 60.0)

        # 3) POINT on the far left calls pet to walk/run left
        cam.set_scene("point", hand_nx=0.20, hand_ny=0.55)
        for _ in range(3):
            ctrl.process_frame(cam.read())  # type: ignore[arg-type]
        assert engine.animation in ("walk", "run")
        assert engine.facing == "left"

        # 4) Sustained FIST puts pet to sleep
        cam.set_scene("fist", hand_nx=0.50, hand_ny=0.60)
        t0 = time.time()
        for step in range(5):
            raw = cam.read()
            assert raw is not None
            stamped = CameraFrame(
                frame_bgr=raw.frame_bgr,
                timestamp=t0 + step * 0.08,
                frame_index=raw.frame_index,
                source_kind=raw.source_kind,
            )
            ctrl.process_frame(stamped)
        assert engine.state == "SLEEPING"
        assert engine.animation == "sleep"

        # 5) Unarmed OPEN PALM wakes sleeping pet (and NEVER initiates a handoff!)
        cam.set_scene("open_palm", hand_nx=0.50, hand_ny=0.60)
        for _ in range(3):
            ctrl.process_frame(cam.read())  # type: ignore[arg-type]
        assert engine.state == "ACTIVE"
        assert confirmations == []

        # 6) Armed incoming handoff + OPEN PALM confirms via gesture
        handoff_gate.arm_for_incoming_offer("ho-live-99", expires_at=time.time() + 6.0)
        cam.set_scene("open_palm", hand_nx=0.50, hand_ny=0.60)
        update = None
        for _ in range(3):
            update = ctrl.process_frame(cam.read())  # type: ignore[arg-type]
            if update.confirmed_handoff_id:
                break
        assert update is not None
        assert update.confirmed_handoff_id == "ho-live-99"
        assert confirmations == [("ho-live-99", "gesture")]


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


def test_low_light_enhancement_lifts_dark_ps3_eye_frames() -> None:
    dark = np.full((240, 320, 3), (18, 22, 28), dtype=np.uint8)
    cv2.circle(dark, (160, 120), 40, (42, 55, 72), -1)
    enhanced = enhance_low_quality_frame(dark)
    assert enhanced.shape == dark.shape
    assert float(np.mean(enhanced)) > float(np.mean(dark)) + 15.0


def test_opencv_detector_ignores_stationary_face_and_torso_and_tracks_moving_hand() -> None:
    """Reproduces the real-world case where a user sits in frame (face + torso) and waves a hand."""
    detector = OpenCVHandDetector()
    skin_bgr = (128, 168, 224)

    def make_frame(hand_x: int | None, hand_y: int = 150, ts: float = 1.0) -> CameraFrame:
        img = np.full((240, 320, 3), (35, 28, 24), dtype=np.uint8)
        # Large stationary user face in upper-center + wide torso touching bottom
        cv2.ellipse(img, (160, 75), (34, 42), 0, 0, 360, skin_bgr, -1)
        cv2.rectangle(img, (70, 175), (250, 240), skin_bgr, -1)
        if hand_x is not None:
            cv2.circle(img, (hand_x, hand_y), 24, skin_bgr, -1)
        return CameraFrame(frame_bgr=img, timestamp=ts, frame_index=int(ts * 30), source_kind="ps3_eye")

    # 1) Motion-scored tracking without calibration: prime with stationary user, then move hand on the right
    detector.detect(make_frame(None, ts=1.0))
    detector.detect(make_frame(240, hand_y=140, ts=1.1))
    res_motion = detector.detect(make_frame(260, hand_y=135, ts=1.2))
    assert res_motion.detected is True
    assert res_motion.palm_center is not None
    # Must lock onto the moving hand on the right (x > 0.72), NOT the stationary face at x=0.50!
    assert res_motion.palm_center.x > 0.72

    # 2) Seated-user background calibration ("Ignore Me + BG"): completely erases user face & torso
    detector.clear_background()
    seated_only = make_frame(None, ts=2.0)
    assert detector.calibrate_background(seated_only.frame_bgr) is True
    assert detector.detect(seated_only).detected is False

    raised_hand = make_frame(65, hand_y=130, ts=2.1)
    res_bg = detector.detect(raised_hand)
    assert res_bg.detected is True
    assert res_bg.palm_center is not None
    assert res_bg.palm_center.x < 0.30

    # 3) Active Hand Zone (ROI) ignores face completely even without BG calibration
    detector.clear_background()
    detector.set_roi_norm((0.55, 0.15, 0.42, 0.75))
    res_roi = detector.detect(make_frame(265, hand_y=145, ts=3.0))
    assert res_roi.detected is True
    assert res_roi.palm_center is not None
    assert res_roi.palm_center.x > 0.72

    # 4) Adaptive skin calibration handles unusual camera tint
    tinted_hand = np.full((240, 320, 3), (30, 30, 30), dtype=np.uint8)
    cv2.circle(tinted_hand, (240, 130), 32, (95, 130, 175), -1)
    assert detector.calibrate_skin_from_roi(tinted_hand) is True


def test_opencv_detector_rejects_noisy_beige_wall_without_whiteout() -> None:
    """Regression test: warm beige indoor wall + PS3 Eye noise must NOT turn the skin mask white."""
    rng = np.random.default_rng(42)
    wall = np.full((240, 320, 3), (150, 172, 188), dtype=np.int16)
    noise = rng.integers(-12, 13, size=wall.shape, dtype=np.int16)
    noisy_wall = np.clip(wall + noise, 0, 255).astype(np.uint8)

    detector = OpenCVHandDetector()
    res = detector.detect(
        CameraFrame(frame_bgr=noisy_wall, timestamp=1.0, frame_index=1, source_kind="ps3_eye")
    )
    assert res.detected is False
    assert res.mask_preview is not None
    white_ratio = float(np.count_nonzero(res.mask_preview)) / float(res.mask_preview.size)
    assert white_ratio < 0.05
    assert isinstance(get_last_mediapipe_status(), str)


def test_pc_high_precision_rotated_hands_fist_before_pinch_and_hysteresis() -> None:
    """Verifies upgraded PC 21-point hand recognition accuracy across rotations,
    FIST-vs-PINCH disambiguation, PEACE detection, pinch hysteresis, and One-Euro smoothing.
    """
    from windows.hand_detection import HandDetectionResult, HandDetector, OneEuroFilter

    recognizer = GestureRecognizer(hold_frames=1)

    # 1) All 5 gestures (plus tricky tight_fist) work even when rotated +35° or -45°!
    for angle in (0.0, 35.0, -45.0):
        for pose, expected in (
            ("open_palm", GestureType.RECEIVE_PET),
            ("pinch", GestureType.PINCH),
            ("point", GestureType.POINT),
            ("peace", GestureType.PEACE),
            ("fist", GestureType.FIST),
            ("tight_fist", GestureType.FIST),
        ):
            rotated = _rotate_skeleton(_make_21_skeleton(pose), angle)
            hand = analyze_21_landmarks(rotated, confidence=0.95)
            gesture, conf = recognizer.classify_hand(hand)
            assert gesture == expected, f"angle={angle} pose={pose}: expected {expected}, got {gesture} ({hand})"
            assert conf >= 0.60

    # 2) Pinch hysteresis: stays locked during drag even if pinch_ratio loosens to 0.46
    loose_pinch = HandDetectionResult(
        detected=True,
        confidence=0.92,
        palm_center=Point2D(0.5, 0.5),
        extended_fingers=3,
        openness_score=0.55,
        pinch_ratio=0.46,
        tracker_name="mediapipe",
        landmarks_21=tuple(_make_21_skeleton("pinch")),
        finger_states=(False, False, True, True, True),
        avg_tip_ratio=1.65,
    )
    g_fresh, _ = recognizer.classify_hand(loose_pinch, currently_pinching=False)
    g_held, _ = recognizer.classify_hand(loose_pinch, currently_pinching=True)
    assert g_fresh != GestureType.PINCH
    assert g_held == GestureType.PINCH

    # 3) 1-frame classification flicker immunity while hand stays detected
    class _ScriptedDetector(HandDetector):
        def __init__(self, seq: list[HandDetectionResult]) -> None:
            self._seq = list(seq)
            self._idx = 0

        def detect(self, frame: CameraFrame) -> HandDetectionResult:
            res = self._seq[min(self._idx, len(self._seq) - 1)]
            self._idx += 1
            return res

    pinch_hand = analyze_21_landmarks(_make_21_skeleton("pinch"), confidence=0.95)
    ambiguous_hand = HandDetectionResult(
        detected=True,
        confidence=0.85,
        palm_center=Point2D(0.5, 0.5),
        extended_fingers=2,
        openness_score=0.45,
        pinch_ratio=0.72,
        tracker_name="mediapipe",
        landmarks_21=tuple(_make_21_skeleton("point")),
        finger_states=(True, False, True, False, False),
        avg_tip_ratio=1.55,
    )
    rec_stable = GestureRecognizer(
        detector=_ScriptedDetector([pinch_hand, pinch_hand, ambiguous_hand, pinch_hand]),
        hold_frames=2,
        miss_tolerance=1,
    )
    dummy_img = np.zeros((120, 160, 3), dtype=np.uint8)
    evs = [
        rec_stable.process_frame(CameraFrame(dummy_img, timestamp=1.0 + i * 0.033, frame_index=i, source_kind="synthetic"))
        for i in range(4)
    ]
    assert evs[1].gesture == GestureType.PINCH
    assert evs[2].gesture == GestureType.PINCH  # Bridged single ambiguous frame without dropping!
    assert evs[3].gesture == GestureType.PINCH

    # 4) One-Euro filter suppresses jitter when still while tracking fast jumps closely
    flt = OneEuroFilter(min_cutoff=1.2, beta=0.2)
    jitter_outputs = [flt.filter(0.50 + (0.015 if i % 2 == 0 else -0.015), 1.0 + i * 0.033) for i in range(15)]
    jitter_spread = max(jitter_outputs[-6:]) - min(jitter_outputs[-6:])
    assert jitter_spread < 0.012  # Heavily damped compared to 0.030 input oscillation!

    fast_out = flt.filter(0.85, 1.55)
    assert fast_out > 0.65  # Responds rapidly to fast movement


def test_pc_peace_and_wave_drive_unified_love_emotion(tmp_path: Path) -> None:
    from windows.hand_detection import HandDetector

    class _PoseDetector(HandDetector):
        def __init__(self) -> None:
            self.pose = "peace"
            self.cx = 0.5

        def detect(self, frame: CameraFrame):
            return analyze_21_landmarks(_make_21_skeleton(self.pose, cx=self.cx), confidence=0.95)

    cfg = load_avatar_config()
    bounds = DesktopBounds([ScreenRect(0, 0, 1280, 720, is_primary=True)])
    engine = PetEngine(cfg, bounds, pet_width=128, pet_height=128, state_file=tmp_path / "state.json")
    det = _PoseDetector()
    rec = GestureRecognizer(detector=det, hold_frames=1)
    gate = HandoffGestureConfirmationController(recognizer=rec)
    ctrl = GesturePetController(engine, rec, gate)
    dummy = np.zeros((120, 160, 3), dtype=np.uint8)

    # PEACE -> joyful jump + LOVE emotion!
    det.pose = "peace"
    upd = ctrl.process_frame(CameraFrame(dummy, timestamp=10.0, frame_index=1, source_kind="synthetic"))
    assert upd.event.gesture == GestureType.PEACE
    assert engine.emotion == "love"
    assert engine.animation == "jump"

    # Let pet land & calm back to neutral
    for _ in range(260):
        engine.tick(1.0 / 60.0)
    assert engine.emotion == "neutral"

    # Fast sideways OPEN PALM wave -> pets with LOVE emotion!
    det.pose = "open_palm"
    for idx, x_pos in enumerate((0.30, 0.45, 0.62, 0.78)):
        det.cx = x_pos
        ctrl.process_frame(CameraFrame(dummy, timestamp=20.0 + idx * 0.05, frame_index=10 + idx, source_kind="synthetic"))
    assert engine.emotion == "love"



