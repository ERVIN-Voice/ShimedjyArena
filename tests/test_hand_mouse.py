"""Unit tests for HandMouse PC Control (anti-misclick gesture mouse engine)."""

from __future__ import annotations

from pathlib import Path

from build_exe_handmouse.generate_icon import generate_icon
from hand_mouse.cursor_engine import (
    HandMouseConfig,
    HandMouseController,
    PointerMode,
    extract_aim_point,
    map_norm_to_screen,
)
from hand_mouse.main import parse_args
from hand_mouse.win_input import ScreenBounds, VirtualPointerBackend
from windows.hand_detection import GestureEvent, GestureType, HandDetectionResult, Point2D


def _make_21_landmarks(
    anchor_x: float,
    anchor_y: float,
    *,
    tip_dx: float = 0.0,
    tip_dy: float = -0.14,
) -> tuple[Point2D, ...]:
    """Synthesize 21 normalized hand landmarks with controllable index fingertip shift."""
    pts = [Point2D(anchor_x, anchor_y) for _ in range(21)]
    pts[0] = Point2D(anchor_x, anchor_y + 0.08)   # wrist
    pts[5] = Point2D(anchor_x - 0.02, anchor_y)   # index MCP
    pts[9] = Point2D(anchor_x + 0.01, anchor_y)   # middle MCP
    pts[8] = Point2D(anchor_x + tip_dx, anchor_y + tip_dy)  # index tip
    return tuple(pts)


def _hand_event(
    gesture: GestureType,
    x: float,
    y: float,
    *,
    t: float,
    pinch_ratio: float = 0.85,
    tip_dx: float = 0.0,
    tip_dy: float = -0.14,
) -> GestureEvent:
    lm = _make_21_landmarks(x, y, tip_dx=tip_dx, tip_dy=tip_dy)
    hand = HandDetectionResult(
        detected=True,
        confidence=0.95,
        palm_center=Point2D(x, y),
        pinch_ratio=pinch_ratio,
        tracker_name="mediapipe",
        landmarks_21=lm,
    )
    return GestureEvent(
        gesture=gesture,
        confidence=0.95,
        timestamp=t,
        hand=hand,
        smoothed_palm=Point2D(x, y),
    )


def test_anchor_aim_point_ignores_fingertip_shift_during_pinch() -> None:
    lm_open = _make_21_landmarks(0.50, 0.50, tip_dx=0.0, tip_dy=-0.15)
    lm_pinching = _make_21_landmarks(0.50, 0.50, tip_dx=-0.06, tip_dy=-0.03)

    h_open = HandDetectionResult(detected=True, confidence=0.95, palm_center=Point2D(0.5, 0.5), landmarks_21=lm_open, tracker_name="mediapipe")
    h_pinch = HandDetectionResult(detected=True, confidence=0.95, palm_center=Point2D(0.5, 0.5), landmarks_21=lm_pinching, tracker_name="mediapipe")

    # Classic fingertip shifts drastically when pinching...
    tip_open = extract_aim_point(h_open, aim_source="index_tip")
    tip_pinch = extract_aim_point(h_pinch, aim_source="index_tip")
    assert tip_open is not None and tip_pinch is not None
    assert tip_open.distance_to(tip_pinch) > 0.10

    # ...whereas knuckle/palm anchor stays rock-steady (zero pinch drift!)
    anc_open = extract_aim_point(h_open, aim_source="anchor")
    anc_pinch = extract_aim_point(h_pinch, aim_source="anchor")
    assert anc_open is not None and anc_pinch is not None
    assert anc_open.distance_to(anc_pinch) < 1e-6


def test_active_zone_mapping_reaches_full_screen_edges() -> None:
    bounds = ScreenBounds(0, 0, 1920, 1080)
    cfg = HandMouseConfig(mirror_x=False, active_zone_x0=0.15, active_zone_x1=0.85, active_zone_y0=0.15, active_zone_y1=0.85)

    lx, ly = map_norm_to_screen(Point2D(0.15, 0.15), bounds, cfg)
    rx, ry = map_norm_to_screen(Point2D(0.85, 0.85), bounds, cfg)
    assert lx == 0.0 and ly == 0.0
    assert rx == 1919.0 and ry == 1079.0


