"""Authoritative multi-tab / multi-PC room state for Browser Shimeji.

Pure Python (no network or GUI dependencies) so cross-tab throws, neighbor
portal routing, disconnect rescue, multi-pet spawning, and thrown balls are
100% deterministic and unit-testable.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

VALID_EMOTIONS: tuple[str, ...] = (
    "neutral",
    "happy",
    "love",
    "excited",
    "scared",
    "dizzy",
    "angry",
    "sleepy",
)

DEFAULT_PET_NAMES: tuple[str, ...] = (
    "Чубрик",
    "Пиксель",
    "Блик",
    "Моти",
    "Шустрик",
    "Зефир",
    "Комета",
    "Пушистик",
)

DEFAULT_PET_EMOTIONS: tuple[str, ...] = (
    "neutral",
    "happy",
    "love",
    "excited",
    "dizzy",
    "sleepy",
    "scared",
    "angry",
)

MAX_PETS = 8


@dataclass
class TabClient:
    """One connected browser tab (on the same PC or on another PC/phone)."""

    tab_id: str
    label: str
    pc_name: str = "Browser"
    browser: str = "Web"
    remote_ip: str = ""
    width: int = 1280
    height: int = 720
    order: int = 0
    connected_at: float = field(default_factory=time.time)

    def to_dict(self, pet_count: int = 0) -> dict[str, Any]:
        return {
            "tab_id": self.tab_id,
            "label": self.label,
            "pc_name": self.pc_name,
            "browser": self.browser,
            "remote_ip": self.remote_ip,
            "width": self.width,
            "height": self.height,
            "order": self.order,
            "pet_count": pet_count,
        }


@dataclass
class BrowserPet:
    """Authoritative server state for one browser Shimeji."""

    pet_id: str
    name: str = "Чубрик"
    emotion: str = "neutral"
    owner_tab_id: str | None = None
    nx: float = 0.5
    ny: float = 0.72
    vx: float = 0.0
    vy: float = 0.0
    facing: str = "right"
    animation: str = "idle"
    scale: int = 4
    note_text: str = ""
    note_from: str = ""
    throws_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pet_id": self.pet_id,
            "name": self.name,
            "emotion": self.emotion,
            "owner_tab_id": self.owner_tab_id,
            "nx": round(self.nx, 4),
            "ny": round(self.ny, 4),
            "vx": round(self.vx, 1),
            "vy": round(self.vy, 1),
            "facing": self.facing,
            "animation": self.animation,
            "scale": self.scale,
            "note_text": self.note_text,
            "note_from": self.note_from,
            "throws_count": self.throws_count,
        }


@dataclass(frozen=True)
class ThrowResult:
    """Result of transferring a pet from one tab/PC to another."""

    pet: BrowserPet
    from_tab_id: str
    to_tab_id: str
    entry_edge: str  # "left" | "right" | "top"
    ny: float
    vx: float
    vy: float
    note_text: str = ""
    note_from: str = ""


@dataclass(frozen=True)
class BallTransferResult:
    """Result of throwing a bouncy ball across a portal edge to another tab/PC."""

    ball_id: str
    from_tab_id: str
    to_tab_id: str
    entry_edge: str  # "left" | "right" | "top"
    ny: float
    vx: float
    vy: float
    color: str = "#ff6b6b"


class RoomState:
    """Single source of truth for all connected tabs/PCs and BrowserPets."""

    def __init__(self) -> None:
        self._tabs: dict[str, TabClient] = {}
        self._tab_order: list[str] = []
        self._pets: dict[str, BrowserPet] = {}
        self._total_throws: int = 0
        self._next_tab_num: int = 1
        self._next_pet_num: int = 1

    @property
    def total_throws(self) -> int:
        return self._total_throws

    @property
    def tab_count(self) -> int:
        return len(self._tabs)

    @property
    def pet_count(self) -> int:
        return len(self._pets)

    def tabs_ordered(self) -> list[TabClient]:
        return [self._tabs[tid] for tid in self._tab_order if tid in self._tabs]

    def get_tab(self, tab_id: str) -> TabClient | None:
        return self._tabs.get(tab_id)

    def get_pet(self, pet_id: str) -> BrowserPet | None:
        return self._pets.get(pet_id)

    def all_pets(self) -> list[BrowserPet]:
        return list(self._pets.values())

    def _sync_order_indices(self) -> None:
        for idx, tid in enumerate(self._tab_order):
            if tid in self._tabs:
                self._tabs[tid].order = idx

    def register_tab(
        self,
        *,
        tab_id: str | None = None,
        label: str = "",
        pc_name: str = "",
        browser: str = "",
        remote_ip: str = "",
        width: int = 1280,
        height: int = 720,
    ) -> tuple[TabClient, list[BrowserPet]]:
        """Register a browser tab and return ``(tab, newly_assigned_pets)``."""
        tid = (tab_id or "").strip() or f"tab-{uuid.uuid4().hex[:8]}"
        if tid in self._tabs:
            tab = self._tabs[tid]
            tab.width = max(240, int(width))
            tab.height = max(240, int(height))
            if label.strip():
                tab.label = label.strip()[:36]
            return tab, []

        num = self._next_tab_num
        self._next_tab_num += 1
        clean_pc = (pc_name or "").strip()[:24] or (remote_ip or f"ПК-{num}")
        clean_browser = (browser or "").strip()[:18] or "Browser"
        clean_label = (label or "").strip()[:36] or f"{clean_pc} · #{num}"

        tab = TabClient(
            tab_id=tid,
            label=clean_label,
            pc_name=clean_pc,
            browser=clean_browser,
            remote_ip=remote_ip,
            width=max(240, int(width)),
            height=max(240, int(height)),
            order=len(self._tab_order),
        )
        self._tabs[tid] = tab
        self._tab_order.append(tid)
        self._sync_order_indices()

        assigned: list[BrowserPet] = []
        if not self._pets:
            pet = self.spawn_pet(owner_tab_id=tid)
            if pet is not None:
                assigned.append(pet)
        else:
            # Rescue any orphaned pets waiting for a tab to open
            for pet in self._pets.values():
                if pet.owner_tab_id is None or pet.owner_tab_id not in self._tabs:
                    pet.owner_tab_id = tid
                    pet.nx = 0.5
                    pet.ny = 0.25
                    pet.vx = 0.0
                    pet.vy = 0.0
                    assigned.append(pet)

        return tab, assigned

    def unregister_tab(self, tab_id: str) -> list[ThrowResult]:
        """Remove a tab and rescue any pets it owned onto another live tab."""
        if tab_id not in self._tabs:
            return []

        old_idx = self._tab_order.index(tab_id) if tab_id in self._tab_order else 0
        self._tabs.pop(tab_id, None)
        if tab_id in self._tab_order:
            self._tab_order.remove(tab_id)
        self._sync_order_indices()

        rescued: list[ThrowResult] = []
        fallback_tab_id: str | None = None
        if self._tab_order:
            fallback_tab_id = self._tab_order[min(old_idx, len(self._tab_order) - 1)]

        for pet in self._pets.values():
            if pet.owner_tab_id == tab_id:
                pet.owner_tab_id = fallback_tab_id
                pet.nx = 0.5
                pet.ny = 0.18
                pet.vx = 0.0
                pet.vy = 180.0
                pet.animation = "receive"
                if fallback_tab_id is not None:
                    rescued.append(
                        ThrowResult(
                            pet=pet,
                            from_tab_id=tab_id,
                            to_tab_id=fallback_tab_id,
                            entry_edge="top",
                            ny=0.18,
                            vx=0.0,
                            vy=180.0,
                            note_text="Спаслись из закрытой вкладки!",
                            note_from="Система",
                        )
                    )
        return rescued

    def rename_tab(self, tab_id: str, new_label: str) -> TabClient | None:
        tab = self._tabs.get(tab_id)
        cleaned = (new_label or "").strip()[:36]
        if tab is None or not cleaned:
            return None
        tab.label = cleaned
        return tab

    def resize_tab(self, tab_id: str, width: int, height: int) -> None:
        tab = self._tabs.get(tab_id)
        if tab is not None:
            tab.width = max(240, int(width))
            tab.height = max(240, int(height))

    def move_tab(self, tab_id: str, direction: int) -> bool:
        """Shift a tab left (-1) or right (+1) in the portal ribbon."""
        if tab_id not in self._tab_order or len(self._tab_order) < 2:
            return False
        idx = self._tab_order.index(tab_id)
        new_idx = max(0, min(len(self._tab_order) - 1, idx + (-1 if direction < 0 else 1)))
        if new_idx == idx:
            return False
        self._tab_order[idx], self._tab_order[new_idx] = self._tab_order[new_idx], self._tab_order[idx]
        self._sync_order_indices()
        return True

    def get_neighbors(self, tab_id: str) -> tuple[TabClient | None, TabClient | None]:
        """Return ``(left_neighbor, right_neighbor)`` in the circular tab ribbon."""
        n = len(self._tab_order)
        if n < 2 or tab_id not in self._tab_order:
            return None, None
        idx = self._tab_order.index(tab_id)
        left_id = self._tab_order[(idx - 1) % n]
        right_id = self._tab_order[(idx + 1) % n]
        return self._tabs.get(left_id), self._tabs.get(right_id)

    def spawn_pet(
        self,
        owner_tab_id: str | None = None,
        *,
        name: str = "",
        emotion: str = "",
    ) -> BrowserPet | None:
        """Spawn a new BrowserPet (up to MAX_PETS)."""
        if len(self._pets) >= MAX_PETS:
            return None
        target_owner = owner_tab_id if owner_tab_id in self._tabs else (self._tab_order[0] if self._tab_order else None)
        idx = self._next_pet_num - 1
        self._next_pet_num += 1

        chosen_name = (name or "").strip()[:24] or DEFAULT_PET_NAMES[idx % len(DEFAULT_PET_NAMES)]
        chosen_emotion = emotion if emotion in VALID_EMOTIONS else DEFAULT_PET_EMOTIONS[idx % len(DEFAULT_PET_EMOTIONS)]

        pet = BrowserPet(
            pet_id=f"pet-{uuid.uuid4().hex[:6]}",
            name=chosen_name,
            emotion=chosen_emotion,
            owner_tab_id=target_owner,
            nx=0.35 + 0.3 * ((idx % 3) / 2.0),
            ny=0.22,
            vx=0.0,
            vy=0.0,
            facing="right" if idx % 2 == 0 else "left",
            animation="receive",
        )
        self._pets[pet.pet_id] = pet
        return pet

    def remove_pet(self, pet_id: str) -> bool:
        """Remove an extra pet (always keeps at least 1 pet in the room)."""
        if len(self._pets) <= 1 or pet_id not in self._pets:
            return False
        del self._pets[pet_id]
        return True

    def update_pet_state(
        self,
        pet_id: str,
        from_tab_id: str,
        *,
        nx: float | None = None,
        ny: float | None = None,
        vx: float | None = None,
        vy: float | None = None,
        facing: str | None = None,
        animation: str | None = None,
        emotion: str | None = None,
    ) -> BrowserPet | None:
        pet = self._pets.get(pet_id)
        if pet is None or pet.owner_tab_id != from_tab_id:
            return None
        if nx is not None:
            pet.nx = max(-0.2, min(1.2, float(nx)))
        if ny is not None:
            pet.ny = max(0.0, min(1.0, float(ny)))
        if vx is not None:
            pet.vx = float(vx)
        if vy is not None:
            pet.vy = float(vy)
        if facing in ("left", "right"):
            pet.facing = facing
        if animation:
            pet.animation = str(animation)[:24]
        if emotion in VALID_EMOTIONS:
            pet.emotion = emotion
        return pet

    def _resolve_target(
        self,
        from_tab_id: str,
        edge: str,
        target_tab_id: str | None,
    ) -> tuple[TabClient | None, str]:
        """Resolve destination tab and entry edge (``left`` / ``right`` / ``top``)."""
        if target_tab_id and target_tab_id != from_tab_id and target_tab_id in self._tabs:
            entry = "left" if edge == "right" else ("right" if edge == "left" else "top")
            return self._tabs[target_tab_id], entry

        left_nb, right_nb = self.get_neighbors(from_tab_id)
        if edge == "left" and left_nb is not None:
            return left_nb, "right"
        if edge == "right" and right_nb is not None:
            return right_nb, "left"
        if right_nb is not None:
            return right_nb, "top"
        return None, "top"

    def throw_pet(
        self,
        pet_id: str,
        from_tab_id: str,
        *,
        edge: str = "right",
        target_tab_id: str | None = None,
        ny: float = 0.55,
        vx: float = 700.0,
        vy: float = -220.0,
        emotion: str | None = None,
        note_text: str = "",
    ) -> ThrowResult | None:
        """Atomically transfer a pet from ``from_tab_id`` to another tab/PC."""
        pet = self._pets.get(pet_id)
        if pet is None or pet.owner_tab_id != from_tab_id:
            return None

        target, entry_edge = self._resolve_target(from_tab_id, edge, target_tab_id)
        if target is None or target.tab_id == from_tab_id:
            return None

        clamped_ny = max(0.08, min(0.88, float(ny)))
        clamped_vx = max(-2400.0, min(2400.0, float(vx)))
        clamped_vy = max(-1800.0, min(1400.0, float(vy)))

        # Ensure pet actually launches inward from the arrival portal edge!
        if entry_edge == "left" and clamped_vx < 320.0:
            clamped_vx = max(460.0, abs(clamped_vx))
        elif entry_edge == "right" and clamped_vx > -320.0:
            clamped_vx = -max(460.0, abs(clamped_vx))

        if emotion in VALID_EMOTIONS:
            pet.emotion = emotion

        sender = self._tabs.get(from_tab_id)
        clean_note = (note_text or "").strip()[:100]
        sender_name = sender.label if sender is not None else from_tab_id

        pet.owner_tab_id = target.tab_id
        pet.nx = 0.04 if entry_edge == "left" else (0.96 if entry_edge == "right" else 0.5)
        pet.ny = clamped_ny if entry_edge != "top" else 0.16
        pet.vx = clamped_vx
        pet.vy = clamped_vy
        pet.facing = "right" if clamped_vx >= 0 else "left"
        pet.animation = "receive"
        pet.note_text = clean_note
        pet.note_from = sender_name if clean_note else ""
        pet.throws_count += 1
        self._total_throws += 1

        return ThrowResult(
            pet=pet,
            from_tab_id=from_tab_id,
            to_tab_id=target.tab_id,
            entry_edge=entry_edge,
            ny=pet.ny,
            vx=pet.vx,
            vy=pet.vy,
            note_text=clean_note,
            note_from=sender_name if clean_note else "",
        )

    def summon_pet(self, pet_id: str, to_tab_id: str) -> ThrowResult | None:
        """Pull a pet from wherever it currently is into ``to_tab_id``."""
        pet = self._pets.get(pet_id)
        if pet is None or to_tab_id not in self._tabs or pet.owner_tab_id == to_tab_id:
            return None
        from_tab_id = pet.owner_tab_id or ""
        pet.owner_tab_id = to_tab_id
        pet.nx = 0.5
        pet.ny = 0.16
        pet.vx = 0.0
        pet.vy = 160.0
        pet.animation = "receive"
        pet.throws_count += 1
        self._total_throws += 1
        return ThrowResult(
            pet=pet,
            from_tab_id=from_tab_id,
            to_tab_id=to_tab_id,
            entry_edge="top",
            ny=0.16,
            vx=0.0,
            vy=160.0,
            note_text="Я тут! 👋",
            note_from="",
        )

    def throw_ball(
        self,
        from_tab_id: str,
        *,
        edge: str = "right",
        target_tab_id: str | None = None,
        ny: float = 0.5,
        vx: float = 750.0,
        vy: float = -240.0,
        color: str = "#ff6b6b",
    ) -> BallTransferResult | None:
        """Route a thrown toy ball across a portal edge to another tab/PC."""
        if from_tab_id not in self._tabs:
            return None
        target, entry_edge = self._resolve_target(from_tab_id, edge, target_tab_id)
        if target is None or target.tab_id == from_tab_id:
            return None
        clamped_ny = max(0.08, min(0.88, float(ny)))
        clamped_vx = max(-2400.0, min(2400.0, float(vx)))
        clamped_vy = max(-1600.0, min(1400.0, float(vy)))
        if entry_edge == "left" and clamped_vx < 320.0:
            clamped_vx = max(460.0, abs(clamped_vx))
        elif entry_edge == "right" and clamped_vx > -320.0:
            clamped_vx = -max(460.0, abs(clamped_vx))
        return BallTransferResult(
            ball_id=f"ball-{uuid.uuid4().hex[:6]}",
            from_tab_id=from_tab_id,
            to_tab_id=target.tab_id,
            entry_edge=entry_edge,
            ny=clamped_ny,
            vx=clamped_vx,
            vy=clamped_vy,
            color=(color or "#ff6b6b")[:20],
        )

    def snapshot_for_tab(self, viewer_tab_id: str | None = None) -> dict[str, Any]:
        """Build JSON-serializable room snapshot (with left/right neighbors for viewer)."""
        counts: dict[str, int] = {}
        for pet in self._pets.values():
            if pet.owner_tab_id:
                counts[pet.owner_tab_id] = counts.get(pet.owner_tab_id, 0) + 1

        tabs = [t.to_dict(pet_count=counts.get(t.tab_id, 0)) for t in self.tabs_ordered()]
        left_nb, right_nb = self.get_neighbors(viewer_tab_id) if viewer_tab_id else (None, None)
        return {
            "type": "room_state",
            "tabs": tabs,
            "pets": [p.to_dict() for p in self._pets.values()],
            "total_throws": self._total_throws,
            "max_pets": MAX_PETS,
            "neighbors": {
                "left": left_nb.to_dict(pet_count=counts.get(left_nb.tab_id, 0)) if left_nb else None,
                "right": right_nb.to_dict(pet_count=counts.get(right_nb.tab_id, 0)) if right_nb else None,
            },
        }
