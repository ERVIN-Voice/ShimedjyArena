"""GCS Local Hub — Central asyncio WebSocket server, mDNS advertiser, and QR pairing helper.

Runs locally on the Windows host (no cloud APIs, no internet required) and acts as
the single source of truth for the virtual companion across Windows and Android clients.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import signal
import socket
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import qrcode
import websockets
from websockets.asyncio.server import Server, ServerConnection
from websockets.exceptions import ConnectionClosed

from server.avatar_state import (
    AvatarStateError,
    AvatarStateStore,
    HandoffStateError,
    NotOwnerError,
)
from server.device_manager import DeviceError, DeviceManager
from server.handoff_manager import HandoffError, HandoffManager, HandoffOutcome
from shared.config_loader import ProtocolConfig, load_protocol_config, make_envelope

logger = logging.getLogger(__name__)

HUB_VERSION = "1.0.0"


def detect_local_ip() -> str:
    """Best-effort LAN IPv4 detection without external network requests."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not transmit packets; asks OS routing table for preferred LAN interface
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        sock.close()

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip:
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def build_pairing_uri(host: str, port: int, protocol: ProtocolConfig | None = None) -> str:
    """Build a QR-scannable local pairing URI for Android manual connection."""
    proto = protocol or load_protocol_config()
    display_host = detect_local_ip() if host in ("0.0.0.0", "", "::") else host
    ws_url = f"ws://{display_host}:{port}"
    query = urlencode(
        {
            "ws": ws_url,
            "host": display_host,
            "port": str(port),
            "protocol": proto.protocol_version,
        }
    )
    return f"{proto.qr_scheme}?{query}"


def render_pairing_qr_ascii(pairing_uri: str) -> str:
    """Render a compact ASCII QR code for console output."""
    qr = qrcode.QRCode(border=1)
    qr.add_data(pairing_uri)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


def save_pairing_qr_png(pairing_uri: str, output_path: Path) -> Path:
    """Save a crisp pixel-perfect pairing QR code PNG image."""
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(pairing_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    return output_path


class _MessageIdDeduplicator:
    """Bounded FIFO/LRU set rejecting duplicate message_ids from retransmissions."""

    def __init__(self, max_entries: int = 1024) -> None:
        self._max_entries = max_entries
        self._seen: OrderedDict[str, float] = OrderedDict()

    def mark_and_check_duplicate(self, key: str) -> bool:
        """Return True if `key` was already processed; otherwise record and return False."""
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = time.time()
        while len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)
        return False


class _MDNSAdvertiser:
    """Optional mDNS/Zeroconf service advertiser for LAN auto-discovery."""

    def __init__(self, protocol: ProtocolConfig, host: str, port: int) -> None:
        self._protocol = protocol
        self._host = host
        self._port = port
        self._aiozc: Any = None
        self._service_info: Any = None

    async def start(self) -> bool:
        try:
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf

            ip_str = detect_local_ip() if self._host in ("0.0.0.0", "", "::") else self._host
            packed_ip = socket.inet_aton(ip_str)
            hostname = socket.gethostname().split(".")[0] or "gcs-host"
            service_type = self._protocol.mdns_service_type
            service_name = f"{self._protocol.mdns_service_name}-{hostname}.{service_type}"

            self._service_info = ServiceInfo(
                type_=service_type,
                name=service_name,
                addresses=[packed_ip],
                port=self._port,
                properties={
                    "protocol": self._protocol.protocol_version,
                    "hub_version": HUB_VERSION,
                    "ws_path": "/",
                },
                server=f"{hostname}.local.",
            )
            self._aiozc = AsyncZeroconf()
            await self._aiozc.async_register_service(self._service_info)
            logger.info("mDNS service registered: %s (%s:%d)", service_name, ip_str, self._port)
            return True
        except Exception as exc:
            logger.warning("mDNS advertisement unavailable (continuing with QR/manual IP): %s", exc)
            self._aiozc = None
            self._service_info = None
            return False

    async def stop(self) -> None:
        if self._aiozc is not None:
            try:
                if self._service_info is not None:
                    await self._aiozc.async_unregister_service(self._service_info)
                await self._aiozc.async_close()
            except Exception as exc:
                logger.debug("Ignoring mDNS shutdown error: %s", exc)
            finally:
                self._aiozc = None
                self._service_info = None


