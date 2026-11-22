"""Core desktop pet physics, multi-monitor geometry, dragging, and persistence engine.

Kept strictly independent of Qt/PySide6 so every movement, multi-monitor clamping,
edge-handoff detection, and state-persistence rule is deterministic and testable.
"""

from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shared.config_loader import AvatarConfig, get_user_data_dir

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScreenRect:
    """Usable screen rectangle (excluding OS taskbar) for a single monitor."""

    x: int
    y: int
    width: int
    height: int
    name: str = "primary"
    is_primary: bool = True

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains_point(self, px: float, py: float) -> bool:
        return self.left <= px < self.right and self.top <= py < self.bottom


class DesktopBounds:
    """Multi-monitor virtual desktop geometry helper."""

    def __init__(self, screens: list[ScreenRect]) -> None:
        if not screens:
            screens = [ScreenRect(0, 0, 1920, 1080, name="default", is_primary=True)]
        self.screens = list(screens)

    @property
    def primary_screen(self) -> ScreenRect:
        for s in self.screens:
            if s.is_primary:
                return s
        return self.screens[0]

    @property
    def virtual_left(self) -> int:
        return min(s.left for s in self.screens)

    @property
    def virtual_top(self) -> int:
        return min(s.top for s in self.screens)

    @property
    def virtual_right(self) -> int:
        return max(s.right for s in self.screens)

    @property
    def virtual_bottom(self) -> int:
        return max(s.bottom for s in self.screens)

    def screen_for_point(self, px: float, py: float) -> ScreenRect:
        """Return the monitor containing (px, py), or the nearest monitor if outside."""
        for screen in self.screens:
            if screen.contains_point(px, py):
                return screen

        # Nearest monitor by Manhattan distance to its clamped rectangle
        best = self.screens[0]
        best_dist = float("inf")
        for screen in self.screens:
            cx = min(max(px, screen.left), screen.right - 1)
            cy = min(max(py, screen.top), screen.bottom - 1)
            dist = abs(px - cx) + abs(py - cy)
            if dist < best_dist:
                best_dist = dist
                best = screen
        return best

    def clamp_pet_position(self, x: float, y: float, pet_w: int, pet_h: int) -> tuple[float, float, ScreenRect]:
        """Keep the pet fully inside the monitor closest to its center."""
        cx = x + pet_w / 2.0
        cy = y + pet_h / 2.0
        screen = self.screen_for_point(cx, cy)

        max_x = max(screen.left, screen.right - pet_w)
        max_y = max(screen.top, screen.bottom - pet_h)
        clamped_x = min(max(x, float(screen.left)), float(max_x))
        clamped_y = min(max(y, float(screen.top)), float(max_y))
        return clamped_x, clamped_y, screen

    def detect_outer_edge(
        self,
        x: float,
        y: float,
        pet_w: int,
        pet_h: int,
        margin_px: int,
    ) -> tuple[str | None, float]:
        """Detect whether the pet is touching an outer boundary of the multi-monitor desktop.

        Internal seams between adjacent monitors do NOT count as outer edges so the user
        can drag freely across monitors without accidentally triggering a handoff.
        """
        cx = x + pet_w / 2.0
        cy = y + pet_h / 2.0
        screen = self.screen_for_point(cx, cy)
        norm_y = (cy - screen.top) / max(1.0, float(screen.height))
        norm_y = min(1.0, max(0.0, norm_y))

        # Check whether another monitor sits immediately beyond each side
        has_left_neighbor = any(s is not screen and s.right <= screen.left + 4 and s.top <= cy <= s.bottom for s in self.screens)
        has_right_neighbor = any(s is not screen and s.left >= screen.right - 4 and s.top <= cy <= s.bottom for s in self.screens)
        has_top_neighbor = any(s is not screen and s.bottom <= screen.top + 4 and s.left <= cx <= s.right for s in self.screens)

        if not has_left_neighbor and x <= screen.left + margin_px:
            return "left", norm_y
        if not has_right_neighbor and (x + pet_w) >= (screen.right - margin_px):
            return "right", norm_y
        if not has_top_neighbor and y <= screen.top + margin_px:
            return "top", norm_y
        return None, norm_y


@dataclass
class EdgeHandoffEvent:
    """Emitted when the user drags and holds the pet at an outer desktop edge."""

    edge: str
    normalized_y: float
    x: float
    y: float


