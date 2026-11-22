"""Real-time gesture control bridge mapping camera hand gestures to PetEngine actions.

Supported live interactions (unified with Android Shimeji Hand Cam):
- PINCH (thumb + index together): Grab and drag the pet smoothly across the desktop.
- POINT (raised index finger): Call the pet so it walks/runs excitedly toward your hand's X position.
- PEACE (index + middle V-sign): Joyful celebration jump with LOVE hearts!
- OPEN PALM (RECEIVE_PET):
    * When an incoming handoff offer is armed -> confirms acceptance (confirmation-only!).
    * Otherwise -> wakes sleeping pet, waves sideways to pet with LOVE ♥, or jumps on upward flick.
- FIST (sustained): Puts the pet to sleep.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from windows.camera import CameraFrame
from windows.hand_detection import (
    GestureEvent,
    GestureRecognizer,
    GestureType,
    HandoffGestureConfirmationController,
    Point2D,
)
from windows.pet_engine import PetEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GestureControlUpdate:
    """Result of processing one camera frame through GesturePetController."""

    event: GestureEvent
    active_action: str
    mapped_desktop_pos: tuple[float, float] | None
    confirmed_handoff_id: str | None = None
    state_changed: bool = False


class GesturePetController:
    """Drives PetEngine in real time from 2D RGB hand tracking events."""

    def __init__(
        self,
        engine: PetEngine,
        recognizer: GestureRecognizer,
        handoff_confirm: HandoffGestureConfirmationController,
        *,
        edge_padding: float = 0.22,
        edge_padding_y: float = 0.18,
        release_grace_frames: int = 6,
        fist_sleep_seconds: float = 0.70,
        jump_flick_vy_threshold: float = -0.80,
        wave_pet_vx_threshold: float = 0.50,
        jump_cooldown_seconds: float = 0.80,
        wave_cooldown_seconds: float = 0.90,
        peace_cooldown_seconds: float = 1.40,
        on_pet_state_changed: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.recognizer = recognizer
        self.handoff_confirm = handoff_confirm
        self._edge_padding_x = min(0.35, max(0.0, float(edge_padding)))
        self._edge_padding_y = min(0.35, max(0.0, float(edge_padding_y)))
        self._release_grace_frames = max(1, int(release_grace_frames))
        self._fist_sleep_seconds = max(0.1, float(fist_sleep_seconds))
        self._jump_flick_vy_threshold = float(jump_flick_vy_threshold)
        self._wave_pet_vx_threshold = float(wave_pet_vx_threshold)
        self._jump_cooldown_seconds = float(jump_cooldown_seconds)
        self._wave_cooldown_seconds = float(wave_cooldown_seconds)
        self._peace_cooldown_seconds = float(peace_cooldown_seconds)
        self._on_pet_state_changed = on_pet_state_changed

        self._paused: bool = False
        self._gesture_dragging: bool = False
        self._pinch_miss_frames: int = 0
        self._fist_started_at: float | None = None
        self._last_jump_at: float = 0.0
        self._last_wave_at: float = 0.0
        self._last_peace_at: float = 0.0
        self._active_action: str = ""
        self._last_event: GestureEvent | None = None

    @property
    def paused(self) -> bool:
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        self._paused = bool(value)
        if self._paused and self._gesture_dragging:
            self._release_gesture_drag()
        if self._paused:
            self._active_action = "Gesture Control Paused"

    @property
    def is_gesture_dragging(self) -> bool:
        return self._gesture_dragging

    @property
    def active_action(self) -> str:
        return self._active_action

    @property
    def last_event(self) -> GestureEvent | None:
        return self._last_event

    @property
    def control_zone_norm(self) -> tuple[float, float, float, float]:
        """Normalized (x, y, w, h) region of the camera frame mapped to 100% of the desktop.

        Optimized for 640x480 webcams so the user can comfortably reach every outer screen edge
        without their hand leaving the camera field of view.
        """
        roi = self.recognizer.detector.roi_norm
        if roi is not None:
            rx, ry, rw, rh = roi
            inner_pad_x = rw * 0.14
            inner_pad_y = rh * 0.14
            return (
                rx + inner_pad_x,
                ry + inner_pad_y,
                max(0.10, rw - 2.0 * inner_pad_x),
                max(0.10, rh - 2.0 * inner_pad_y),
            )
        px = self._edge_padding_x
        py = self._edge_padding_y
        return (px, py, max(0.10, 1.0 - 2.0 * px), max(0.10, 1.0 - 2.0 * py))

    def map_hand_to_desktop(self, palm: Point2D) -> tuple[float, float]:
        """Map normalized hand coordinates inside the active camera zone onto 100% of the desktop."""
        zx, zy, zw, zh = self.control_zone_norm
        nx = min(1.0, max(0.0, (palm.x - zx) / max(0.08, zw)))
        ny = min(1.0, max(0.0, (palm.y - zy) / max(0.08, zh)))

        bounds = self.engine.bounds
        v_left = bounds.virtual_left
        v_top = bounds.virtual_top
        v_width = max(1, bounds.virtual_right - bounds.virtual_left)
        v_height = max(1, bounds.virtual_bottom - bounds.virtual_top)

        usable_w = max(0, v_width - self.engine.pet_width)
        usable_h = max(0, v_height - self.engine.pet_height)
        tx = float(v_left) + nx * usable_w
        ty = float(v_top) + ny * usable_h
        tx, ty, _ = bounds.clamp_pet_position(tx, ty, self.engine.pet_width, self.engine.pet_height)
        return tx, ty

    def _release_gesture_drag(self) -> bool:
        if not self._gesture_dragging:
            return False
        self._gesture_dragging = False
        self._pinch_miss_frames = 0
        self.engine.end_drag()
        return True

    def process_frame(self, frame: CameraFrame) -> GestureControlUpdate:
        """Analyze one camera frame and apply real-time gestures to the desktop pet."""
        event = self.recognizer.process_frame(frame)
        self._last_event = event

        palm = event.smoothed_palm or event.hand.palm_center
        mapped_pos = self.map_hand_to_desktop(palm) if palm is not None else None

        # 1) Incoming handoff confirmation ALWAYS takes top priority (even if pet isn't currently owned)
        if self.handoff_confirm.pending_handoff_id is not None:
            confirmed = self.handoff_confirm.process_event(event)
            if confirmed is not None:
                self._active_action = "Received Pet via Open Palm!"
                return GestureControlUpdate(
                    event=event,
                    active_action=self._active_action,
                    mapped_desktop_pos=mapped_pos,
                    confirmed_handoff_id=confirmed,
                    state_changed=True,
                )
            self._active_action = "ARMED: Show Open Palm to Receive Pet"
            return GestureControlUpdate(
                event=event,
                active_action=self._active_action,
                mapped_desktop_pos=mapped_pos,
            )

        if self._paused:
            self._active_action = "Gesture Control Paused"
            return GestureControlUpdate(
                event=event,
                active_action=self._active_action,
                mapped_desktop_pos=mapped_pos,
            )

        if not self.engine.is_owner or not self.engine.is_visible or self.engine.state == "HANDOFF_PENDING":
            if self._gesture_dragging:
                self._gesture_dragging = False
                self._pinch_miss_frames = 0
            self._active_action = "Pet on another device"
            return GestureControlUpdate(
                event=event,
                active_action=self._active_action,
                mapped_desktop_pos=mapped_pos,
            )

        state_changed = False
        now = event.timestamp or time.time()

        # 2) PINCH -> Grab & Drag pet smoothly with hand
        if event.gesture == GestureType.PINCH and mapped_pos is not None:
            self._fist_started_at = None
            self._pinch_miss_frames = 0
            tx, ty = mapped_pos
            if not self._gesture_dragging:
                self._gesture_dragging = True
                self.engine.start_drag(tx, ty)
                state_changed = True
                logger.debug("Gesture PINCH grabbed pet at (%.0f, %.0f)", tx, ty)
            else:
                self.engine.update_drag(tx, ty)
            self._active_action = f"PINCH: Grabbing Pet ({int(tx)}, {int(ty)})"
            if state_changed and self._on_pet_state_changed is not None:
                self._on_pet_state_changed()
            return GestureControlUpdate(
                event=event,
                active_action=self._active_action,
                mapped_desktop_pos=mapped_pos,
                state_changed=state_changed,
            )

        # Handle pinch release grace period to avoid accidental drops on 1-frame camera flicker
        if self._gesture_dragging:
            if event.gesture in (GestureType.RECEIVE_PET, GestureType.FIST, GestureType.POINT, GestureType.PEACE):
                # Deliberate different gesture -> release immediately
                state_changed = self._release_gesture_drag()
                self._active_action = "Released Pet"
            else:
                self._pinch_miss_frames += 1
                if mapped_pos is not None and self._pinch_miss_frames < self._release_grace_frames:
                    self.engine.update_drag(*mapped_pos)
                    self._active_action = "PINCH: Holding Pet"
                    return GestureControlUpdate(
                        event=event,
                        active_action=self._active_action,
                        mapped_desktop_pos=mapped_pos,
                    )
                if self._pinch_miss_frames >= self._release_grace_frames:
                    state_changed = self._release_gesture_drag()
                    self._active_action = "Released Pet"

        # 3) PEACE (V-sign) -> Joyful jump with LOVE hearts!
        if event.gesture == GestureType.PEACE:
            self._fist_started_at = None
            if (now - self._last_peace_at) >= self._peace_cooldown_seconds:
                self.engine.celebrate_peace()
                self._last_peace_at = now
                state_changed = True
                self._active_action = "PEACE: Yay! Love & Jump!"
            else:
                self._active_action = "PEACE: Celebrating!"

        # 4) POINT -> Call pet to walk/run toward hand X coordinate
        elif event.gesture == GestureType.POINT and mapped_pos is not None:
            self._fist_started_at = None
            target_center_x = mapped_pos[0] + self.engine.pet_width / 2.0
            self.engine.guide_toward_x(target_center_x)
            state_changed = True
            self._active_action = f"POINT: Calling Pet -> X={int(target_center_x)}"

        # 5) OPEN PALM (not armed for handoff) -> Wake pet, Jump on upward flick, or Pet with LOVE on wave
        elif event.gesture == GestureType.RECEIVE_PET:
            self._fist_started_at = None
            if self.engine.state == "SLEEPING":
                self.engine.wake_up()
                state_changed = True
                self._active_action = "OPEN PALM: Woke Up Pet!"
            elif (
                event.palm_vy <= self._jump_flick_vy_threshold
                and (now - self._last_jump_at) >= self._jump_cooldown_seconds
            ):
                self.engine.trigger_jump()
                self._last_jump_at = now
                state_changed = True
                self._active_action = "FLICK UP: Pet Jump!"
            elif (
                abs(event.palm_vx) >= self._wave_pet_vx_threshold
                and (now - self._last_wave_at) >= self._wave_cooldown_seconds
            ):
                self.engine.pet_with_love()
                self._last_wave_at = now
                state_changed = True
                self._active_action = "WAVE PALM: Petted with LOVE!"
            else:
                self._active_action = "OPEN PALM: Ready (wave=love, flick up=jump)"

        # 5) FIST (sustained) -> Put pet to sleep
        elif event.gesture == GestureType.FIST:
            if self._fist_started_at is None:
                self._fist_started_at = now
            held = now - self._fist_started_at
            if held >= self._fist_sleep_seconds and self.engine.state != "SLEEPING":
                self.engine.trigger_sleep()
                state_changed = True
                self._active_action = "FIST: Pet Sleeping"
            elif self.engine.state == "SLEEPING":
                self._active_action = "FIST: Pet Sleeping"
            else:
                pct = int(min(100, (held / self._fist_sleep_seconds) * 100))
                self._active_action = f"FIST: Hold to Sleep ({pct}%)"

        else:
            self._fist_started_at = None
            if not state_changed:
                self._active_action = (
                    "Hand detected — Pinch=Grab | Point=Call | Palm=Wake | Fist=Sleep"
                    if event.hand.detected
                    else "No hand in view"
                )

        if state_changed and self._on_pet_state_changed is not None:
            self._on_pet_state_changed()

        return GestureControlUpdate(
            event=event,
            active_action=self._active_action,
            mapped_desktop_pos=mapped_pos,
            state_changed=state_changed,
        )
