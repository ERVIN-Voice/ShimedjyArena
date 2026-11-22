"""Authoritative single-source-of-truth avatar state for GCS Local Hub.

Guarantees:
- Only one device can own the avatar at any instant (`owner`).
- Non-owner devices cannot mutate position, animation, or state.
- Two-phase handoff atomically locks state (`HANDOFF_PENDING`) with a saved
  rollback snapshot so timeout/rejection/disconnect restores the exact prior state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from shared.config_loader import ProtocolConfig, load_protocol_config


class AvatarStateError(Exception):
    """Base error for invalid avatar state transitions."""


class NotOwnerError(AvatarStateError):
    """Raised when a non-owner device attempts to mutate authoritative state."""


class HandoffStateError(AvatarStateError):
    """Raised when a handoff state transition violates atomicity invariants."""


@dataclass
class Position:
    """2D avatar coordinates in the owning device's coordinate space."""

    x: float = 0.0
    y: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {"x": round(float(self.x), 2), "y": round(float(self.y), 2)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Position:
        if not raw:
            return cls(0.0, 0.0)
        return cls(x=float(raw.get("x", 0.0)), y=float(raw.get("y", 0.0)))


@dataclass
class AvatarSnapshot:
    """Immutable snapshot of the authoritative avatar state."""

    owner: str | None
    position: Position
    animation: str
    state: str
    facing: str
    handoff_id: str | None
    version: int
    updated_at: float
    emotion: str = "neutral"

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "position": self.position.to_dict(),
            "animation": self.animation,
            "state": self.state,
            "facing": self.facing,
            "emotion": self.emotion,
            "handoff_id": self.handoff_id,
            "version": self.version,
            "updated_at": self.updated_at,
        }


@dataclass
class _PreHandoffBackup:
    owner: str
    position: Position
    animation: str
    state: str
    facing: str
    emotion: str = "neutral"