def test_pre_pinch_freeze_lock_and_cancel_prevents_misclicks() -> None:
    ptr = VirtualPointerBackend(ScreenBounds(0, 0, 1920, 1080))
    cfg = HandMouseConfig(
        mirror_x=False,
        left_click_confirm_sec=0.12,
        freeze_on_pre_pinch=True,
        pre_pinch_freeze_ratio=0.56,
        yield_to_physical_mouse=False,
    )
    ctrl = HandMouseController(ptr, cfg)

    # 1. Aim cleanly at target
    s_aim = ctrl.update(_hand_event(GestureType.POINT, 0.50, 0.50, t=1.0, pinch_ratio=0.90), now=1.0)
    assert s_aim.mode == PointerMode.AIM
    aim_x, aim_y = s_aim.target_x, s_aim.target_y

    # 2. Fingers begin closing -> PRE_CLICK_LOCK freezes target even if hand jitters slightly!
    s_pre = ctrl.update(_hand_event(GestureType.POINT, 0.512, 0.508, t=1.03, pinch_ratio=0.47), now=1.03)
    assert s_pre.mode == PointerMode.PRE_CLICK_LOCK
    assert s_pre.target_locked is True
    assert (s_pre.target_x, s_pre.target_y) == (aim_x, aim_y)

    # 3. Brief pinch released BEFORE left_click_confirm_sec -> ZERO clicks fired!
    s_short = ctrl.update(_hand_event(GestureType.PINCH, 0.515, 0.510, t=1.05, pinch_ratio=0.30), now=1.05)
    assert s_short.mode == PointerMode.LEFT_HOLD
    assert (s_short.target_x, s_short.target_y) == (aim_x, aim_y)
    ctrl.update(_hand_event(GestureType.POINT, 0.50, 0.50, t=1.10, pinch_ratio=0.88), now=1.10)
    assert not any(ev[0] == "down" for ev in ptr.events)

    # 4. Full confirmed pinch fires LMB at exact frozen pixel!
    ctrl.update(_hand_event(GestureType.PINCH, 0.50, 0.50, t=1.20, pinch_ratio=0.30), now=1.20)
    s_fire = ctrl.update(_hand_event(GestureType.PINCH, 0.508, 0.506, t=1.35, pinch_ratio=0.28), now=1.35)
    assert s_fire.last_click is not None
    assert s_fire.last_click.kind == "left"
    assert (s_fire.last_click.x, s_fire.last_click.y) == (aim_x, aim_y)
    assert ("down", ("left", aim_x, aim_y)) in ptr.events
    assert ("up", ("left", aim_x, aim_y)) in ptr.events


def test_double_click_magnetic_snap_and_drag_and_drop() -> None:
    ptr = VirtualPointerBackend(ScreenBounds(0, 0, 1920, 1080))
    cfg = HandMouseConfig(
        mirror_x=False,
        left_click_confirm_sec=0.08,
        double_click_window_sec=0.60,
        double_click_snap_px=45.0,
        drag_hold_sec=0.30,
        yield_to_physical_mouse=False,
    )
    ctrl = HandMouseController(ptr, cfg)

    # First click
    s1 = ctrl.update(_hand_event(GestureType.POINT, 0.50, 0.50, t=1.0), now=1.0)
    ctrl.update(_hand_event(GestureType.PINCH, 0.50, 0.50, t=1.02, pinch_ratio=0.28), now=1.02)
    s_c1 = ctrl.update(_hand_event(GestureType.PINCH, 0.50, 0.50, t=1.12, pinch_ratio=0.28), now=1.12)
    assert s_c1.last_click is not None and s_c1.last_click.kind == "left"
    first_pt = (s1.target_x, s1.target_y)

    # Release and drift slightly (~15 px), then second pinch snaps magnetically to first_pt!
    ctrl.update(_hand_event(GestureType.POINT, 0.507, 0.504, t=1.20), now=1.20)
    ctrl.update(_hand_event(GestureType.PINCH, 0.507, 0.504, t=1.25, pinch_ratio=0.28), now=1.25)
    s_c2 = ctrl.update(_hand_event(GestureType.PINCH, 0.507, 0.504, t=1.36, pinch_ratio=0.28), now=1.36)
    assert s_c2.last_click is not None and s_c2.last_click.kind == "double"
    assert (s_c2.last_click.x, s_c2.last_click.y) == first_pt

    # Keep holding pinch past drag_hold_sec and sweep right -> Drag & Drop!
    s_drag = ctrl.update(_hand_event(GestureType.PINCH, 0.65, 0.50, t=1.70, pinch_ratio=0.28), now=1.70)
    assert s_drag.mode == PointerMode.DRAG
    assert "left" in ptr.pressed
    assert s_drag.target_x > first_pt[0]

    # Release pinch -> mouse_up('left')
    ctrl.update(_hand_event(GestureType.POINT, 0.65, 0.50, t=1.80, pinch_ratio=0.85), now=1.80)
    assert "left" not in ptr.pressed


