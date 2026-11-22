"""Device registry and connection lifecycle manager for GCS Local Hub."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from shared.config_loader import ProtocolConfig, load_protocol_config


class DeviceError(Exception):
    """Raised when device registration or lookup fails."""


@dataclass
class DeviceInfo:
    """Metadata and active connection state for a registered client device."""

    device_id: str
    device_type: str  # "windows" | "android"
    device_name: str
    screen_width: int = 1920
    screen_height: int = 1080
    capabilities: list[str] = field(default_factory=list)
    connected: bool = True
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    websocket: Any = field(default=None, repr=False)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "device_name": self.device_name,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "capabilities": list(self.capabilities),
            "connected": self.connected,
            "last_seen": self.last_seen,
        }


class DeviceManager:
    """Tracks registered Windows and Android clients and their WebSocket handles."""

    def __init__(self, protocol: ProtocolConfig | None = None) -> None:
        self._protocol = protocol or load_protocol_config()
        self._devices: dict[str, DeviceInfo] = {}
        self._ws_to_device_id: dict[Any, str] = {}

    def register(
        self,
        *,
        device_id: str,
        device_type: str,
        device_name: str,
        websocket: Any,
        screen_width: int = 1920,
        screen_height: int = 1080,
        capabilities: list[str] | None = None,
    ) -> tuple[DeviceInfo, Any | None]:
        """Register or reconnect a device.

        Returns `(device_info, superseded_websocket)`. When a device reconnects with
        the same `device_id`, the old WebSocket mapping is cleanly detached so closing
        the superseded socket will NOT mark the newly reconnected device offline.
        """
        if not device_id or not isinstance(device_id, str):
            raise DeviceError("device_id must be a non-empty string.")
        if device_type not in self._protocol.device_types:
            raise DeviceError(
                f"Unsupported device_type '{device_type}'. Allowed: {sorted(self._protocol.device_types)}"
            )

        now = time.time()
        superseded_ws: Any | None = None
        existing = self._devices.get(device_id)
        if existing is not None:
            if existing.websocket is not None and existing.websocket is not websocket:
                superseded_ws = existing.websocket
                self._ws_to_device_id.pop(superseded_ws, None)
            existing.device_type = device_type
            existing.device_name = device_name or existing.device_name
            existing.screen_width = int(screen_width)
            existing.screen_height = int(screen_height)
            existing.capabilities = list(capabilities or [])
            existing.connected = True
            existing.connected_at = now
            existing.last_seen = now
            existing.websocket = websocket
            info = existing
        else:
            info = DeviceInfo(
                device_id=device_id,
                device_type=device_type,
                device_name=device_name or f"{device_type}-{device_id[:6]}",
                screen_width=int(screen_width),
                screen_height=int(screen_height),
                capabilities=list(capabilities or []),
                connected=True,
                connected_at=now,
                last_seen=now,
                websocket=websocket,
            )
            self._devices[device_id] = info

        if websocket is not None:
            self._ws_to_device_id[websocket] = device_id
        return info, superseded_ws

    def disconnect_by_websocket(self, websocket: Any) -> DeviceInfo | None:
        """Mark the device associated with `websocket` as disconnected.

        If `websocket` was already superseded by a newer reconnect, returns None so
        the active session is undisturbed.
        """
        device_id = self._ws_to_device_id.pop(websocket, None)
        if device_id is None:
            return None
        info = self._devices.get(device_id)
        if info is None:
            return None
        if info.websocket is websocket:
            info.connected = False
            info.websocket = None
            info.last_seen = time.time()
            return info
        return None

    def mark_disconnected(self, device_id: str) -> DeviceInfo | None:
        """Explicitly disconnect a device by ID."""
        info = self._devices.get(device_id)
        if info is None:
            return None
        if info.websocket is not None:
            self._ws_to_device_id.pop(info.websocket, None)
        info.connected = False
        info.websocket = None
        info.last_seen = time.time()
        return info

    def touch(self, device_id: str) -> None:
        info = self._devices.get(device_id)
        if info is not None:
            info.last_seen = time.time()

    def get(self, device_id: str) -> DeviceInfo | None:
        return self._devices.get(device_id)

    def get_by_websocket(self, websocket: Any) -> DeviceInfo | None:
        device_id = self._ws_to_device_id.get(websocket)
        return self._devices.get(device_id) if device_id is not None else None

    def is_connected(self, device_id: str) -> bool:
        info = self._devices.get(device_id)
        return bool(info is not None and info.connected)

    def connected_devices(self) -> list[DeviceInfo]:
        return [d for d in self._devices.values() if d.connected]

    def all_devices(self) -> list[DeviceInfo]:
        return list(self._devices.values())

    def find_default_target(self, source_device_id: str) -> DeviceInfo | None:
        """Select a connected peer device, preferring the opposite platform (Windows <-> Android)."""
        source = self._devices.get(source_device_id)
        candidates = [d for d in self._devices.values() if d.connected and d.device_id != source_device_id]
        if not candidates:
            return None
        if source is not None:
            opposite = [d for d in candidates if d.device_type != source.device_type]
            if opposite:
                return opposite[0]
        return candidates[0]

    def list_devices_payload(self) -> list[dict[str, Any]]:
        return [d.to_public_dict() for d in self._devices.values()]