class AvatarStateStore:
    """Single source of truth for the virtual companion's state."""

    def __init__(
        self,
        protocol: ProtocolConfig | None = None,
        *,
        initial_owner: str | None = None,
    ) -> None:
        self._protocol = protocol or load_protocol_config()
        self._owner: str | None = initial_owner
        self._position: Position = Position(200.0, 200.0)
        self._animation: str = "idle"
        self._state: str = "ACTIVE"
        self._facing: str = "right"
        self._emotion: str = "neutral"
        self._handoff_id: str | None = None
        self._version: int = 1
        self._updated_at: float = time.time()
        self._pre_handoff_backup: _PreHandoffBackup | None = None

    @property
    def owner(self) -> str | None:
        return self._owner

    @property
    def handoff_id(self) -> str | None:
        return self._handoff_id

    @property
    def state(self) -> str:
        return self._state

    @property
    def emotion(self) -> str:
        return self._emotion

    @property
    def version(self) -> int:
        return self._version

    def snapshot(self) -> AvatarSnapshot:
        """Return a detached copy of the current authoritative state."""
        return AvatarSnapshot(
            owner=self._owner,
            position=Position(self._position.x, self._position.y),
            animation=self._animation,
            state=self._state,
            facing=self._facing,
            handoff_id=self._handoff_id,
            version=self._version,
            updated_at=self._updated_at,
            emotion=self._emotion,
        )

    def _bump_version(self) -> None:
        self._version += 1
        self._updated_at = time.time()

    def _validate_animation(self, animation: str) -> str:
        if animation not in self._protocol.animations:
            raise AvatarStateError(
                f"Invalid animation '{animation}'. Allowed: {sorted(self._protocol.animations)}"
            )
        return animation

    def _validate_state(self, state: str) -> str:
        if state not in self._protocol.avatar_states:
            raise AvatarStateError(
                f"Invalid avatar state '{state}'. Allowed: {sorted(self._protocol.avatar_states)}"
            )
        return state

    def _validate_facing(self, facing: str) -> str:
        if facing not in self._protocol.facing_values:
            raise AvatarStateError(
                f"Invalid facing '{facing}'. Allowed: {sorted(self._protocol.facing_values)}"
            )
        return facing

    def _validate_emotion(self, emotion: str) -> str:
        normalized = str(emotion).strip().lower()
        if self._protocol.emotions and normalized not in self._protocol.emotions:
            raise AvatarStateError(
                f"Invalid emotion '{emotion}'. Allowed: {sorted(self._protocol.emotions)}"
            )
        return normalized

    def assign_owner_if_unowned(self, device_id: str) -> bool:
        """Assign initial owner only when no device currently owns the avatar."""
        if self._owner is not None:
            return False
        self._owner = device_id
        self._state = "ACTIVE"
        self._animation = "idle"
        self._emotion = "neutral"
        self._handoff_id = None
        self._bump_version()
        return True

    def update_from_owner(
        self,
        device_id: str,
        *,
        position: Position | dict[str, Any] | None = None,
        animation: str | None = None,
        state: str | None = None,
        facing: str | None = None,
        emotion: str | None = None,
    ) -> AvatarSnapshot:
        """Apply a state update strictly from the current owning device."""
        if self._owner != device_id:
            raise NotOwnerError(
                f"Device '{device_id}' cannot update avatar owned by '{self._owner}'."
            )
        if self._state == "HANDOFF_PENDING" or self._handoff_id is not None:
            raise HandoffStateError(
                f"Cannot mutate avatar state while handoff '{self._handoff_id}' is pending."
            )

        if state is not None:
            validated_state = self._validate_state(state)
            if validated_state == "HANDOFF_PENDING":
                raise HandoffStateError(
                    "HANDOFF_PENDING can only be entered through begin_handoff()."
                )
            self._state = validated_state

        if animation is not None:
            self._animation = self._validate_animation(animation)

        if facing is not None:
            self._facing = self._validate_facing(facing)

        if emotion is not None:
            self._emotion = self._validate_emotion(emotion)

        if position is not None:
            if isinstance(position, dict):
                self._position = Position.from_dict(position)
            else:
                self._position = Position(position.x, position.y)

        self._bump_version()
        return self.snapshot()

    def begin_handoff(self, handoff_id: str, expected_owner: str) -> AvatarSnapshot:
        """Phase 1: Atomically lock the avatar into HANDOFF_PENDING and save rollback state."""
        if not handoff_id:
            raise HandoffStateError("handoff_id must be non-empty.")
        if self._owner != expected_owner:
            raise NotOwnerError(
                f"Device '{expected_owner}' is not current owner '{self._owner}'."
            )
        if self._handoff_id is not None or self._state == "HANDOFF_PENDING":
            raise HandoffStateError(
                f"Another handoff ('{self._handoff_id}') is already in progress."
            )

        # Preserve pre-handoff state so cancellation restores cleanly
        restorable_state = "ACTIVE" if self._state == "DRAGGING" else self._state
        restorable_anim = "idle" if self._animation in ("grab", "handoff") else self._animation
        self._pre_handoff_backup = _PreHandoffBackup(
            owner=self._owner,
            position=Position(self._position.x, self._position.y),
            animation=restorable_anim,
            state=restorable_state,
            facing=self._facing,
            emotion=self._emotion,
        )

        self._handoff_id = handoff_id
        self._state = "HANDOFF_PENDING"
        self._animation = "handoff"
        self._bump_version()
        return self.snapshot()

    def commit_handoff(
        self,
        handoff_id: str,
        new_owner: str,
        *,
        landing_position: Position | dict[str, Any] | None = None,
    ) -> AvatarSnapshot:
        """Phase 2 (Accept): Atomically transfer ownership to new_owner and clear handoff lock."""
        if self._handoff_id != handoff_id or self._state != "HANDOFF_PENDING":
            raise HandoffStateError(
                f"Cannot commit handoff '{handoff_id}'; active handoff is '{self._handoff_id}'."
            )
        if not new_owner:
            raise HandoffStateError("new_owner must be non-empty.")

        self._owner = new_owner
        self._handoff_id = None
        self._state = "ACTIVE"
        self._animation = "receive"
        if landing_position is not None:
            if isinstance(landing_position, dict):
                self._position = Position.from_dict(landing_position)
            else:
                self._position = Position(landing_position.x, landing_position.y)
        self._pre_handoff_backup = None
        self._bump_version()
        return self.snapshot()

    def rollback_handoff(self, handoff_id: str) -> AvatarSnapshot:
        """Phase 2 (Cancel/Timeout): Atomically restore previous owner and pre-handoff state."""
        if self._handoff_id != handoff_id or self._state != "HANDOFF_PENDING":
            raise HandoffStateError(
                f"Cannot rollback handoff '{handoff_id}'; active handoff is '{self._handoff_id}'."
            )

        backup = self._pre_handoff_backup
        if backup is not None:
            self._owner = backup.owner
            self._position = Position(backup.position.x, backup.position.y)
            self._animation = backup.animation
            self._state = backup.state
            self._facing = backup.facing
            self._emotion = backup.emotion
        else:
            self._state = "ACTIVE"
            self._animation = "idle"

        self._handoff_id = None
        self._pre_handoff_backup = None
        self._bump_version()
        return self.snapshot()
