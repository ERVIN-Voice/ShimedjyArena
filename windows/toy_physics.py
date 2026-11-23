"""Pure Qt-free desktop toy physics & mini-game controllers for GCS Shimeji.

Powers 4 interactive desktop games + gentle head-petting detection:
1. Fetch Ball (`FetchBallState`): bouncy desktop pixel ball you can grab & throw;
   the pet chases it, catches it, tosses it playfully, and scores points!
2. Snack Drop (`SnackDropState`): tasty pixel cookie / donut / strawberry / popcorn
   drops onto the desktop; the pet runs over and munches it happily!
3. Balloon Pop (`BalloonPopGame`): floating colorful pixel balloons rise across the
   screen; click them OR watch the pet jump up to pop them for a combo score!
4. Laser Chase (`LaserChaseTracker`): pet chases the user's cursor like a kitten
   and celebrates each time it catches the red pixel dot!
5. Head-Petting Stroke Detector (`PettingStrokeDetector`): notices back-and-forth
   mouse strokes across the pet's head without clicking -> purrs & hearts!
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from windows.pet_engine import DesktopBounds, ScreenRect


@dataclass
class FetchBallState:
    """Physics state for the interactive bouncy desktop ball."""

    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    radius: float = 18.0
    angle: float = 0.0
    active: bool = True
    being_dragged: bool = False
    catch_cooldown: float = 0.0
    catches: int = 0
    _prev_drag_x: float = 0.0
    _prev_drag_y: float = 0.0
    _prev_drag_t: float = 0.0

    @property
    def center_x(self) -> float:
        return self.x + self.radius

    @property
    def center_y(self) -> float:
        return self.y + self.radius

    def start_drag(self, x: float, y: float, now: float) -> None:
        self.being_dragged = True
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self._prev_drag_x = x
        self._prev_drag_y = y
        self._prev_drag_t = now

    def update_drag(self, x: float, y: float, now: float) -> None:
        if not self.being_dragged:
            return
        dt = max(0.008, now - self._prev_drag_t)
        raw_vx = (x - self._prev_drag_x) / dt
        raw_vy = (y - self._prev_drag_y) / dt
        # Exponentially smooth throw velocity so releases feel crisp and fun
        self.vx = 0.45 * self.vx + 0.55 * max(-1400.0, min(1400.0, raw_vx))
        self.vy = 0.45 * self.vy + 0.55 * max(-1400.0, min(1400.0, raw_vy))
        self.x = x
        self.y = y
        self._prev_drag_x = x
        self._prev_drag_y = y
        self._prev_drag_t = now

    def release_drag(self) -> None:
        self.being_dragged = False
        self.catch_cooldown = 0.25

    def kick_by_pet(self, rng: random.Random) -> None:
        """Pet joyfully bats/bounces the ball back into the air!"""
        self.catches += 1
        self.catch_cooldown = 1.15
        self.vx = rng.choice((-1.0, 1.0)) * rng.uniform(220.0, 440.0)
        self.vy = -rng.uniform(420.0, 620.0)

    def tick(self, dt: float, bounds: DesktopBounds) -> None:
        if not self.active:
            return
        if self.catch_cooldown > 0.0:
            self.catch_cooldown = max(0.0, self.catch_cooldown - dt)
        if self.being_dragged or dt <= 0.0:
            return

        screen = bounds.screen_for_point(self.center_x, self.center_y)
        diam = self.radius * 2.0

        self.vy += 1250.0 * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.angle = (self.angle + (self.vx * dt) / max(8.0, self.radius)) % (math.tau)

        # Bounce off screen walls & floor
        if self.x < screen.left:
            self.x = float(screen.left)
            self.vx = abs(self.vx) * 0.78
        elif self.x + diam > screen.right:
            self.x = float(screen.right - diam)
            self.vx = -abs(self.vx) * 0.78

        if self.y < screen.top:
            self.y = float(screen.top)
            self.vy = abs(self.vy) * 0.72
        elif self.y + diam >= screen.bottom:
            self.y = float(screen.bottom - diam)
            if abs(self.vy) > 90.0:
                self.vy = -abs(self.vy) * 0.68
            else:
                self.vy = 0.0
            self.vx *= max(0.0, 1.0 - 2.4 * dt)
            if abs(self.vx) < 8.0:
                self.vx = 0.0

    def is_caught_by_pet(self, pet_cx: float, pet_cy: float, pet_w: int, pet_h: int) -> bool:
        if not self.active or self.being_dragged or self.catch_cooldown > 0.0:
            return False
        dist = math.hypot(self.center_x - pet_cx, self.center_y - pet_cy)
        return dist <= (max(pet_w, pet_h) * 0.42 + self.radius)


SNACK_KINDS: tuple[str, ...] = ("cookie", "donut", "strawberry", "popcorn")


@dataclass
class SnackDropState:
    """Physics for a delicious pixel treat dropped onto the desktop."""

    x: float
    y: float
    kind: str = "cookie"
    vy: float = 0.0
    size: int = 36
    active: bool = True

    @property
    def center_x(self) -> float:
        return self.x + self.size / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.size / 2.0

    def tick(self, dt: float, bounds: DesktopBounds) -> None:
        if not self.active or dt <= 0.0:
            return
        screen = bounds.screen_for_point(self.center_x, self.center_y)
        self.vy += 980.0 * dt
        self.y += self.vy * dt
        floor_y = float(screen.bottom - self.size)
        if self.y >= floor_y:
            self.y = floor_y
            if abs(self.vy) > 120.0:
                self.vy = -abs(self.vy) * 0.35
            else:
                self.vy = 0.0

    def is_eaten_by_pet(self, pet_cx: float, pet_cy: float, pet_w: int, pet_h: int) -> bool:
        if not self.active:
            return False
        return math.hypot(self.center_x - pet_cx, self.center_y - pet_cy) <= (max(pet_w, pet_h) * 0.45 + self.size * 0.4)


@dataclass
class FloatingBalloon:
    """A single rising colorful pixel balloon."""

    balloon_id: int
    x: float
    y: float
    vy: float
    sway_phase: float
    color_hex: str
    radius: float = 22.0
    popped: bool = False

    @property
    def center_x(self) -> float:
        return self.x + self.radius

    @property
    def center_y(self) -> float:
        return self.y + self.radius


_BALLOON_COLORS: tuple[str, ...] = (
    "#ff5c94",  # Pink
    "#ffd43b",  # Gold
    "#38bdf8",  # Sky
    "#34eb8a",  # Mint
    "#c084fc",  # Violet
    "#fb923c",  # Orange
)


@dataclass
class BalloonPopGame:
    """25-second desktop balloon pop mini-game (click balloons or let the pet jump & pop!)."""

    duration_sec: float = 25.0
    remaining_sec: float = 0.0
    active: bool = False
    score: int = 0
    best_score: int = 0
    balloons: list[FloatingBalloon] = field(default_factory=list)
    _next_id: int = 1
    _spawn_accum: float = 0.0
    _rng: random.Random = field(default_factory=random.Random)

    def start(self, screen: ScreenRect) -> None:
        self.active = True
        self.remaining_sec = self.duration_sec
        self.score = 0
        self.balloons.clear()
        self._spawn_accum = 0.0
        # Spawn 3 starter balloons right away so action is immediate
        for _ in range(3):
            self._spawn_balloon(screen, y_override=screen.bottom - self._rng.uniform(140.0, 260.0))

    def stop(self) -> None:
        self.active = False
        self.balloons.clear()
        self.best_score = max(self.best_score, self.score)

    def _spawn_balloon(self, screen: ScreenRect, *, y_override: float | None = None) -> None:
        margin = 60.0
        bx = self._rng.uniform(screen.left + margin, max(screen.left + margin + 10, screen.right - margin - 44))
        by = y_override if y_override is not None else float(screen.bottom - 30)
        b = FloatingBalloon(
            balloon_id=self._next_id,
            x=bx,
            y=by,
            vy=-self._rng.uniform(70.0, 125.0),
            sway_phase=self._rng.uniform(0.0, math.tau),
            color_hex=self._rng.choice(_BALLOON_COLORS),
        )
        self._next_id += 1
        self.balloons.append(b)

    def pop_balloon(self, balloon_id: int) -> FloatingBalloon | None:
        for b in self.balloons:
            if b.balloon_id == balloon_id and not b.popped:
                b.popped = True
                self.score += 1
                self.best_score = max(self.best_score, self.score)
                return b
        return None

    def tick(
        self,
        dt: float,
        screen: ScreenRect,
        pet_cx: float,
        pet_cy: float,
        pet_w: int,
        pet_h: int,
    ) -> tuple[list[FloatingBalloon], bool]:
        """Advance balloons; returns `(newly_pet_popped_balloons, game_just_finished)`."""
        if not self.active or dt <= 0.0:
            return [], False

        self.remaining_sec -= dt
        if self.remaining_sec <= 0.0:
            self.remaining_sec = 0.0
            self.stop()
            return [], True

        self._spawn_accum += dt
        if self._spawn_accum >= 1.15 and len(self.balloons) < 7:
            self._spawn_accum = 0.0
            self._spawn_balloon(screen)

        pet_popped: list[FloatingBalloon] = []
        survivors: list[FloatingBalloon] = []
        hit_r = max(pet_w, pet_h) * 0.48
        for b in self.balloons:
            if b.popped:
                continue
            b.sway_phase += dt * 2.4
            b.x += math.sin(b.sway_phase) * 32.0 * dt
            b.y += b.vy * dt
            # Clamp horizontally
            b.x = max(float(screen.left + 12), min(float(screen.right - b.radius * 2 - 12), b.x))
            if b.y + b.radius * 2 < screen.top - 10:
                continue
            if math.hypot(b.center_x - pet_cx, b.center_y - pet_cy) <= (hit_r + b.radius):
                b.popped = True
                self.score += 1
                self.best_score = max(self.best_score, self.score)
                pet_popped.append(b)
                continue
            survivors.append(b)

        self.balloons = survivors
        return pet_popped, False

    def nearest_reachable_balloon(self, pet_cx: float, floor_y: float) -> FloatingBalloon | None:
        """Pick the lowest/closest active balloon so the pet can chase and jump for it."""
        candidates = [b for b in self.balloons if not b.popped and b.y >= floor_y - 380.0]
        if not candidates:
            return None
        return min(candidates, key=lambda b: abs(b.center_x - pet_cx) + max(0.0, (floor_y - b.center_y) * 0.4))


class PettingStrokeDetector:
    """Detects gentle back-and-forth mouse stroking across the pet's head (without clicking!)."""

    def __init__(self, *, reversals_required: int = 3, window_sec: float = 1.6, cooldown_sec: float = 2.8) -> None:
        self._reversals_required = max(2, int(reversals_required))
        self._window_sec = max(0.4, float(window_sec))
        self._cooldown_sec = max(0.5, float(cooldown_sec))
        self._last_x: float | None = None
        self._last_dir: int = 0
        self._reversal_times: list[float] = []
        self._last_trigger_t: float = -999.0

    def reset(self) -> None:
        self._last_x = None
        self._last_dir = 0
        self._reversal_times.clear()

    def feed(self, local_x: float, local_y: float, pet_h: int, *, now: float) -> bool:
        """Feed hover mouse coords inside the pet window; returns True when a petting purr triggers."""
        # Petting works best on the top 65% of the pet (head & ears!)
        if local_y > pet_h * 0.68:
            return False

        if self._last_x is None:
            self._last_x = local_x
            return False

        dx = local_x - self._last_x
        if abs(dx) < 5.0:
            return False

        cur_dir = 1 if dx > 0 else -1
        self._last_x = local_x
        if self._last_dir != 0 and cur_dir != self._last_dir:
            self._reversal_times.append(now)
        self._last_dir = cur_dir

        cutoff = now - self._window_sec
        self._reversal_times = [t for t in self._reversal_times if t >= cutoff]

        if len(self._reversal_times) >= self._reversals_required and (now - self._last_trigger_t) >= self._cooldown_sec:
            self._last_trigger_t = now
            self._reversal_times.clear()
            return True
        return False
