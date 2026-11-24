"""Cozy non-punishing Tamagotchi-style companion profile & friendship progression."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from shared.config_loader import get_user_data_dir

logger = logging.getLogger(__name__)

_FRIENDSHIP_TITLES: tuple[tuple[int, str], ...] = (
    (1, "Знакомый чубрик"),
    (3, "Приятель экрана"),
    (6, "Верный напарник"),
    (10, "Лучший друг ♥"),
    (15, "Душа рабочего стола"),
    (25, "Легенда монитора ★"),
)


@dataclass
class PetProfile:
    """Persistent stats & friendship progression for GCS Shimeji (never dies or punishes)."""

    mood: float = 85.0          # 0..100
    hunger: float = 85.0        # 0..100 (100 = full & happy)
    energy: float = 90.0        # 0..100
    xp: int = 0
    balls_caught: int = 0
    snacks_eaten: int = 0
    balloons_popped: int = 0
    pets_given: int = 0
    sites_watched: int = 0

    @property
    def level(self) -> int:
        return 1 + max(0, self.xp) // 100

    @property
    def xp_into_level(self) -> int:
        return max(0, self.xp) % 100

    @property
    def title(self) -> str:
        lvl = self.level
        chosen = _FRIENDSHIP_TITLES[0][1]
        for min_lvl, label in _FRIENDSHIP_TITLES:
            if lvl >= min_lvl:
                chosen = label
        return chosen

    def _clamp(self) -> None:
        self.mood = max(10.0, min(100.0, self.mood))
        self.hunger = max(10.0, min(100.0, self.hunger))
        self.energy = max(10.0, min(100.0, self.energy))

    def tick(self, dt: float, *, sleeping: bool = False) -> None:
        """Gentle stat drift over time (very slow, cozy pace)."""
        if dt <= 0.0:
            return
        if sleeping:
            self.energy += dt * 1.4
            self.mood += dt * 0.08
        else:
            self.hunger -= dt * 0.035
            self.energy -= dt * 0.025
            if self.hunger < 30.0:
                self.mood -= dt * 0.03
        self._clamp()

    def record_petting(self) -> bool:
        """Record head-petting; returns True if leveled up."""
        old_lvl = self.level
        self.pets_given += 1
        self.mood += 10.0
        self.xp += 12
        self._clamp()
        return self.level > old_lvl

    def record_snack(self) -> bool:
        old_lvl = self.level
        self.snacks_eaten += 1
        self.hunger += 28.0
        self.mood += 12.0
        self.energy += 8.0
        self.xp += 18
        self._clamp()
        return self.level > old_lvl

    def record_ball_catch(self) -> bool:
        old_lvl = self.level
        self.balls_caught += 1
        self.mood += 14.0
        self.energy -= 3.0
        self.xp += 22
        self._clamp()
        return self.level > old_lvl

    def record_balloon_pop(self) -> bool:
        old_lvl = self.level
        self.balloons_popped += 1
        self.mood += 6.0
        self.xp += 10
        self._clamp()
        return self.level > old_lvl

    def record_site_watched(self) -> bool:
        old_lvl = self.level
        self.sites_watched += 1
        self.mood += 3.0
        self.xp += 5
        self._clamp()
        return self.level > old_lvl

    def save(self, path: Path | None = None) -> None:
        target = path or (get_user_data_dir() / "pet_profile.json")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.debug("Could not save pet profile: %s", exc)

    @classmethod
    def load(cls, path: Path | None = None) -> PetProfile:
        target = path or (get_user_data_dir() / "pet_profile.json")
        if not target.exists():
            return cls()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return cls()
            return cls(
                mood=float(data.get("mood", 85.0)),
                hunger=float(data.get("hunger", 85.0)),
                energy=float(data.get("energy", 90.0)),
                xp=int(data.get("xp", 0)),
                balls_caught=int(data.get("balls_caught", 0)),
                snacks_eaten=int(data.get("snacks_eaten", 0)),
                balloons_popped=int(data.get("balloons_popped", 0)),
                pets_given=int(data.get("pets_given", 0)),
                sites_watched=int(data.get("sites_watched", 0)),
            )
        except Exception as exc:
            logger.debug("Could not load pet profile: %s", exc)
            return cls()