class PetEngine:
    """Manages position, smooth motion, autonomous AI, dragging, and saved state."""

    def __init__(
        self,
        config: AvatarConfig,
        desktop_bounds: DesktopBounds,
        pet_width: int,
        pet_height: int,
        *,
        state_file: Path | None = None,
        rng: random.Random | None = None,
        on_edge_handoff: Callable[[EdgeHandoffEvent], None] | None = None,
    ) -> None:
        self.config = config
        self.bounds = desktop_bounds
        self.pet_width = pet_width
        self.pet_height = pet_height
        if state_file is not None:
            self.state_file = state_file
        else:
            raw_state = Path(config.behavior.position_state_file)
            self.state_file = raw_state if raw_state.is_absolute() else (get_user_data_dir() / raw_state)
        self._rng = rng or random.Random()
        self._on_edge_handoff = on_edge_handoff

        primary = self.bounds.primary_screen
        self.x: float = float(primary.left + max(0, (primary.width - pet_width) // 2))
        self.y: float = float(primary.bottom - pet_height)
        self.vx: float = 0.0
        self.vy: float = 0.0
        self.facing: str = "right"
        self.state: str = "ACTIVE"
        self.animation: str = "idle"
        self.emotion: str = "neutral"
        self.emotion_timer: float = 0.0
        self.effect_phase: float = 0.0
        self.is_owner: bool = True
        self.is_visible: bool = True

        # Drag state
        self._dragging: bool = False
        self._drag_target_x: float = self.x
        self._drag_target_y: float = self.y
        self._edge_dwell_edge: str | None = None
        self._edge_dwell_seconds: float = 0.0
        self._edge_handoff_fired: bool = False

        # Autonomous timers
        self._idle_elapsed: float = 0.0
        self._behavior_timer: float = self._next_behavior_interval()
        self._airborne: bool = False

    def _next_behavior_interval(self) -> float:
        beh = self.config.behavior
        return self._rng.uniform(beh.wander_min_seconds, beh.wander_max_seconds)

    def update_desktop_bounds(self, bounds: DesktopBounds) -> None:
        """Update monitor layout dynamically (e.g., monitor plugged/unplugged)."""
        self.bounds = bounds
        self.x, self.y, _ = self.bounds.clamp_pet_position(self.x, self.y, self.pet_width, self.pet_height)

    def update_pet_size(self, pet_width: int, pet_height: int) -> None:
        """Adjust size after scale change while keeping bottom edge anchored."""
        bottom = self.y + self.pet_height
        self.pet_width = pet_width
        self.pet_height = pet_height
        self.y = bottom - self.pet_height
        self.x, self.y, _ = self.bounds.clamp_pet_position(self.x, self.y, self.pet_width, self.pet_height)

    def load_saved_position(self) -> dict[str, object] | None:
        """Restore persisted position/facing/scale from disk if available."""
        if not self.state_file.exists():
            return None
        try:
            with self.state_file.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            saved_x = float(data.get("x", self.x))
            saved_y = float(data.get("y", self.y))
            clamped_x, clamped_y, _ = self.bounds.clamp_pet_position(
                saved_x, saved_y, self.pet_width, self.pet_height
            )
            self.x = clamped_x
            self.y = clamped_y
            facing = data.get("facing")
            if facing in ("left", "right"):
                self.facing = facing
            return data
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Could not load saved pet state from %s: %s", self.state_file, exc)
            return None

    def save_position(self, scale: int | None = None) -> bool:
        """Atomically persist pet position to disk."""
        payload: dict[str, object] = {
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "facing": self.facing,
        }
        if scale is not None:
            payload["scale"] = int(scale)

        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp_path, self.state_file)
            return True
        except OSError as exc:
            logger.warning("Failed to save pet state to %s: %s", self.state_file, exc)
            return False

    def set_position_immediate(self, x: float, y: float) -> None:
        """Place the pet at a clamped desktop coordinate."""
        self.x, self.y, _ = self.bounds.clamp_pet_position(x, y, self.pet_width, self.pet_height)
        self._drag_target_x = self.x
        self._drag_target_y = self.y

    def start_drag(self, target_x: float, target_y: float) -> None:
        """Enter mouse-drag mode."""
        if not self.is_owner or self.state == "HANDOFF_PENDING":
            return
        self._dragging = True
        self._airborne = False
        self.vx = 0.0
        self.vy = 0.0
        self.state = "DRAGGING"
        self.animation = "grab"
        self._idle_elapsed = 0.0
        self._edge_dwell_edge = None
        self._edge_dwell_seconds = 0.0
        self._edge_handoff_fired = False
        self.update_drag(target_x, target_y)

    def update_drag(self, target_x: float, target_y: float) -> None:
        """Update target position while the user drags the pet across monitors."""
        if not self._dragging:
            return
        # Allow crossing freely between monitors; clamp only to the screen nearest the pointer.
        cx, cy, _ = self.bounds.clamp_pet_position(target_x, target_y, self.pet_width, self.pet_height)
        dx = cx - self._drag_target_x
        if abs(dx) > 1.5:
            self.facing = "right" if dx > 0 else "left"
        self._drag_target_x = cx
        self._drag_target_y = cy

    def end_drag(self) -> None:
        """Release the pet; if airborne, let gravity bring it down to the monitor floor."""
        if not self._dragging:
            return
        self._dragging = False
        self._edge_dwell_edge = None
        self._edge_dwell_seconds = 0.0
        self._edge_handoff_fired = False

        if self.state == "HANDOFF_PENDING":
            return

        screen = self.bounds.screen_for_point(self.x + self.pet_width / 2.0, self.y + self.pet_height / 2.0)
        floor_y = float(screen.bottom - self.pet_height)
        self.state = "ACTIVE"
        if self.y < floor_y - 2.0:
            self._airborne = True
            self.vy = 0.0
            self.animation = "jump"
        else:
            self.y = floor_y
            self._airborne = False
            self.animation = "landing"
        self._behavior_timer = self._next_behavior_interval()

    def set_emotion(self, emotion: str, duration_seconds: float = 3.2) -> None:
        """Set one of the 8 unified cross-device pet emotions."""
        valid = {"neutral", "happy", "love", "excited", "scared", "dizzy", "angry", "sleepy"}
        norm = str(emotion).strip().lower()
        if norm not in valid:
            norm = "neutral"
        self.emotion = norm
        self.emotion_timer = max(0.0, float(duration_seconds)) if norm != "neutral" else 0.0

    def pet_with_love(self) -> None:
        """React to petting/waving with LOVE hearts and calm down if sleeping/angry."""
        if not self.is_owner or self.state == "HANDOFF_PENDING":
            return
        if self.state == "SLEEPING":
            self.wake_up()
        self.set_emotion("love", duration_seconds=3.6)

    def celebrate_peace(self) -> None:
        """React to PEACE gesture with a joyful jump and LOVE hearts."""
        if not self.is_owner or self._dragging or self.state == "HANDOFF_PENDING":
            return
        if self.state == "SLEEPING":
            self.wake_up()
        self.trigger_jump()
        self.set_emotion("love", duration_seconds=3.4)

    def trigger_jump(self) -> None:
        """Cause the pet to jump vertically from the floor."""
        if not self.is_owner or self._dragging or self.state == "HANDOFF_PENDING":
            return
        if not self._airborne:
            self.state = "ACTIVE"
            self._airborne = True
            self.vy = self.config.behavior.jump_velocity_px_per_sec
            self.animation = "jump"
            self._idle_elapsed = 0.0
            if self.emotion not in ("love", "angry"):
                self.set_emotion("happy", duration_seconds=2.4)

    def trigger_sleep(self) -> None:
        """Put the pet into sleep state."""
        if not self.is_owner or self._dragging or self.state == "HANDOFF_PENDING":
            return
        self.vx = 0.0
        self.state = "SLEEPING"
        self.animation = "sleep"
        self.set_emotion("sleepy", duration_seconds=8.0)

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    def guide_toward_x(self, target_x: float) -> None:
        """Steer the pet smoothly toward a desktop X coordinate (used by POINT gesture)."""
        if not self.is_owner or self._dragging or self.state == "HANDOFF_PENDING" or self._airborne:
            return
        self.state = "ACTIVE"
        self._idle_elapsed = 0.0
        pet_center_x = self.x + self.pet_width / 2.0
        delta = target_x - pet_center_x
        beh = self.config.behavior
        if abs(delta) < max(24.0, self.pet_width * 0.25):
            self.vx = 0.0
            self.animation = "idle"
            self._behavior_timer = 1.5
            return

        direction = 1.0 if delta > 0 else -1.0
        self.facing = "right" if direction > 0 else "left"
        if abs(delta) > 260.0:
            self.vx = direction * beh.run_speed_px_per_sec
            self.animation = "run"
        else:
            self.vx = direction * beh.walk_speed_px_per_sec
            self.animation = "walk"
        self.set_emotion("excited", duration_seconds=2.0)
        # Prevent autonomous AI from immediately overriding the user's hand guidance
        self._behavior_timer = 1.2

    def wake_up(self) -> None:
        """Wake the pet back into active idle state."""
        if not self.is_owner or self.state == "HANDOFF_PENDING":
            return
        self.state = "ACTIVE"
        self.animation = "idle"
        self._idle_elapsed = 0.0
        if self.emotion == "sleepy":
            self.set_emotion("happy", duration_seconds=2.0)

    def enter_handoff_pending(self) -> None:
        """Transition into outgoing handoff pending state and stop autonomous movement."""
        self._dragging = False
        self.vx = 0.0
        self.vy = 0.0
        self.state = "HANDOFF_PENDING"
        self.animation = "handoff"

    def complete_outgoing_handoff(self) -> None:
        """Hide pet once ownership has atomically moved to another device."""
        self.is_owner = False
        self.is_visible = False
        self._dragging = False
        self.vx = 0.0
        self.vy = 0.0
        self.state = "ACTIVE"

    def cancel_handoff_restore(self, *, x: float | None = None, y: float | None = None) -> None:
        """Restore pet visibility & active state if an outgoing handoff timed out or failed."""
        self.is_owner = True
        self.is_visible = True
        self._dragging = False
        self.vx = 0.0
        self.vy = 0.0
        self.state = "ACTIVE"
        self.animation = "landing"
        if x is not None and y is not None:
            self.set_position_immediate(x, y)
        # Nudge slightly away from outer edge so it does not re-trigger immediately
        screen = self.bounds.screen_for_point(self.x + self.pet_width / 2.0, self.y + self.pet_height / 2.0)
        pad = self.config.behavior.edge_handoff_margin_px + 20
        self.x = min(max(self.x, float(screen.left + pad)), float(max(screen.left + pad, screen.right - self.pet_width - pad)))
        self._behavior_timer = self._next_behavior_interval()

    def receive_from_edge(self, edge: str, normalized_y: float = 0.75) -> tuple[float, float]:
        """Materialize the pet on the primary monitor after confirming an incoming handoff."""
        screen = self.bounds.primary_screen
        norm_y = min(0.95, max(0.05, normalized_y))
        spawn_y = screen.top + norm_y * max(0, screen.height - self.pet_height)
        inset = self.config.behavior.edge_handoff_margin_px + 28

        # If source left from its right edge, arrive at our left edge (and vice versa)
        if edge == "right":
            spawn_x = float(screen.left + inset)
            self.facing = "right"
        elif edge == "left":
            spawn_x = float(screen.right - self.pet_width - inset)
            self.facing = "left"
        else:
            spawn_x = float(screen.left + (screen.width - self.pet_width) / 2.0)
            self.facing = "right"

        self.is_owner = True
        self.is_visible = True
        self._dragging = False
        self.vx = 0.0
        self.vy = 0.0
        self.state = "ACTIVE"
        self.animation = "receive"
        self.set_position_immediate(spawn_x, spawn_y)
        floor_y = float(screen.bottom - self.pet_height)
        self._airborne = self.y < floor_y - 2.0
        self._idle_elapsed = 0.0
        self._behavior_timer = self._next_behavior_interval()
        return self.x, self.y

    def tick(self, dt: float) -> None:
        """Advance physics, smooth drag interpolation, edge detection, emotions, and autonomous behavior."""
        if dt <= 0.0 or not self.is_owner or not self.is_visible:
            return
        dt = min(dt, 0.1)  # Guard against large frame stalls
        self.effect_phase += dt * 5.2
        if self.emotion_timer > 0.0:
            self.emotion_timer = max(0.0, self.emotion_timer - dt)
            if self.emotion_timer <= 0.0 and self.state != "SLEEPING":
                self.emotion = "neutral"

        beh = self.config.behavior

        if self.state == "HANDOFF_PENDING":
            return

        if self._dragging:
            # Smooth exponential follow for natural feel
            alpha = 1.0 - pow(1.0 - beh.smooth_follow_factor, dt * 60.0)
            self.x += (self._drag_target_x - self.x) * alpha
            self.y += (self._drag_target_y - self.y) * alpha

            edge, norm_y = self.bounds.detect_outer_edge(
                self.x, self.y, self.pet_width, self.pet_height, beh.edge_handoff_margin_px
            )
            if edge is not None:
                if edge == self._edge_dwell_edge:
                    self._edge_dwell_seconds += dt
                else:
                    self._edge_dwell_edge = edge
                    self._edge_dwell_seconds = dt

                if self._edge_dwell_seconds >= beh.edge_handoff_hold_seconds and not self._edge_handoff_fired:
                    self._edge_handoff_fired = True
                    if self._on_edge_handoff is not None:
                        self._on_edge_handoff(
                            EdgeHandoffEvent(edge=edge, normalized_y=norm_y, x=self.x, y=self.y)
                        )
            else:
                self._edge_dwell_edge = None
                self._edge_dwell_seconds = 0.0
                self._edge_handoff_fired = False
            return

        # Gravity when airborne
        screen = self.bounds.screen_for_point(self.x + self.pet_width / 2.0, self.y + self.pet_height / 2.0)
        floor_y = float(screen.bottom - self.pet_height)

        if self._airborne or self.y < floor_y - 1.0:
            self._airborne = True
            self.vy += beh.gravity_px_per_sec2 * dt
            self.y += self.vy * dt
            self.x += self.vx * dt
            if self.y >= floor_y:
                self.y = floor_y
                self.vy = 0.0
                self._airborne = False
                if self.animation not in ("receive",):
                    self.animation = "landing"
        else:
            self.y = floor_y
            self.vy = 0.0
            self._airborne = False

            # Horizontal movement on floor
            if self.animation in ("walk", "run"):
                self.x += self.vx * dt
                # Bounce smoothly at outer desktop edges while wandering
                if self.x <= self.bounds.virtual_left:
                    self.x = float(self.bounds.virtual_left)
                    self.vx = abs(self.vx)
                    self.facing = "right"
                elif self.x + self.pet_width >= self.bounds.virtual_right:
                    self.x = float(self.bounds.virtual_right - self.pet_width)
                    self.vx = -abs(self.vx)
                    self.facing = "left"

                # Keep aligned to the floor of whichever monitor we walked onto
                new_screen = self.bounds.screen_for_point(self.x + self.pet_width / 2.0, self.y + self.pet_height / 2.0)
                self.y = float(new_screen.bottom - self.pet_height)

            # Autonomous state transitions
            if self.state == "SLEEPING":
                self.vx = 0.0
                self.animation = "sleep"
            elif self.animation not in ("receive", "landing"):
                self._behavior_timer -= dt
                if self.animation == "idle":
                    self._idle_elapsed += dt
                    if self._idle_elapsed >= beh.idle_to_sleep_seconds:
                        self.trigger_sleep()
                        return
                else:
                    self._idle_elapsed = 0.0

                if self._behavior_timer <= 0.0:
                    self._choose_next_autonomous_action()

        self.x, self.y, _ = self.bounds.clamp_pet_position(self.x, self.y, self.pet_width, self.pet_height)

    def _choose_next_autonomous_action(self) -> None:
        beh = self.config.behavior
        self._behavior_timer = self._next_behavior_interval()
        roll = self._rng.random()
        direction = -1.0 if self._rng.random() < 0.5 else 1.0

        if roll < 0.35:
            self.vx = 0.0
            self.animation = "idle"
        elif roll < 0.72:
            self.vx = direction * beh.walk_speed_px_per_sec
            self.facing = "right" if direction > 0 else "left"
            self.animation = "walk"
        elif roll < 0.90:
            self.vx = direction * beh.run_speed_px_per_sec
            self.facing = "right" if direction > 0 else "left"
            self.animation = "run"
        else:
            self.vx = direction * (beh.walk_speed_px_per_sec * 0.6)
            self.facing = "right" if direction > 0 else "left"
            self.trigger_jump()
