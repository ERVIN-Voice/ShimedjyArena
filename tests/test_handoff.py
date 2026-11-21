"""Tests for two-phase atomic handoff (Android <-> Windows), timeout rollback,
disconnect safety, and duplicate/idempotency protection.
"""

from __future__ import annotations

import asyncio

import pytest

from server.avatar_state import AvatarStateStore
from server.device_manager import DeviceManager
from server.handoff_manager import HandoffError, HandoffManager, HandoffOutcome


def _make_setup(
    *,
    initial_owner: str = "android-1",
    timeout_seconds: float = 0.25,
    enforce_bounds: bool = False,
    on_timeout=None,
) -> tuple[AvatarStateStore, DeviceManager, HandoffManager]:
    store = AvatarStateStore(initial_owner=initial_owner)
    devices = DeviceManager()
    devices.register(
        device_id="win-1",
        device_type="windows",
        device_name="Desktop PC",
        websocket=object(),
    )
    devices.register(
        device_id="android-1",
        device_type="android",
        device_name="Pixel Phone",
        websocket=object(),
    )
    manager = HandoffManager(
        store,
        devices,
        timeout_seconds=timeout_seconds,
        enforce_timeout_bounds=enforce_bounds,
        on_timeout_cancelled=on_timeout,
    )
    return store, devices, manager


def test_timeout_bounds_enforced_in_production_mode() -> None:
    store = AvatarStateStore(initial_owner="win-1")
    devices = DeviceManager()
    with pytest.raises(ValueError):
        HandoffManager(store, devices, timeout_seconds=2.0, enforce_timeout_bounds=True)
    with pytest.raises(ValueError):
        HandoffManager(store, devices, timeout_seconds=12.0, enforce_timeout_bounds=True)

    valid = HandoffManager(store, devices, timeout_seconds=6.5, enforce_timeout_bounds=True)
    assert valid.timeout_seconds == 6.5


async def test_two_phase_handoff_android_to_windows_and_back() -> None:
    store, _, mgr = _make_setup(initial_owner="android-1", timeout_seconds=1.0)
    try:
        # Android -> Windows (target resolved automatically as opposite platform)
        init_out = await mgr.initiate_handoff(
            source_device_id="android-1",
            edge="right",
            normalized_y=0.4,
            idempotency_key="req-a2w-1",
        )
        assert not init_out.duplicate
        assert init_out.handoff.target_device_id == "win-1"
        assert store.state == "HANDOFF_PENDING"
        assert store.owner == "android-1"

        # Windows confirms acceptance
        confirm_out = await mgr.confirm_handoff(
            handoff_id=init_out.handoff.handoff_id,
            device_id="win-1",
            accepted=True,
            confirmation_source="click",
            landing_position={"x": 50.0, "y": 420.0},
        )
        assert confirm_out.completed
        assert store.owner == "win-1"
        assert store.state == "ACTIVE"
        assert store.handoff_id is None

        # Windows -> Android
        back_init = await mgr.initiate_handoff(
            source_device_id="win-1",
            edge="left",
            normalized_y=0.7,
            idempotency_key="req-w2a-1",
        )
        assert back_init.handoff.target_device_id == "android-1"
        assert store.state == "HANDOFF_PENDING"

        back_confirm = await mgr.confirm_handoff(
            handoff_id=back_init.handoff.handoff_id,
            device_id="android-1",
            accepted=True,
            confirmation_source="touch",
        )
        assert back_confirm.completed
        assert store.owner == "android-1"
        assert store.state == "ACTIVE"
    finally:
        await mgr.shutdown()


