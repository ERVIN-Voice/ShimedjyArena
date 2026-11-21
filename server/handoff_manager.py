"""Two-phase atomic handoff coordinator with timeout rollback and duplicate protection."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from server.avatar_state import (
    AvatarSnapshot,
    AvatarStateStore,
    HandoffStateError,
    NotOwnerError,
    Position,
)
from server.device_manager import DeviceManager
from shared.config_loader import ProtocolConfig, load_protocol_config

logger = logging.getLogger(__name__)


class HandoffError(Exception):
    """Structured error raised for invalid handoff requests."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PendingHandoff:
    """In-flight two-phase handoff transaction."""

    handoff_id: str
    source_device_id: str
    target_device_id: str
    edge: str
    normalized_y: float
    created_at: float
    expires_at: float
    timeout_seconds: float
    idempotency_key: str | None = None
    status: str = "PENDING"  # "PENDING" | "COMPLETED" | "CANCELLED"
    cancel_reason: str | None = None
    confirmation_source: str | None = None


@dataclass
class HandoffOutcome:
    """Result returned from initiate / confirm / cancel operations."""

    handoff: PendingHandoff
    avatar_snapshot: AvatarSnapshot
    duplicate: bool = False
    completed: bool = False
    cancelled: bool = False


OnTimeoutCallback = Callable[[HandoffOutcome], Awaitable[None]]


