"""Tests for authoritative AvatarStateStore invariants."""

from __future__ import annotations

import pytest

from server.avatar_state import (
    AvatarStateError,
    AvatarStateStore,
    HandoffStateError,
    NotOwnerError,
    Position,
)


def test_initial_owner_assigned_only_once() -> None:
    store = AvatarStateStore()
    assert store.owner is None
    assert store.assign_owner_if_unowned("win-1") is True
    assert store.owner == "win-1"
    # Second device must NOT steal ownership
    assert store.assign_owner_if_unowned("android-1") is False
    assert store.owner == "win-1"


def test_only_owner_can_update_state() -> None:
    store = AvatarStateStore(initial_owner="win-1")
    snap = store.update_from_owner(
        "win-1",
        position=Position(320.5, 480.0),
        animation="walk",
        state="ACTIVE",
        facing="left",
    )
    assert snap.owner == "win-1"
    assert snap.position.x == 320.5
    assert snap.animation == "walk"
    assert snap.facing == "left"
    assert snap.version == 2

    with pytest.raises(NotOwnerError):
        store.update_from_owner("android-1", animation="run")

    with pytest.raises(AvatarStateError):
        store.update_from_owner("win-1", animation="invalid_anim_name")


def test_atomic_handoff_commit_and_rollback() -> None:
    store = AvatarStateStore(initial_owner="android-1")
    store.update_from_owner(
        "android-1",
        position={"x": 150.0, "y": 400.0},
        animation="walk",
        state="ACTIVE",
        facing="right",
    )

    # Begin handoff locks state
    pending = store.begin_handoff("ho-001", expected_owner="android-1")
    assert pending.state == "HANDOFF_PENDING"
    assert pending.handoff_id == "ho-001"
    assert pending.owner == "android-1"

    # Mutations blocked while handoff pending
    with pytest.raises(HandoffStateError):
        store.update_from_owner("android-1", animation="idle")

    # Concurrent second handoff blocked
    with pytest.raises(HandoffStateError):
        store.begin_handoff("ho-002", expected_owner="android-1")

    # Rollback restores exact pre-handoff snapshot
    restored = store.rollback_handoff("ho-001")
    assert restored.owner == "android-1"
    assert restored.state == "ACTIVE"
    assert restored.animation == "walk"
    assert restored.position.x == 150.0
    assert restored.position.y == 400.0
    assert restored.handoff_id is None

    # Now commit handoff to windows
    store.begin_handoff("ho-003", expected_owner="android-1")
    committed = store.commit_handoff("ho-003", new_owner="win-1", landing_position={"x": 42.0, "y": 500.0})
    assert committed.owner == "win-1"
    assert committed.state == "ACTIVE"
    assert committed.animation == "receive"
    assert committed.handoff_id is None
    assert committed.position.x == 42.0
    assert committed.position.y == 500.0