async def test_handoff_timeout_cancels_and_restores_previous_state() -> None:
    timed_out: list[HandoffOutcome] = []

    async def on_timeout(outcome: HandoffOutcome) -> None:
        timed_out.append(outcome)

    store, _, mgr = _make_setup(initial_owner="android-1", timeout_seconds=0.15, on_timeout=on_timeout)
    try:
        store.update_from_owner("android-1", position={"x": 88.0, "y": 240.0}, animation="walk")
        init_out = await mgr.initiate_handoff(source_device_id="android-1", edge="right")
        assert store.state == "HANDOFF_PENDING"

        # Wait past timeout without confirmation
        await asyncio.sleep(0.28)

        assert len(timed_out) == 1
        assert timed_out[0].cancelled
        assert timed_out[0].handoff.cancel_reason == "timeout"
        assert store.owner == "android-1"
        assert store.state == "ACTIVE"
        assert store.handoff_id is None
        assert store.snapshot().position.x == 88.0
        assert store.snapshot().animation == "walk"

        # Late confirmation after timeout must be safely ignored as duplicate/finalized
        late = await mgr.confirm_handoff(
            handoff_id=init_out.handoff.handoff_id,
            device_id="win-1",
            accepted=True,
            confirmation_source="click",
        )
        assert late.duplicate
        assert late.cancelled
        assert store.owner == "android-1"
    finally:
        await mgr.shutdown()


async def test_duplicate_requests_and_duplicate_confirmations_are_idempotent() -> None:
    store, _, mgr = _make_setup(initial_owner="win-1", timeout_seconds=1.0)
    try:
        first = await mgr.initiate_handoff(
            source_device_id="win-1",
            edge="right",
            idempotency_key="idem-42",
        )
        # Retransmitted request with same idempotency key returns same handoff_id
        second = await mgr.initiate_handoff(
            source_device_id="win-1",
            edge="right",
            idempotency_key="idem-42",
        )
        assert second.duplicate
        assert second.handoff.handoff_id == first.handoff.handoff_id

        # Different handoff while one is active is rejected
        with pytest.raises(HandoffError) as exc_info:
            await mgr.initiate_handoff(source_device_id="win-1", edge="left", idempotency_key="idem-other")
        assert exc_info.value.code == "HANDOFF_ALREADY_ACTIVE"

        # First confirm succeeds; duplicate confirm is idempotent and does not corrupt state
        c1 = await mgr.confirm_handoff(
            handoff_id=first.handoff.handoff_id,
            device_id="android-1",
            accepted=True,
            confirmation_source="gesture",
        )
        assert c1.completed and not c1.duplicate
        assert store.owner == "android-1"

        c2 = await mgr.confirm_handoff(
            handoff_id=first.handoff.handoff_id,
            device_id="android-1",
            accepted=True,
            confirmation_source="gesture",
        )
        assert c2.duplicate and c2.completed
        assert store.owner == "android-1"
    finally:
        await mgr.shutdown()


async def test_disconnect_mid_handoff_rolls_back_to_source() -> None:
    store, _, mgr = _make_setup(initial_owner="android-1", timeout_seconds=2.0)
    try:
        await mgr.initiate_handoff(source_device_id="android-1", edge="right")
        assert store.state == "HANDOFF_PENDING"

        # Target disconnects before confirming
        outcome = await mgr.handle_device_disconnect("win-1")
        assert outcome is not None
        assert outcome.cancelled
        assert outcome.handoff.cancel_reason == "target_disconnected"
        assert store.owner == "android-1"
        assert store.state == "ACTIVE"
        assert store.handoff_id is None
    finally:
        await mgr.shutdown()


async def test_wrong_target_cannot_confirm_handoff() -> None:
    store, devices, mgr = _make_setup(initial_owner="win-1", timeout_seconds=2.0)
    devices.register(
        device_id="android-2",
        device_type="android",
        device_name="Tablet",
        websocket=object(),
    )
    try:
        init_out = await mgr.initiate_handoff(
            source_device_id="win-1",
            target_device_id="android-1",
            edge="right",
        )
        with pytest.raises(HandoffError) as exc_info:
            await mgr.confirm_handoff(
                handoff_id=init_out.handoff.handoff_id,
                device_id="android-2",
                accepted=True,
                confirmation_source="touch",
            )
        assert exc_info.value.code == "WRONG_TARGET_DEVICE"
        assert store.state == "HANDOFF_PENDING"
    finally:
        await mgr.shutdown()