class GCSLocalHub:
    """Central local WebSocket Hub managing devices, authoritative avatar state, and handoffs."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        protocol: ProtocolConfig | None = None,
        handoff_timeout_seconds: float | None = None,
        enforce_timeout_bounds: bool = True,
        enable_mdns: bool = True,
    ) -> None:
        self.protocol = protocol or load_protocol_config()
        self.host = host if host is not None else self.protocol.default_host
        self.requested_port = port if port is not None else self.protocol.default_port
        self.actual_port: int = self.requested_port
        self.enable_mdns = enable_mdns

        self.state_store = AvatarStateStore(self.protocol)
        self.device_manager = DeviceManager(self.protocol)
        self.handoff_manager = HandoffManager(
            self.state_store,
            self.device_manager,
            self.protocol,
            timeout_seconds=handoff_timeout_seconds,
            enforce_timeout_bounds=enforce_timeout_bounds,
            on_timeout_cancelled=self._handle_handoff_timeout,
        )

        self._dedup = _MessageIdDeduplicator(max_entries=2048)
        self._server: Server | None = None
        self._mdns: _MDNSAdvertiser | None = None
        self._running: bool = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def ws_url(self) -> str:
        connect_host = "127.0.0.1" if self.host in ("0.0.0.0", "", "::") else self.host
        return f"ws://{connect_host}:{self.actual_port}"

    @property
    def pairing_uri(self) -> str:
        return build_pairing_uri(self.host, self.actual_port, self.protocol)

    async def start(self) -> None:
        """Start the WebSocket server and optional mDNS advertisement."""
        if self._running:
            return
        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.requested_port,
            ping_interval=self.protocol.heartbeat_interval_seconds,
            ping_timeout=self.protocol.heartbeat_timeout_seconds,
        )
        # Resolve actual port (especially useful when requested_port == 0 in tests)
        sockets = self._server.sockets
        if sockets:
            self.actual_port = int(sockets[0].getsockname()[1])
        self._running = True

        if self.enable_mdns:
            self._mdns = _MDNSAdvertiser(self.protocol, self.host, self.actual_port)
            await self._mdns.start()

        logger.info("GCS Local Hub listening on %s:%d (pairing: %s)", self.host, self.actual_port, self.pairing_uri)

    async def stop(self) -> None:
        """Gracefully stop mDNS, active handoff timers, and the WebSocket server."""
        if not self._running:
            return
        self._running = False
        await self.handoff_manager.shutdown()

        if self._mdns is not None:
            await self._mdns.stop()
            self._mdns = None

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info("GCS Local Hub stopped.")

    async def _send_envelope(self, websocket: ServerConnection | None, msg_type: str, payload: dict[str, Any]) -> bool:
        if websocket is None:
            return False
        raw = json.dumps(make_envelope(msg_type, payload))
        try:
            await websocket.send(raw)
            return True
        except Exception as exc:
            logger.debug("Failed to send '%s': %s", msg_type, exc)
            return False

    async def _send_error(
        self,
        websocket: ServerConnection,
        code: str,
        message: str,
        ref_message_id: str | None = None,
    ) -> None:
        await self._send_envelope(
            websocket,
            "error",
            {
                "code": code,
                "message": message,
                "ref_message_id": ref_message_id,
            },
        )

    async def _broadcast(
        self,
        msg_type: str,
        payload: dict[str, Any],
        *,
        exclude_device_id: str | None = None,
    ) -> None:
        tasks = []
        for dev in self.device_manager.connected_devices():
            if exclude_device_id and dev.device_id == exclude_device_id:
                continue
            if dev.websocket is not None:
                tasks.append(self._send_envelope(dev.websocket, msg_type, payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_device_list(self) -> None:
        await self._broadcast("device_list", {"devices": self.device_manager.list_devices_payload()})

    async def _broadcast_state_sync(self, exclude_device_id: str | None = None) -> None:
        await self._broadcast("state_sync", self.state_store.snapshot().to_dict(), exclude_device_id=exclude_device_id)

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        remote = getattr(websocket, "remote_address", None)
        logger.info("Client connected from %s", remote)
        try:
            async for raw_message in websocket:
                if not isinstance(raw_message, str):
                    await self._send_error(websocket, "INVALID_FRAME", "Only UTF-8 JSON text frames are supported.")
                    continue
                await self._dispatch_raw_message(websocket, raw_message)
        except ConnectionClosed:
            pass
        except Exception:
            logger.exception("Unexpected error in WebSocket connection handler for %s", remote)
        finally:
            await self._handle_disconnect(websocket)

    async def _handle_disconnect(self, websocket: ServerConnection) -> None:
        device = self.device_manager.disconnect_by_websocket(websocket)
        if device is None:
            return
        logger.info("Device disconnected: %s (%s)", device.device_id, device.device_type)

        # If a handoff involving this device was in flight, roll it back cleanly
        outcome = await self.handoff_manager.handle_device_disconnect(device.device_id)
        if outcome is not None:
            await self._notify_handoff_cancelled(outcome)

        await self._broadcast_device_list()

    async def _dispatch_raw_message(self, websocket: ServerConnection, raw_message: str) -> None:
        try:
            envelope = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            await self._send_error(websocket, "INVALID_JSON", f"Malformed JSON: {exc}")
            return

        if not isinstance(envelope, dict):
            await self._send_error(websocket, "INVALID_ENVELOPE", "Message must be a JSON object.")
            return

        msg_type = envelope.get("type")
        message_id = envelope.get("message_id")
        payload = envelope.get("payload")

        if not isinstance(msg_type, str) or not isinstance(payload, dict):
            await self._send_error(
                websocket,
                "INVALID_ENVELOPE",
                "Envelope must include string 'type' and object 'payload'.",
                ref_message_id=str(message_id) if message_id else None,
            )
            return

        if msg_type not in self.protocol.message_types:
            await self._send_error(
                websocket,
                "UNKNOWN_MESSAGE_TYPE",
                f"Unknown message type '{msg_type}'.",
                ref_message_id=str(message_id) if message_id else None,
            )
            return

        # Deduplicate retransmitted messages by (device_id/socket, message_id)
        device = self.device_manager.get_by_websocket(websocket)
        if isinstance(message_id, str) and message_id and msg_type not in ("ping", "pong"):
            dedup_scope = device.device_id if device is not None else str(id(websocket))
            if self._dedup.mark_and_check_duplicate(f"{dedup_scope}:{message_id}"):
                logger.debug("Ignored duplicate message_id=%s (%s) from %s", message_id, msg_type, dedup_scope)
                return

        if device is not None:
            self.device_manager.touch(device.device_id)

        if msg_type == "ping":
            await self._send_envelope(
                websocket,
                "pong",
                {"sent_at": payload.get("sent_at", time.time()), "received_at": time.time()},
            )
            return
        if msg_type == "pong":
            return

        if msg_type == "register_device":
            await self._on_register_device(websocket, payload, message_id)
            return

        # All remaining commands require prior registration
        if device is None:
            await self._send_error(
                websocket,
                "UNREGISTERED_CLIENT",
                "Send 'register_device' before other protocol messages.",
                ref_message_id=message_id,
            )
            return

        if msg_type == "state_update":
            await self._on_state_update(websocket, device.device_id, payload, message_id)
        elif msg_type == "avatar_event":
            await self._on_avatar_event(websocket, device.device_id, payload, message_id)
        elif msg_type == "handoff_request":
            await self._on_handoff_request(websocket, device.device_id, payload, message_id)
        elif msg_type == "handoff_confirm":
            await self._on_handoff_confirm(websocket, device.device_id, payload, message_id)
        else:
            await self._send_error(
                websocket,
                "UNHANDLED_CLIENT_MESSAGE",
                f"Message type '{msg_type}' is server-emitted only.",
                ref_message_id=message_id,
            )

    async def _on_register_device(
        self,
        websocket: ServerConnection,
        payload: dict[str, Any],
        message_id: str | None,
    ) -> None:
        try:
            device, superseded_ws = self.device_manager.register(
                device_id=str(payload.get("device_id", "")).strip(),
                device_type=str(payload.get("device_type", "")).strip().lower(),
                device_name=str(payload.get("device_name", "")).strip(),
                websocket=websocket,
                screen_width=int(payload.get("screen_width", 1920)),
                screen_height=int(payload.get("screen_height", 1080)),
                capabilities=list(payload.get("capabilities", [])),
            )
        except (DeviceError, ValueError, TypeError) as exc:
            await self._send_error(websocket, "REGISTER_FAILED", str(exc), ref_message_id=message_id)
            return

        # Close superseded stale socket if the same device reconnected cleanly
        if superseded_ws is not None:
            try:
                await superseded_ws.close(code=4001, reason="Superseded by reconnect")
            except Exception:
                pass

        # Assign first registered device as initial owner if avatar currently has no owner
        owner_assigned = self.state_store.assign_owner_if_unowned(device.device_id)
        snapshot = self.state_store.snapshot().to_dict()

        await self._send_envelope(
            websocket,
            "register_ack",
            {
                "device_id": device.device_id,
                "hub_version": HUB_VERSION,
                "protocol_version": self.protocol.protocol_version,
                "avatar_state": snapshot,
                "devices": self.device_manager.list_devices_payload(),
            },
        )
        await self._broadcast_device_list()
        if owner_assigned:
            await self._broadcast_state_sync()

        logger.info(
            "Registered device: %s (type=%s, name=%s, owner=%s)",
            device.device_id,
            device.device_type,
            device.device_name,
            self.state_store.owner,
        )

    async def _on_state_update(
        self,
        websocket: ServerConnection,
        device_id: str,
        payload: dict[str, Any],
        message_id: str | None,
    ) -> None:
        claimed_id = payload.get("device_id")
        if claimed_id and claimed_id != device_id:
            await self._send_error(
                websocket,
                "DEVICE_MISMATCH",
                "payload.device_id does not match registered connection.",
                ref_message_id=message_id,
            )
            return

        try:
            snapshot = self.state_store.update_from_owner(
                device_id,
                position=payload.get("position"),
                animation=payload.get("animation"),
                state=payload.get("state"),
                facing=payload.get("facing"),
            )
        except (NotOwnerError, HandoffStateError, AvatarStateError, ValueError, TypeError) as exc:
            await self._send_error(websocket, "STATE_UPDATE_REJECTED", str(exc), ref_message_id=message_id)
            # Re-sync caller with authoritative state to prevent drift
            await self._send_envelope(websocket, "state_sync", self.state_store.snapshot().to_dict())
            return

        await self._broadcast("state_sync", snapshot.to_dict(), exclude_device_id=device_id)

    async def _on_avatar_event(
        self,
        websocket: ServerConnection,
        device_id: str,
        payload: dict[str, Any],
        message_id: str | None,
    ) -> None:
        event_name = str(payload.get("event", "")).strip()
        if not event_name:
            await self._send_error(websocket, "INVALID_EVENT", "Missing event name.", ref_message_id=message_id)
            return
        await self._broadcast(
            "avatar_event",
            {
                "device_id": device_id,
                "event": event_name,
                "data": payload.get("data", {}),
            },
            exclude_device_id=device_id,
        )

    async def _on_handoff_request(
        self,
        websocket: ServerConnection,
        device_id: str,
        payload: dict[str, Any],
        message_id: str | None,
    ) -> None:
        source_id = str(payload.get("source_device_id") or device_id)
        if source_id != device_id:
            await self._send_error(
                websocket,
                "DEVICE_MISMATCH",
                "source_device_id must match the requesting device.",
                ref_message_id=message_id,
            )
            return

        try:
            outcome = await self.handoff_manager.initiate_handoff(
                source_device_id=device_id,
                target_device_id=payload.get("target_device_id"),
                edge=str(payload.get("edge", "right")),
                normalized_y=float(payload.get("normalized_y", 0.5)),
                idempotency_key=payload.get("idempotency_key"),
            )
        except HandoffError as exc:
            await self._send_error(websocket, exc.code, exc.message, ref_message_id=message_id)
            # Send authoritative state back so source does not remain hidden
            await self._send_envelope(websocket, "state_sync", self.state_store.snapshot().to_dict())
            return

        if outcome.duplicate:
            # Already handled; re-sync caller without duplicating notifications
            await self._send_envelope(websocket, "state_sync", outcome.avatar_snapshot.to_dict())
            return

        ho = outcome.handoff
        source_dev = self.device_manager.get(ho.source_device_id)
        target_dev = self.device_manager.get(ho.target_device_id)

        # Notify source device to temporarily hide the pet
        await self._send_envelope(
            source_dev.websocket if source_dev else None,
            "handoff_pending",
            {
                "handoff_id": ho.handoff_id,
                "source_device_id": ho.source_device_id,
                "target_device_id": ho.target_device_id,
                "expires_at": ho.expires_at,
                "timeout_seconds": ho.timeout_seconds,
            },
        )

        # Notify target device to prompt for confirmation
        await self._send_envelope(
            target_dev.websocket if target_dev else None,
            "handoff_offer",
            {
                "handoff_id": ho.handoff_id,
                "source_device_id": ho.source_device_id,
                "source_device_type": source_dev.device_type if source_dev else "unknown",
                "source_device_name": source_dev.device_name if source_dev else ho.source_device_id,
                "edge": ho.edge,
                "normalized_y": ho.normalized_y,
                "expires_at": ho.expires_at,
                "timeout_seconds": ho.timeout_seconds,
                "avatar_state": outcome.avatar_snapshot.to_dict(),
            },
        )

        await self._broadcast_state_sync()

    async def _on_handoff_confirm(
        self,
        websocket: ServerConnection,
        device_id: str,
        payload: dict[str, Any],
        message_id: str | None,
    ) -> None:
        handoff_id = str(payload.get("handoff_id", "")).strip()
        if not handoff_id:
            await self._send_error(websocket, "INVALID_HANDOFF_ID", "handoff_id is required.", ref_message_id=message_id)
            return

        try:
            outcome = await self.handoff_manager.confirm_handoff(
                handoff_id=handoff_id,
                device_id=device_id,
                accepted=bool(payload.get("accepted", True)),
                confirmation_source=str(payload.get("confirmation_source", "touch")),
                landing_position=payload.get("landing_position"),
            )
        except HandoffError as exc:
            await self._send_error(websocket, exc.code, exc.message, ref_message_id=message_id)
            return

        if outcome.duplicate:
            await self._send_envelope(websocket, "state_sync", outcome.avatar_snapshot.to_dict())
            return

        if outcome.completed:
            ho = outcome.handoff
            await self._broadcast(
                "handoff_complete",
                {
                    "handoff_id": ho.handoff_id,
                    "previous_owner": ho.source_device_id,
                    "new_owner": ho.target_device_id,
                    "edge": ho.edge,
                    "normalized_y": ho.normalized_y,
                    "confirmation_source": ho.confirmation_source,
                    "avatar_state": outcome.avatar_snapshot.to_dict(),
                },
            )
            await self._broadcast_state_sync()
        elif outcome.cancelled:
            await self._notify_handoff_cancelled(outcome)

    async def _handle_handoff_timeout(self, outcome: HandoffOutcome) -> None:
        await self._notify_handoff_cancelled(outcome)

    async def _notify_handoff_cancelled(self, outcome: HandoffOutcome) -> None:
        ho = outcome.handoff
        await self._broadcast(
            "handoff_cancel",
            {
                "handoff_id": ho.handoff_id,
                "reason": ho.cancel_reason or "cancelled",
                "restored_owner": outcome.avatar_snapshot.owner,
                "avatar_state": outcome.avatar_snapshot.to_dict(),
            },
        )
        await self._broadcast_state_sync()


async def _run_hub_cli(args: argparse.Namespace) -> None:
    hub = GCSLocalHub(
        host=args.host,
        port=args.port,
        handoff_timeout_seconds=args.handoff_timeout,
        enable_mdns=not args.no_mdns,
    )
    await hub.start()

    uri = hub.pairing_uri
    display_host = detect_local_ip() if args.host in ("0.0.0.0", "", "::") else args.host
    print("\n=== GCS Local Hub Started ===")
    print(f"WebSocket URL : ws://{display_host}:{hub.actual_port}")
    print(f"Pairing URI   : {uri}")
    if args.qr:
        print(render_pairing_qr_ascii(uri))
    if args.qr_png:
        saved = save_pairing_qr_png(uri, Path(args.qr_png))
        print(f"Saved QR PNG  : {saved}")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        await hub.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Run the GCS Shimeji Local Hub server.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket port (default: 8765)")
    parser.add_argument(
        "--handoff-timeout",
        type=float,
        default=6.0,
        help="Handoff confirmation timeout in seconds (5.0 - 8.0, default: 6.0)",
    )
    parser.add_argument("--no-mdns", action="store_true", help="Disable mDNS/Zeroconf advertisement")
    parser.add_argument("--qr", action="store_true", help="Print ASCII QR code for Android pairing")
    parser.add_argument("--qr-png", default="", help="Optional path to save pairing QR code as PNG")
    args = parser.parse_args()
    asyncio.run(_run_hub_cli(args))


if __name__ == "__main__":
    main()