def test_right_click_scroll_clutch_and_physical_mouse_yield() -> None:
    ptr = VirtualPointerBackend(ScreenBounds(0, 0, 1920, 1080))
    cfg = HandMouseConfig(
        mirror_x=False,
        right_click_confirm_sec=0.25,
        palm_scroll_enabled=True,
        yield_to_physical_mouse=True,
        physical_mouse_override_px=25.0,
        physical_mouse_yield_sec=1.0,
    )
    ctrl = HandMouseController(ptr, cfg)

    # Aim
    s_aim = ctrl.update(_hand_event(GestureType.POINT, 0.45, 0.45, t=1.0), now=1.0)
    aim_pt = (s_aim.target_x, s_aim.target_y)

    # PEACE held -> Right Click at frozen target
    ctrl.update(_hand_event(GestureType.PEACE, 0.45, 0.45, t=1.05), now=1.05)
    s_rmb = ctrl.update(_hand_event(GestureType.PEACE, 0.46, 0.46, t=1.35), now=1.35)
    assert s_rmb.mode == PointerMode.RIGHT_HOLD
    assert s_rmb.last_click is not None and s_rmb.last_click.kind == "right"
    assert (s_rmb.last_click.x, s_rmb.last_click.y) == aim_pt

    # Open palm moving up -> scroll positive
    ctrl.update(_hand_event(GestureType.RECEIVE_PET, 0.50, 0.60, t=1.50), now=1.50)
    s_scr = ctrl.update(_hand_event(GestureType.RECEIVE_PET, 0.50, 0.45, t=1.55), now=1.55)
    assert s_scr.mode == PointerMode.SCROLL
    assert any(ev[0] == "scroll" and ev[1] > 0 for ev in ptr.events)

    # FIST -> Clutch/Pause (cursor freezes while hand moves across frame)
    pos_before_fist = (ptr.cursor_x, ptr.cursor_y)
    s_fist = ctrl.update(_hand_event(GestureType.FIST, 0.80, 0.80, t=1.65), now=1.65)
    assert s_fist.mode == PointerMode.CLUTCH
    assert (ptr.cursor_x, ptr.cursor_y) == pos_before_fist

    # User grabs physical mouse and moves 150 px away -> controller politely yields
    ptr.cursor_x += 150
    s_yield = ctrl.update(_hand_event(GestureType.POINT, 0.25, 0.25, t=1.75), now=1.75)
    assert s_yield.mode == PointerMode.PAUSED
    assert s_yield.yielded_to_mouse is True


def test_cli_parse_args_and_icon_generation(tmp_path: Path) -> None:
    ns = parse_args(["--camera", "ps3_eye", "--camera-index", "2", "--paused", "--tray", "--self-test"])
    assert ns.camera == "ps3_eye"
    assert ns.camera_index == 2
    assert ns.paused is True
    assert ns.tray is True
    assert ns.self_test is True

    ico = generate_icon(tmp_path / "test_reticle.ico")
    assert ico.exists() and ico.stat().st_size > 1000


def test_threaded_camera_reader_end_to_end_with_hand_mouse() -> None:
    import time
    from windows.camera import ThreadedCameraReader, create_camera
    from windows.hand_detection import GestureRecognizer, OpenCVHandDetector

    cam = create_camera("synthetic")
    reader = ThreadedCameraReader(cam, max_poll_hz=60.0)
    assert reader.start() is True
    try:
        frame = None
        for _ in range(40):
            frame, _ = reader.get_latest(0)
            if frame is not None:
                break
            time.sleep(0.01)
        assert frame is not None
        rec = GestureRecognizer(detector=OpenCVHandDetector(), hold_frames=1)
        ctrl = HandMouseController(VirtualPointerBackend(ScreenBounds(0, 0, 1920, 1080)))
        ev = rec.process_frame(frame)
        st = ctrl.update(ev, now=1.0)
        assert st.target_x >= 0 and st.target_y >= 0
    finally:
        reader.stop()
