"""End-to-end WebSocket network tests for GCS Local Hub, reconnection,
message deduplication, QR pairing, and full two-phase handoffs over sockets.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import websockets

from server.hub import (
    GCSLocalHub,
    build_pairing_uri,
    render_pairing_qr_ascii,
    save_pairing_qr_png,
)
from shared.config_loader import make_envelope
from windows.network_client import EmbeddedHubRunner, WindowsHubClient


async def _send(ws: Any, msg_type: str, payload: dict[str, Any], message_id: str | None = None) -> str:
    env = make_envelope(msg_type, payload, message_id=message_id)
    await ws.send(json.dumps(env))
    return env["message_id"]


async def _recv_until(ws: Any, expected_type: str, timeout: float = 2.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Did not receive message of type '{expected_type}' within {timeout}s")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if msg.get("type") == expected_type:
            return msg


async def test_pairing_uri_and_qr_generation(tmp_path: Path) -> None:
    uri = build_pairing_uri("192.168.1.42", 8765)
    assert uri.startswith("gcs-shimeji://pair?")
    assert "192.168.1.42" in uri
    assert "8765" in uri

    ascii_qr = render_pairing_qr_ascii(uri)
    assert len(ascii_qr.strip()) > 20

    png_path = save_pairing_qr_png(uri, tmp_path / "pair.png")
    assert png_path.exists()
    assert png_path.stat().st_size > 100


async def test_websocket_registration_state_sync_and_owner_enforcement() -> None:
    hub = GCSLocalHub(host="127.0.0.1", port=0, enable_mdns=False)
    await hub.start()
    try:
        async with websockets.connect(hub.ws_url) as ws_win, websockets.connect(hub.ws_url) as ws_and:
            # Windows registers first -> becomes initial owner
            await _send(
                ws_win,
                "register_device",
                {
                    "device_id": "win-desktop",
                    "device_type": "windows",
                    "device_name": "Workstation",
                    "screen_width": 1920,
                    "screen_height": 1080,
                },
            )
            ack_win = await _recv_until(ws_win, "register_ack")
            assert ack_win["payload"]["avatar_state"]["owner"] == "win-desktop"

            # Android registers second -> observer only
            await _send(
                ws_and,
                "register_device",
                {
                    "device_id": "android-phone",
                    "device_type": "android",
                    "device_name": "Galaxy",
                    "screen_width": 1080,
                    "screen_height": 2400,
                },
            )
            ack_and = await _recv_until(ws_and, "register_ack")
            assert ack_and["payload"]["avatar_state"]["owner"] == "win-desktop"

            # Owner sends state_update -> Android receives state_sync
            await _send(
                ws_win,
                "state_update",
                {
                    "device_id": "win-desktop",
                    "position": {"x": 512.0, "y": 900.0},
                    "animation": "run",
                    "state": "ACTIVE",
                    "facing": "left",
                },
            )
            sync = await _recv_until(ws_and, "state_sync")
            assert sync["payload"]["owner"] == "win-desktop"
            assert sync["payload"]["animation"] == "run"
            assert sync["payload"]["position"]["x"] == 512.0

            # Non-owner Android tries to overwrite state -> rejected with error
            await _send(
                ws_and,
                "state_update",
                {
                    "device_id": "android-phone",
                    "animation": "sleep",
                },
            )
            err = await _recv_until(ws_and, "error")
            assert err["payload"]["code"] == "STATE_UPDATE_REJECTED"
            assert hub.state_store.snapshot().animation == "run"
    finally:
        await hub.stop()


async def test_websocket_end_to_end_handoff_and_timeout_rollback() -> None:
    hub = GCSLocalHub(
        host="127.0.0.1",
        port=0,
        handoff_timeout_seconds=0.25,
        enforce_timeout_bounds=False,
        enable_mdns=False,
    )
    await hub.start()
    try:
        async with websockets.connect(hub.ws_url) as ws_win, websockets.connect(hub.ws_url) as ws_and:
            await _send(ws_win, "register_device", {"device_id": "win-1", "device_type": "windows", "device_name": "PC"})
            await _recv_until(ws_win, "register_ack")
            await _send(ws_and, "register_device", {"device_id": "and-1", "device_type": "android", "device_name": "Phone"})
            await _recv_until(ws_and, "register_ack")

            # 1) Windows -> Android confirmed handoff
            await _send(
                ws_win,
                "handoff_request",
                {
                    "source_device_id": "win-1",
                    "edge": "right",
                    "normalized_y": 0.6,
                    "idempotency_key": "ws-ho-1",
                },
            )
            pending = await _recv_until(ws_win, "handoff_pending")
            offer = await _recv_until(ws_and, "handoff_offer")
            ho_id = offer["payload"]["handoff_id"]
            assert pending["payload"]["handoff_id"] == ho_id

            # Android confirms by touch
            await _send(
                ws_and,
                "handoff_confirm",
                {
                    "handoff_id": ho_id,
                    "device_id": "and-1",
                    "accepted": True,
                    "confirmation_source": "touch",
                    "landing_position": {"x": 120.0, "y": 800.0},
                },
            )
            comp_win = await _recv_until(ws_win, "handoff_complete")
            comp_and = await _recv_until(ws_and, "handoff_complete")
            assert comp_win["payload"]["new_owner"] == "and-1"
            assert comp_and["payload"]["new_owner"] == "and-1"
            assert hub.state_store.owner == "and-1"

            # 2) Android -> Windows unconfirmed handoff -> timeout rollback back to Android
            await _send(
                ws_and,
                "handoff_request",
                {
                    "source_device_id": "and-1",
                    "edge": "left",
                    "normalized_y": 0.5,
                    "idempotency_key": "ws-ho-timeout",
                },
            )
            offer2 = await _recv_until(ws_win, "handoff_offer")
            assert offer2["payload"]["source_device_id"] == "and-1"

            # Do NOT confirm -> wait for automatic timeout rollback
            cancel_and = await _recv_until(ws_and, "handoff_cancel", timeout=1.5)
            cancel_win = await _recv_until(ws_win, "handoff_cancel", timeout=1.5)
            assert cancel_and["payload"]["reason"] == "timeout"
            assert cancel_and["payload"]["restored_owner"] == "and-1"
            assert cancel_win["payload"]["restored_owner"] == "and-1"
            assert hub.state_store.owner == "and-1"
            assert hub.state_store.state == "ACTIVE"
    finally:
        await hub.stop()


async def test_reconnection_and_message_id_deduplication_and_disconnect_safety() -> None:
    hub = GCSLocalHub(
        host="127.0.0.1",
        port=0,
        handoff_timeout_seconds=1.5,
        enforce_timeout_bounds=False,
        enable_mdns=False,
    )
    await hub.start()
    try:
        # Windows connects and registers
        ws_win1 = await websockets.connect(hub.ws_url)
        await _send(ws_win1, "register_device", {"device_id": "win-1", "device_type": "windows", "device_name": "PC"})
        await _recv_until(ws_win1, "register_ack")

        # Duplicate message_id on state_update should only apply once (version bumps by 1, not 2)
        v_before = hub.state_store.version
        dup_id = "fixed-msg-id-999"
        await _send(ws_win1, "state_update", {"device_id": "win-1", "animation": "jump"}, message_id=dup_id)
        await _send(ws_win1, "state_update", {"device_id": "win-1", "animation": "sleep"}, message_id=dup_id)
        await _send(ws_win1, "ping", {"sent_at": 1.0})
        await _recv_until(ws_win1, "pong")
        assert hub.state_store.version == v_before + 1
        assert hub.state_store.snapshot().animation == "jump"

        # Same device_id reconnects before old socket closes -> old socket superseded cleanly
        ws_win2 = await websockets.connect(hub.ws_url)
        await _send(ws_win2, "register_device", {"device_id": "win-1", "device_type": "windows", "device_name": "PC"})
        ack2 = await _recv_until(ws_win2, "register_ack")
        assert ack2["payload"]["avatar_state"]["owner"] == "win-1"
        await ws_win1.close()
        await asyncio.sleep(0.05)
        assert hub.device_manager.is_connected("win-1") is True

        # Connect Android, start handoff, then Android abruptly disconnects -> rollback to Windows
        ws_and = await websockets.connect(hub.ws_url)
        await _send(ws_and, "register_device", {"device_id": "and-1", "device_type": "android", "device_name": "Phone"})
        await _recv_until(ws_and, "register_ack")

        await _send(ws_win2, "handoff_request", {"source_device_id": "win-1", "edge": "right"})
        await _recv_until(ws_and, "handoff_offer")
        assert hub.state_store.state == "HANDOFF_PENDING"

        await ws_and.close()
        cancel_msg = await _recv_until(ws_win2, "handoff_cancel", timeout=1.5)
        assert cancel_msg["payload"]["reason"] == "target_disconnected"
        assert cancel_msg["payload"]["restored_owner"] == "win-1"
        assert hub.state_store.owner == "win-1"
        assert hub.state_store.state == "ACTIVE"

        await ws_win2.close()
    finally:
        await hub.stop()


async def test_windows_hub_client_and_embedded_hub_handoff_and_reconnect() -> None:
    runner = EmbeddedHubRunner(host="127.0.0.1", port=0, enable_mdns=False)
    runner.start()
    win_client = WindowsHubClient(
        runner.hub.ws_url,
        device_id="win-app-1",
        device_name="WinApp",
    )
    try:
        win_client.start_background()
        # Wait until Windows client connects and registers
        for _ in range(40):
            if win_client.is_connected and runner.hub.state_store.owner == "win-app-1":
                break
            await asyncio.sleep(0.05)
        assert win_client.is_connected
        assert runner.hub.state_store.owner == "win-app-1"

        # Connect simulated Android peer and complete Windows -> Android -> Windows roundtrip
        async with websockets.connect(runner.hub.ws_url) as ws_and:
            await _send(ws_and, "register_device", {"device_id": "and-app-1", "device_type": "android", "device_name": "Pixel"})
            await _recv_until(ws_and, "register_ack")

            idem = win_client.send_handoff_request(edge="right", normalized_y=0.5)
            assert idem is not None
            offer = await _recv_until(ws_and, "handoff_offer")
            ho_id = offer["payload"]["handoff_id"]

            await _send(
                ws_and,
                "handoff_confirm",
                {
                    "handoff_id": ho_id,
                    "device_id": "and-app-1",
                    "accepted": True,
                    "confirmation_source": "touch",
                },
            )
            await _recv_until(ws_and, "handoff_complete")
            assert runner.hub.state_store.owner == "and-app-1"

            # Reverse handoff: Android -> Windows confirmed via WindowsHubClient
            await _send(ws_and, "handoff_request", {"source_device_id": "and-app-1", "edge": "left", "normalized_y": 0.4})
            received_offer_id: str | None = None
            for _ in range(40):
                for ev in win_client.drain_events():
                    if ev.event_type == "message" and ev.payload.get("type") == "handoff_offer":
                        received_offer_id = ev.payload["payload"]["handoff_id"]
                        break
                if received_offer_id:
                    break
                await asyncio.sleep(0.05)
            assert received_offer_id is not None

            assert win_client.send_handoff_confirm(
                handoff_id=received_offer_id,
                accepted=True,
                confirmation_source="gesture",
                landing_x=100.0,
                landing_y=500.0,
            )
            comp = await _recv_until(ws_and, "handoff_complete")
            assert comp["payload"]["new_owner"] == "win-app-1"
            assert comp["payload"]["confirmation_source"] == "gesture"
            assert runner.hub.state_store.owner == "win-app-1"
    finally:
        win_client.stop_background()
        runner.stop()