class HandoffManager:
    """Coordinates atomic two-phase avatar handoffs across devices."""

    def __init__(
        self,
        state_store: AvatarStateStore,
        device_manager: DeviceManager,
        protocol: ProtocolConfig | None = None,
        *,
        timeout_seconds: float | None = None,
        enforce_timeout_bounds: bool = True,
        on_timeout_cancelled: OnTimeoutCallback | None = None,
        max_history: int = 256,
    ) -> None:
        self._state_store = state_store
        self._device_manager = device_manager
        self._protocol = protocol or load_protocol_config()
        self._on_timeout_cancelled = on_timeout_cancelled
        self._max_history = max_history

        chosen_timeout = (
            self._protocol.handoff_default_timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        if enforce_timeout_bounds:
            min_t = self._protocol.handoff_min_timeout_seconds
            max_t = self._protocol.handoff_max_timeout_seconds
            if not (min_t <= chosen_timeout <= max_t):
                raise ValueError(
                    f"Handoff timeout {chosen_timeout}s must be within [{min_t}, {max_t}] seconds."
                )
        elif chosen_timeout <= 0:
            raise ValueError("Handoff timeout must be positive.")

        self._timeout_seconds = chosen_timeout
        self._lock = asyncio.Lock()
        self._active: PendingHandoff | None = None
        self._timeout_task: asyncio.Task[None] | None = None

        # Bounded LRU caches for idempotency and duplicate-message protection
        self._history_by_id: OrderedDict[str, PendingHandoff] = OrderedDict()
        self._idempotency_index: OrderedDict[str, str] = OrderedDict()

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def active_handoff(self) -> PendingHandoff | None:
        return self._active

    def _record_history(self, handoff: PendingHandoff) -> None:
        self._history_by_id[handoff.handoff_id] = handoff
        self._history_by_id.move_to_end(handoff.handoff_id)
        while len(self._history_by_id) > self._max_history:
            self._history_by_id.popitem(last=False)

        if handoff.idempotency_key:
            self._idempotency_index[handoff.idempotency_key] = handoff.handoff_id
            self._idempotency_index.move_to_end(handoff.idempotency_key)
            while len(self._idempotency_index) > self._max_history:
                self._idempotency_index.popitem(last=False)

    def _cancel_timer_unlocked(self) -> None:
        if self._timeout_task is not None:
            current_task = asyncio.current_task()
            if self._timeout_task is not current_task and not self._timeout_task.done():
                self._timeout_task.cancel()
            self._timeout_task = None

    async def shutdown(self) -> None:
        """Cancel any active timeout task during server shutdown."""
        async with self._lock:
            self._cancel_timer_unlocked()

    async def initiate_handoff(
        self,
        *,
        source_device_id: str,
        target_device_id: str | None = None,
        edge: str = "right",
        normalized_y: float = 0.5,
        idempotency_key: str | None = None,
    ) -> HandoffOutcome:
        """Phase 1: Validate owner & target, create handoff_id, lock state, start timeout."""
        async with self._lock:
            # Duplicate request protection via idempotency_key
            if idempotency_key and idempotency_key in self._idempotency_index:
                existing_id = self._idempotency_index[idempotency_key]
                existing = self._history_by_id.get(existing_id)
                if existing is not None:
                    logger.info(
                        "Deduplicated handoff_request (idempotency_key=%s -> handoff_id=%s, status=%s)",
                        idempotency_key,
                        existing.handoff_id,
                        existing.status,
                    )
                    return HandoffOutcome(
                        handoff=existing,
                        avatar_snapshot=self._state_store.snapshot(),
                        duplicate=True,
                        completed=(existing.status == "COMPLETED"),
                        cancelled=(existing.status == "CANCELLED"),
                    )

            if edge not in self._protocol.allowed_edges:
                raise HandoffError(
                    "INVALID_EDGE",
                    f"Edge '{edge}' is not allowed. Allowed: {sorted(self._protocol.allowed_edges)}",
                )

            if self._active is not None:
                raise HandoffError(
                    "HANDOFF_ALREADY_ACTIVE",
                    f"Handoff '{self._active.handoff_id}' is already in progress.",
                )

            if self._state_store.owner != source_device_id:
                raise HandoffError(
                    "NOT_OWNER",
                    f"Device '{source_device_id}' does not own the avatar (owner='{self._state_store.owner}').",
                )

            # Resolve target device (explicit or opposite-platform default)
            resolved_target = target_device_id
            if not resolved_target:
                peer = self._device_manager.find_default_target(source_device_id)
                if peer is None:
                    raise HandoffError(
                        "NO_TARGET_AVAILABLE",
                        "No connected peer device available to receive the avatar.",
                    )
                resolved_target = peer.device_id

            if resolved_target == source_device_id:
                raise HandoffError("INVALID_TARGET", "Cannot hand off avatar to the same device.")

            if not self._device_manager.is_connected(resolved_target):
                raise HandoffError(
                    "TARGET_OFFLINE",
                    f"Target device '{resolved_target}' is not connected.",
                )

            handoff_id = f"ho-{uuid.uuid4().hex[:12]}"
            now = time.time()
            norm_y = min(1.0, max(0.0, float(normalized_y)))

            try:
                snapshot = self._state_store.begin_handoff(handoff_id, expected_owner=source_device_id)
            except (NotOwnerError, HandoffStateError) as exc:
                raise HandoffError("STATE_CONFLICT", str(exc)) from exc

            pending = PendingHandoff(
                handoff_id=handoff_id,
                source_device_id=source_device_id,
                target_device_id=resolved_target,
                edge=edge,
                normalized_y=norm_y,
                created_at=now,
                expires_at=now + self._timeout_seconds,
                timeout_seconds=self._timeout_seconds,
                idempotency_key=idempotency_key,
                status="PENDING",
            )
            self._active = pending
            self._record_history(pending)

            # Schedule automatic timeout cancellation & rollback
            self._timeout_task = asyncio.create_task(
                self._timeout_watchdog(handoff_id, self._timeout_seconds),
                name=f"handoff-timeout-{handoff_id}",
            )

            logger.info(
                "Handoff initiated: %s (%s -> %s, edge=%s, timeout=%.1fs)",
                handoff_id,
                source_device_id,
                resolved_target,
                edge,
                self._timeout_seconds,
            )
            return HandoffOutcome(handoff=pending, avatar_snapshot=snapshot)

    async def confirm_handoff(
        self,
        *,
        handoff_id: str,
        device_id: str,
        accepted: bool,
        confirmation_source: str = "touch",
        landing_position: Position | dict[str, Any] | None = None,
    ) -> HandoffOutcome:
        """Phase 2: Target device accepts or declines the pending handoff."""
        async with self._lock:
            # Idempotency / duplicate confirmation check for already-finished handoffs
            if self._active is None or self._active.handoff_id != handoff_id:
                historical = self._history_by_id.get(handoff_id)
                if historical is not None and historical.status != "PENDING":
                    logger.info(
                        "Ignoring duplicate handoff_confirm for finalized %s (status=%s)",
                        handoff_id,
                        historical.status,
                    )
                    return HandoffOutcome(
                        handoff=historical,
                        avatar_snapshot=self._state_store.snapshot(),
                        duplicate=True,
                        completed=(historical.status == "COMPLETED"),
                        cancelled=(historical.status == "CANCELLED"),
                    )
                raise HandoffError(
                    "HANDOFF_NOT_FOUND",
                    f"No active handoff with id '{handoff_id}'.",
                )

            pending = self._active
            if device_id != pending.target_device_id:
                raise HandoffError(
                    "WRONG_TARGET_DEVICE",
                    f"Device '{device_id}' is not the target '{pending.target_device_id}' for '{handoff_id}'.",
                )

            if accepted and confirmation_source not in self._protocol.allowed_confirmation_sources:
                raise HandoffError(
                    "INVALID_CONFIRMATION_SOURCE",
                    f"Unsupported confirmation_source '{confirmation_source}'.",
                )

            # Guard against late confirmation after deadline
            if time.time() > pending.expires_at:
                return self._rollback_unlocked(pending, reason="timeout")

            self._cancel_timer_unlocked()

            if accepted:
                snapshot = self._state_store.commit_handoff(
                    handoff_id,
                    new_owner=pending.target_device_id,
                    landing_position=landing_position,
                )
                pending.status = "COMPLETED"
                pending.confirmation_source = confirmation_source
                self._active = None
                self._record_history(pending)
                logger.info(
                    "Handoff completed: %s (owner: %s -> %s, via=%s)",
                    handoff_id,
                    pending.source_device_id,
                    pending.target_device_id,
                    confirmation_source,
                )
                return HandoffOutcome(
                    handoff=pending,
                    avatar_snapshot=snapshot,
                    completed=True,
                )

            return self._rollback_unlocked(pending, reason="rejected")

    async def cancel_handoff(self, handoff_id: str, reason: str = "cancelled") -> HandoffOutcome | None:
        """Explicitly cancel an active handoff and restore source ownership."""
        async with self._lock:
            if self._active is None or self._active.handoff_id != handoff_id:
                return None
            return self._rollback_unlocked(self._active, reason=reason)

    async def handle_device_disconnect(self, device_id: str) -> HandoffOutcome | None:
        """If source or target disconnects mid-handoff, roll back cleanly to source."""
        async with self._lock:
            if self._active is None:
                return None
            if device_id == self._active.target_device_id:
                return self._rollback_unlocked(self._active, reason="target_disconnected")
            if device_id == self._active.source_device_id:
                return self._rollback_unlocked(self._active, reason="source_disconnected")
            return None

    def _rollback_unlocked(self, pending: PendingHandoff, *, reason: str) -> HandoffOutcome:
        self._cancel_timer_unlocked()
        snapshot = self._state_store.rollback_handoff(pending.handoff_id)
        pending.status = "CANCELLED"
        pending.cancel_reason = reason
        self._active = None
        self._record_history(pending)
        logger.info(
            "Handoff cancelled: %s (reason=%s, restored_owner=%s)",
            pending.handoff_id,
            reason,
            snapshot.owner,
        )
        return HandoffOutcome(
            handoff=pending,
            avatar_snapshot=snapshot,
            cancelled=True,
        )

    async def _timeout_watchdog(self, handoff_id: str, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            return

        outcome: HandoffOutcome | None = None
        async with self._lock:
            if self._active is not None and self._active.handoff_id == handoff_id:
                outcome = self._rollback_unlocked(self._active, reason="timeout")

        if outcome is not None and self._on_timeout_cancelled is not None:
            try:
                await self._on_timeout_cancelled(outcome)
            except Exception:
                logger.exception("Error in on_timeout_cancelled callback for %s", handoff_id)
