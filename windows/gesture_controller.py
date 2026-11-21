"""Real-time gesture control bridge mapping camera hand gestures to PetEngine actions.

Supported live interactions:
- PINCH (thumb + index together): Grab and drag the pet smoothly across the desktop.
- POINT (raised index finger): Call the pet so it walks/runs toward your hand's X position.
- OPEN PALM (RECEIVE_PET):
    * When an incoming handoff offer is armed -> confirms acceptance (confirmation-only!).
    * Otherwise -> wakes sleeping pet, or makes pet jump on an upward hand flick.
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
        edge_padding: float = 0.10,
        release_grace_frames: int = 4,
        fist_sleep_seconds: float = 0.70,
        jump_flick_vy_threshold: float = -0.85,
        jump_cooldown_seconds: float = 0.80,
        on_pet_state_changed: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.recognizer = recognizer
        self.handoff_confirm = handoff_confirm
        self._edge_padding = min(0.30, max(0.0, float(edge_padding)))
        self._release_grace_frames = max(1, int(release_grace_frames))
        self._fist_sleep_seconds = max(0.1, float(fist_sleep_seconds))
        self._jump_flick_vy_threshold = float(jump_flick_vy_threshold)
        self._jump_cooldown_seconds = float(jump_cooldown_seconds)
        self._on_pet_state_changed = on_pet_state_changed

        self._paused: bool = False
        self._gesture_dragging: bool = False
        self._pinch_miss_frames: int = 0
        self._fist_started_at: float | None = None
        self._last_jump_at: float = 0.0
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

    def map_hand_to_desktop(self, palm: Point2D) -> tuple[float, float]:
        """Map normalized hand coordinates onto the primary monitor with comfortable edge margins."""
        pad = self._edge_padding
        span = max(0.1, 1.0 - 2.0 * pad)
        nx = min(1.0, max(0.0, (palm.x - pad) / span))
        ny = min(1.0, max(0.0, (palm.y - pad) / span))

        screen = self.engine.bounds.primary_screen
        usable_w = max(0, screen.width - self.engine.pet_width)
        usable_h = max(0, screen.height - self.engine.pet_height)
        tx = float(screen.left) + nx * usable_w
        ty = float(screen.top) + ny * usable_h
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
            if event.gesture in (GestureType.RECEIVE_PET, GestureType.FIST, GestureType.POINT):
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

        # 3) POINT -> Call pet to walk/run toward hand X coordinate
        if event.gesture == GestureType.POINT and mapped_pos is not None:
            self._fist_started_at = None
            target_center_x = mapped_pos[0] + self.engine.pet_width / 2.0
            self.engine.guide_toward_x(target_center_x)
            state_changed = True
            self._active_action = f"POINT: Calling Pet -> X={int(target_center_x)}"

        # 4) OPEN PALM (not armed for handoff) -> Wake pet or Jump on sharp upward flick
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
            else:
                self._active_action = "OPEN PALM: Ready (flick up to jump)"

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
