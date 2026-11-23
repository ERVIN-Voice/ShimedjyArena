"""Anti-misclick PC gesture cursor & click state machine.

Designed specifically to eliminate the #1 cause of misclicks in webcam hand-mouse tools:
1. Stable Aim Anchor (MediaPipe palm + index MCP #5 + middle MCP #9) — does NOT jump
   when thumb & index finger curl together for a pinch click! (User can also choose
   classic index fingertip #8 mode if preferred.)
2. Adaptive One-Euro Filter + micro-deadzone — rock-steady on tiny close/minimize
   buttons, instant response on large screen sweeps.
3. Pre-Pinch Freeze Lock + Visual Confirmation Ring:
   - The moment thumb & index begin converging (pinch_ratio < pre_pinch_freeze_ratio)
     or a click gesture starts, the target pixel `(locked_x, locked_y)` FREEZES cleanly
     on screen while the targeting reticle shrinks & fills a confirmation arc.
   - If you realize you're slightly off-target, simply release before the arc finishes
     (zero accidental clicks!).
   - Holding pinch past the click confirmation and moving past `drag_start_px` smoothly
     upgrades to Drag & Drop (mouse_down -> move -> mouse_up on release).
   - Two pinches within `double_click_window_sec` magnetically snap the 2nd click onto
     the 1st click pixel for effortless double-clicking.
4. Gesture Map:
   - Point (1 finger) / Open hand (when palm-scroll disabled) -> Aim & move cursor
   - Pinch (thumb + index) -> Freeze target + Left Click / Double Click / Hold+Move = Drag
   - Peace (2 fingers, V-sign) -> Freeze target + hold confirmation -> Right Click
   - Open Palm (4-5 fingers, when palm-scroll enabled) -> Smooth vertical scrolling
   - Fist -> Clutch / Pause (cursor freezes so you can reposition your hand comfortably)
5. Physical Mouse Yield: if you grab your real mouse and move it > 24 px away from the
   hand cursor, gesture movement politely yields for ~1.2 s instead of fighting you.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from hand_mouse.win_input import OSPointerBackend, ScreenBounds
from windows.hand_detection import (
    GestureEvent,
    GestureType,
    HandDetectionResult,
    OneEuroFilter,
    Point2D,
)


class PointerMode(str, Enum):
    """Visual + behavioral state reported to the on-screen targeting reticle."""

    IDLE = "idle"                      # No hand detected
    PAUSED = "paused"                  # User paused or yielded to physical mouse
    CLUTCH = "clutch"                  # Fist held -> hand repositioning, cursor locked
    AIM = "aim"                        # Moving cursor smoothly
    PRE_CLICK_LOCK = "pre_click_lock"  # Fingers converging -> target pixel frozen!
    LEFT_HOLD = "left_hold"            # Pinch held, filling LMB confirm arc
    RIGHT_HOLD = "right_hold"          # Peace held, filling RMB confirm arc
    DRAG = "drag"                      # Left mouse button held & dragging
    SCROLL = "scroll"                  # Open palm scrolling vertically


@dataclass
class HandMouseConfig:
    """User-tunable settings for HandMouse PC Control."""

    enabled: bool = True
    mirror_x: bool = True
    # Comfortable active camera zone mapped to 100% of screen edges (reaches corners easily)
    active_zone_x0: float = 0.14
    active_zone_x1: float = 0.86
    active_zone_y0: float = 0.14
    active_zone_y1: float = 0.84

    # Aim point source: "anchor" (palm+knuckles, immune to pinch shift) or "index_tip"
    aim_source: Literal["anchor", "index_tip"] = "anchor"

    # Smoothing & anti-jitter
    filter_min_cutoff: float = 1.15
    filter_beta: float = 0.018
    deadzone_px: float = 4.5

    # Anti-misclick freeze & confirmation
    freeze_on_pre_pinch: bool = True
    pre_pinch_freeze_ratio: float = 0.56   # Freeze target before pinch threshold (0.40) triggers
    left_click_confirm_sec: float = 0.11   # Short visual confirmation ring before LMB fires
    right_click_confirm_sec: float = 0.28  # Deliberate hold for RMB (PEACE) -> zero accidental RMB
    right_click_cooldown_sec: float = 0.65

    # Double-click magnetic snap & Drag-and-Drop
    double_click_window_sec: float = 0.55
    double_click_snap_px: float = 42.0
    drag_hold_sec: float = 0.34            # Hold pinch >= 0.34s OR move > drag_start_px -> drag
    drag_start_px: float = 28.0

    # Open-palm scrolling
    palm_scroll_enabled: bool = True
    scroll_deadzone_norm: float = 0.022
    scroll_sensitivity: float = 24.0       # Wheel notches per normalized screen height moved

    # Yield automatically if user moves their physical mouse
    yield_to_physical_mouse: bool = True
    physical_mouse_override_px: float = 26.0
    physical_mouse_yield_sec: float = 1.25

    # Hand loss grace period (prevents dropping drag on 1-2 dropped camera frames)
    hand_loss_grace_sec: float = 0.25

    def active_zone_rect(self) -> tuple[float, float, float, float]:
        x0 = max(0.02, min(0.42, float(self.active_zone_x0)))
        x1 = max(x0 + 0.18, min(0.98, float(self.active_zone_x1)))
        y0 = max(0.02, min(0.42, float(self.active_zone_y0)))
        y1 = max(y0 + 0.18, min(0.98, float(self.active_zone_y1)))
        return (x0, y0, x1 - x0, y1 - y0)


@dataclass(frozen=True)
class ClickFlash:
    """Short-lived visual confirmation pulse rendered at the exact clicked pixel."""

    kind: Literal["left", "right", "double"]
    x: int
    y: int
    timestamp: float


@dataclass(frozen=True)
class HandMouseFrameState:
    """Snapshot produced every camera frame for the on-screen click-target overlay and UI."""

    mode: PointerMode
    target_x: int
    target_y: int
    raw_screen_x: int
    raw_screen_y: int
    target_locked: bool
    confirm_progress: float            # 0.0..1.0 ring fill while holding click gesture
    pending_action: str                # Human-readable label shown on reticle ("ЛКМ", "ПКМ", "ТЯНУ", ...)
    gesture: GestureType
    hand_detected: bool
    scroll_direction: float = 0.0      # >0 up, <0 down, 0 idle
    last_click: ClickFlash | None = None
    yielded_to_mouse: bool = False
    pinch_ratio: float = 1.0


def extract_aim_point(
    hand: HandDetectionResult,
    *,
    aim_source: Literal["anchor", "index_tip"] = "anchor",
    fallback_palm: Point2D | None = None,
) -> Point2D | None:
    """Compute normalized `[0..1]` aim point from a 21-landmark or OpenCV hand result.

    In `anchor` mode with 21 MediaPipe landmarks, uses a rock-solid knuckle/palm triangle
    (45% index MCP #5 + 35% middle MCP #9 + 20% wrist #0), which stays virtually stationary
    when the thumb and index finger pinch together — eliminating pinch-induced cursor drift!
    """
    if not hand.detected:
        return None

    lm = hand.landmarks_21
    if len(lm) >= 21:
        if aim_source == "index_tip":
            return lm[8]
        # Stable knuckle anchor: index MCP (5), middle MCP (9), wrist (0)
        idx_mcp = lm[5]
        mid_mcp = lm[9]
        wrist = lm[0]
        return Point2D(
            x=0.45 * idx_mcp.x + 0.35 * mid_mcp.x + 0.20 * wrist.x,
            y=0.45 * idx_mcp.y + 0.35 * mid_mcp.y + 0.20 * wrist.y,
        )

    if aim_source == "index_tip" and hand.finger_tips:
        # Highest fingertip in OpenCV contour fallback
        return min(hand.finger_tips, key=lambda p: p.y)

    return fallback_palm or hand.palm_center


def map_norm_to_screen(
    pt: Point2D,
    bounds: ScreenBounds,
    config: HandMouseConfig,
) -> tuple[float, float]:
    """Map normalized camera coordinate inside the active zone to physical screen coordinates."""
    zx, zy, zw, zh = config.active_zone_rect()
    nx = (1.0 - pt.x) if config.mirror_x else pt.x
    ny = pt.y

    u = (nx - zx) / max(1e-4, zw)
    v = (ny - zy) / max(1e-4, zh)
    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))

    sx = bounds.left + u * max(1, bounds.width - 1)
    sy = bounds.top + v * max(1, bounds.height - 1)
    return sx, sy


class HandMouseController:
    """Stateful anti-misclick PC gesture mouse controller."""

    def __init__(
        self,
        pointer: OSPointerBackend,
        config: HandMouseConfig | None = None,
    ) -> None:
        self.pointer = pointer
        self.config = config or HandMouseConfig()

        bounds = self.pointer.get_screen_bounds()
        cx, cy = self.pointer.get_cursor_pos()
        cx, cy = bounds.clamp(cx, cy)

        self._fx = OneEuroFilter(
            min_cutoff=self.config.filter_min_cutoff,
            beta=self.config.filter_beta,
            d_cutoff=1.0,
        )
        self._fy = OneEuroFilter(
            min_cutoff=self.config.filter_min_cutoff,
            beta=self.config.filter_beta,
            d_cutoff=1.0,
        )

        self._target_x: int = cx
        self._target_y: int = cy
        self._last_set_pos: tuple[int, int] | None = None
        self._yield_until: float = 0.0

        # Click freeze & confirmation state
        self._locked_pos: tuple[int, int] | None = None
        self._pinch_active: bool = False
        self._pinch_start_t: float = 0.0
        self._pinch_click_fired: bool = False
        self._dragging: bool = False

        # Double-click magnetic memory
        self._last_left_click_t: float = 0.0
        self._last_left_click_pos: tuple[int, int] = (cx, cy)

        # Right-click hold state
        self._right_hold_start_t: float | None = None
        self._right_click_fired: bool = False
        self._last_right_click_t: float = 0.0

        # Scroll anchor
        self._scroll_last_y: float | None = None
        self._scroll_accum: float = 0.0

        # Hand-loss grace & click pulse feedback
        self._last_hand_seen_t: float = 0.0
        self._last_click_flash: ClickFlash | None = None
        self._last_mode: PointerMode = PointerMode.IDLE

    def apply_filter_settings(self) -> None:
        """Recreate 1-Euro filters after live slider updates without teleporting cursor."""
        self._fx = OneEuroFilter(
            min_cutoff=self.config.filter_min_cutoff,
            beta=self.config.filter_beta,
            d_cutoff=1.0,
        )
        self._fy = OneEuroFilter(
            min_cutoff=self.config.filter_min_cutoff,
            beta=self.config.filter_beta,
            d_cutoff=1.0,
        )

    def release_all(self) -> None:
        """Safety release of any held mouse button (called on pause, exit, or hand loss)."""
        if self._dragging:
            self.pointer.mouse_up("left")
            self._dragging = False
        self._pinch_active = False
        self._pinch_click_fired = False
        self._locked_pos = None
        self._right_hold_start_t = None
        self._right_click_fired = False
        self._scroll_last_y = None
        self._scroll_accum = 0.0

    def reset(self) -> None:
        self.release_all()
        self._fx.reset()
        self._fy.reset()
        self._last_set_pos = None
        self._yield_until = 0.0
        self._last_mode = PointerMode.IDLE

    def _move_pointer(self, x: int, y: int) -> None:
        self._target_x = x
        self._target_y = y
        self.pointer.set_cursor_pos(x, y)
        self._last_set_pos = (x, y)

    def _check_physical_mouse_override(self, now: float) -> bool:
        if not self.config.yield_to_physical_mouse:
            return False
        if now < self._yield_until:
            cx, cy = self.pointer.get_cursor_pos()
            self._target_x, self._target_y = cx, cy
            self._last_set_pos = (cx, cy)
            return True
        if self._last_set_pos is not None and not self._dragging:
            actual_x, actual_y = self.pointer.get_cursor_pos()
            dist = math.hypot(actual_x - self._last_set_pos[0], actual_y - self._last_set_pos[1])
            if dist >= self.config.physical_mouse_override_px:
                self.release_all()
                self._yield_until = now + self.config.physical_mouse_yield_sec
                self._target_x, self._target_y = actual_x, actual_y
                self._last_set_pos = (actual_x, actual_y)
                return True
        return False

    def _finish_pinch(self, now: float) -> None:
        if self._dragging:
            self.pointer.mouse_up("left")
            self._dragging = False
        self._pinch_active = False
        self._pinch_click_fired = False
        self._locked_pos = None

    def update(self, event: GestureEvent, *, now: float | None = None) -> HandMouseFrameState:
        """Process one camera gesture event and update OS cursor / clicks / targeting state."""
        t = time.monotonic() if now is None else float(now)
        bounds = self.pointer.get_screen_bounds()

        if not self.config.enabled:
            self.release_all()
            cx, cy = self.pointer.get_cursor_pos()
            self._target_x, self._target_y = cx, cy
            return HandMouseFrameState(
                mode=PointerMode.PAUSED,
                target_x=cx,
                target_y=cy,
                raw_screen_x=cx,
                raw_screen_y=cy,
                target_locked=False,
                confirm_progress=0.0,
                pending_action="ПАУЗА",
                gesture=event.gesture,
                hand_detected=event.hand.detected,
                last_click=self._last_click_flash,
                pinch_ratio=event.hand.pinch_ratio,
            )

        if self._check_physical_mouse_override(t):
            return HandMouseFrameState(
                mode=PointerMode.PAUSED,
                target_x=self._target_x,
                target_y=self._target_y,
                raw_screen_x=self._target_x,
                raw_screen_y=self._target_y,
                target_locked=False,
                confirm_progress=0.0,
                pending_action="МЫШЬ",
                gesture=event.gesture,
                hand_detected=event.hand.detected,
                last_click=self._last_click_flash,
                yielded_to_mouse=True,
                pinch_ratio=event.hand.pinch_ratio,
            )

        hand = event.hand
        aim_pt = extract_aim_point(
            hand,
            aim_source=self.config.aim_source,
            fallback_palm=event.smoothed_palm,
        )

        # Handle hand disappearance with short grace period so drags don't drop on 1 bad frame
        if not hand.detected or aim_pt is None:
            if t - self._last_hand_seen_t > self.config.hand_loss_grace_sec:
                self.release_all()
                self._fx.reset()
                self._fy.reset()
                self._last_mode = PointerMode.IDLE
            return HandMouseFrameState(
                mode=PointerMode.DRAG if self._dragging else PointerMode.IDLE,
                target_x=self._target_x,
                target_y=self._target_y,
                raw_screen_x=self._target_x,
                raw_screen_y=self._target_y,
                target_locked=self._locked_pos is not None,
                confirm_progress=0.0,
                pending_action="ТЯНУ" if self._dragging else "",
                gesture=GestureType.NONE,
                hand_detected=False,
                last_click=self._last_click_flash,
                pinch_ratio=1.0,
            )

        self._last_hand_seen_t = t

        # Map aim point to screen
        raw_fx, raw_fy = map_norm_to_screen(aim_pt, bounds, self.config)
        raw_ix, raw_iy = bounds.clamp(raw_fx, raw_fy)

        gesture = event.gesture

        # ------------------------------------------------------------------
        # 1) FIST -> CLUTCH / PAUSE (reposition hand comfortably without moving cursor)
        # ------------------------------------------------------------------
        if gesture == GestureType.FIST:
            self._finish_pinch(t)
            self._right_hold_start_t = None
            self._right_click_fired = False
            self._scroll_last_y = None
            self._scroll_accum = 0.0
            self._last_mode = PointerMode.CLUTCH
            return HandMouseFrameState(
                mode=PointerMode.CLUTCH,
                target_x=self._target_x,
                target_y=self._target_y,
                raw_screen_x=raw_ix,
                raw_screen_y=raw_iy,
                target_locked=True,
                confirm_progress=0.0,
                pending_action="ПАУЗА",
                gesture=gesture,
                hand_detected=True,
                last_click=self._last_click_flash,
                pinch_ratio=hand.pinch_ratio,
            )

        # ------------------------------------------------------------------
        # 2) PINCH -> Freeze target + LMB confirm / Double Click / Drag & Drop
        # ------------------------------------------------------------------
        if gesture == GestureType.PINCH:
            self._right_hold_start_t = None
            self._right_click_fired = False
            self._scroll_last_y = None
            self._scroll_accum = 0.0

            if not self._pinch_active:
                self._pinch_active = True
                self._pinch_start_t = t
                self._pinch_click_fired = False
                # Keep pre-pinch locked position if we already froze on finger convergence!
                if self._locked_pos is None:
                    self._locked_pos = (self._target_x, self._target_y)
                # Magnetic snap for effortless double-click on the exact same pixel
                if (t - self._last_left_click_t) <= self.config.double_click_window_sec:
                    snap_dist = math.hypot(
                        self._locked_pos[0] - self._last_left_click_pos[0],
                        self._locked_pos[1] - self._last_left_click_pos[1],
                    )
                    if snap_dist <= self.config.double_click_snap_px:
                        self._locked_pos = self._last_left_click_pos
                self._move_pointer(self._locked_pos[0], self._locked_pos[1])

            held_sec = max(0.0, t - self._pinch_start_t)
            lock_x, lock_y = self._locked_pos or (self._target_x, self._target_y)
            drag_dist = math.hypot(raw_ix - lock_x, raw_iy - lock_y)

            # Upgrade to Drag & Drop if held past drag_hold_sec OR moved past drag_start_px
            if not self._dragging and (
                (self._pinch_click_fired and held_sec >= self.config.drag_hold_sec)
                or (held_sec >= max(0.14, self.config.left_click_confirm_sec) and drag_dist >= self.config.drag_start_px)
            ):
                if not self._pinch_click_fired:
                    # Enter drag directly with mouse_down at locked point (no double trigger)
                    self._move_pointer(lock_x, lock_y)
                    self.pointer.mouse_down("left")
                    self._pinch_click_fired = True
                else:
                    # Click already fired; start holding down at locked point for drag
                    self._move_pointer(lock_x, lock_y)
                    self.pointer.mouse_down("left")
                self._dragging = True
                self._locked_pos = None

            if self._dragging:
                filt_x = self._fx.filter(raw_fx, t)
                filt_y = self._fy.filter(raw_fy, t)
                cand_x, cand_y = bounds.clamp(filt_x, filt_y)
                if math.hypot(cand_x - self._target_x, cand_y - self._target_y) >= max(2.0, self.config.deadzone_px * 0.6):
                    self._move_pointer(cand_x, cand_y)
                self._last_mode = PointerMode.DRAG
                return HandMouseFrameState(
                    mode=PointerMode.DRAG,
                    target_x=self._target_x,
                    target_y=self._target_y,
                    raw_screen_x=raw_ix,
                    raw_screen_y=raw_iy,
                    target_locked=False,
                    confirm_progress=1.0,
                    pending_action="ТЯНУ",
                    gesture=gesture,
                    hand_detected=True,
                    last_click=self._last_click_flash,
                    pinch_ratio=hand.pinch_ratio,
                )

            # Still in frozen anti-misclick confirmation window!
            confirm_sec = max(0.0, self.config.left_click_confirm_sec)
            progress = 1.0 if confirm_sec <= 1e-4 else min(1.0, held_sec / confirm_sec)

            if not self._pinch_click_fired and held_sec >= confirm_sec:
                is_double = (
                    (t - self._last_left_click_t) <= self.config.double_click_window_sec
                    and math.hypot(lock_x - self._last_left_click_pos[0], lock_y - self._last_left_click_pos[1])
                    <= self.config.double_click_snap_px
                )
                self._move_pointer(lock_x, lock_y)
                self.pointer.click("left", x=lock_x, y=lock_y)
                self._pinch_click_fired = True
                self._last_left_click_t = t
                self._last_left_click_pos = (lock_x, lock_y)
                self._last_click_flash = ClickFlash(
                    kind="double" if is_double else "left",
                    x=lock_x,
                    y=lock_y,
                    timestamp=t,
                )

            self._last_mode = PointerMode.LEFT_HOLD
            return HandMouseFrameState(
                mode=PointerMode.LEFT_HOLD,
                target_x=lock_x,
                target_y=lock_y,
                raw_screen_x=raw_ix,
                raw_screen_y=raw_iy,
                target_locked=True,
                confirm_progress=progress,
                pending_action="ЛКМ ✓" if self._pinch_click_fired else "ЛКМ",
                gesture=gesture,
                hand_detected=True,
                last_click=self._last_click_flash,
                pinch_ratio=hand.pinch_ratio,
            )

        # Pinch released
        if self._pinch_active:
            self._finish_pinch(t)

        # ------------------------------------------------------------------
        # 3) PEACE (2 fingers) -> Freeze target + deliberate Right Click
        # ------------------------------------------------------------------
        if gesture == GestureType.PEACE:
            self._scroll_last_y = None
            self._scroll_accum = 0.0

            if self._right_hold_start_t is None:
                self._right_hold_start_t = t
                self._right_click_fired = False
                self._locked_pos = (self._target_x, self._target_y)

            lock_x, lock_y = self._locked_pos or (self._target_x, self._target_y)
            held_sec = max(0.0, t - self._right_hold_start_t)
            req_sec = max(0.05, self.config.right_click_confirm_sec)
            progress = min(1.0, held_sec / req_sec)

            if (
                not self._right_click_fired
                and held_sec >= req_sec
                and (t - self._last_right_click_t) >= self.config.right_click_cooldown_sec
            ):
                self._move_pointer(lock_x, lock_y)
                self.pointer.click("right", x=lock_x, y=lock_y)
                self._right_click_fired = True
                self._last_right_click_t = t
                self._last_click_flash = ClickFlash(kind="right", x=lock_x, y=lock_y, timestamp=t)

            self._last_mode = PointerMode.RIGHT_HOLD
            return HandMouseFrameState(
                mode=PointerMode.RIGHT_HOLD,
                target_x=lock_x,
                target_y=lock_y,
                raw_screen_x=raw_ix,
                raw_screen_y=raw_iy,
                target_locked=True,
                confirm_progress=progress,
                pending_action="ПКМ ✓" if self._right_click_fired else "ПКМ",
                gesture=gesture,
                hand_detected=True,
                last_click=self._last_click_flash,
                pinch_ratio=hand.pinch_ratio,
            )

        self._right_hold_start_t = None
        self._right_click_fired = False

        # ------------------------------------------------------------------
        # 4) OPEN PALM (RECEIVE_PET) -> Smooth vertical scrolling (optional)
        # ------------------------------------------------------------------
        if gesture == GestureType.RECEIVE_PET and self.config.palm_scroll_enabled:
            self._locked_pos = None
            palm_y = aim_pt.y
            scroll_dir = 0.0
            if self._scroll_last_y is None:
                self._scroll_last_y = palm_y
                self._scroll_accum = 0.0
            else:
                dy = self._scroll_last_y - palm_y  # Move palm UP -> scroll UP (+delta)
                if abs(dy) >= self.config.scroll_deadzone_norm:
                    step = dy * self.config.scroll_sensitivity
                    self._scroll_accum += step
                    self._scroll_last_y = palm_y
                    if abs(self._scroll_accum) >= 0.45:
                        notches = round(self._scroll_accum, 2)
                        self.pointer.scroll(notches, horizontal=False)
                        scroll_dir = 1.0 if notches > 0 else -1.0
                        self._scroll_accum = 0.0
                    else:
                        scroll_dir = 1.0 if dy > 0 else -1.0
            self._last_mode = PointerMode.SCROLL
            return HandMouseFrameState(
                mode=PointerMode.SCROLL,
                target_x=self._target_x,
                target_y=self._target_y,
                raw_screen_x=raw_ix,
                raw_screen_y=raw_iy,
                target_locked=True,
                confirm_progress=0.0,
                pending_action="СКРОЛЛ ▲" if scroll_dir > 0 else ("СКРОЛЛ ▼" if scroll_dir < 0 else "СКРОЛЛ ⇅"),
                gesture=gesture,
                hand_detected=True,
                scroll_direction=scroll_dir,
                last_click=self._last_click_flash,
                pinch_ratio=hand.pinch_ratio,
            )

        self._scroll_last_y = None
        self._scroll_accum = 0.0

        # ------------------------------------------------------------------
        # 5) PRE-PINCH FREEZE LOCK (fingers starting to close -> freeze target pixel!)
        # ------------------------------------------------------------------
        if (
            self.config.freeze_on_pre_pinch
            and hand.tracker_name == "mediapipe"
            and 0.0 < hand.pinch_ratio < self.config.pre_pinch_freeze_ratio
        ):
            if self._locked_pos is None:
                self._locked_pos = (self._target_x, self._target_y)
            lock_x, lock_y = self._locked_pos
            # If user deliberately sweeps hand far away (>36 px), break pre-lock so aiming never feels stuck
            if math.hypot(raw_ix - lock_x, raw_iy - lock_y) <= max(38.0, self.config.drag_start_px * 1.4):
                closeness = max(
                    0.0,
                    min(
                        1.0,
                        (self.config.pre_pinch_freeze_ratio - hand.pinch_ratio)
                        / max(0.05, self.config.pre_pinch_freeze_ratio - 0.38),
                    ),
                )
                self._last_mode = PointerMode.PRE_CLICK_LOCK
                return HandMouseFrameState(
                    mode=PointerMode.PRE_CLICK_LOCK,
                    target_x=lock_x,
                    target_y=lock_y,
                    raw_screen_x=raw_ix,
                    raw_screen_y=raw_iy,
                    target_locked=True,
                    confirm_progress=closeness * 0.45,
                    pending_action="ПРИЦЕЛ",
                    gesture=gesture,
                    hand_detected=True,
                    last_click=self._last_click_flash,
                    pinch_ratio=hand.pinch_ratio,
                )
            self._locked_pos = None

        # ------------------------------------------------------------------
        # 6) NORMAL AIMING (POINT / open hand / neutral hand)
        # ------------------------------------------------------------------
        self._locked_pos = None
        filt_x = self._fx.filter(raw_fx, t)
        filt_y = self._fy.filter(raw_fy, t)
        cand_x, cand_y = bounds.clamp(filt_x, filt_y)
        move_dist = math.hypot(cand_x - self._target_x, cand_y - self._target_y)
        if move_dist >= self.config.deadzone_px or self._last_mode in (PointerMode.IDLE, PointerMode.PAUSED):
            self._move_pointer(cand_x, cand_y)

        self._last_mode = PointerMode.AIM
        return HandMouseFrameState(
            mode=PointerMode.AIM,
            target_x=self._target_x,
            target_y=self._target_y,
            raw_screen_x=raw_ix,
            raw_screen_y=raw_iy,
            target_locked=False,
            confirm_progress=0.0,
            pending_action="",
            gesture=gesture,
            hand_detected=True,
            last_click=self._last_click_flash,
            pinch_ratio=hand.pinch_ratio,
        )
