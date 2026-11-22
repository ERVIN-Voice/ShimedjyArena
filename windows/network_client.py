"""Windows WebSocket client and embedded Hub runner for GCS Shimeji."""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from server.hub import GCSLocalHub
from shared.config_loader import ProtocolConfig, load_protocol_config, make_envelope

logger = logging.getLogger(__name__)


@dataclass
class HubClientEvent:
    """Normalized event emitted by WindowsHubClient to the desktop pet layer."""

    event_type: str  # "connected" | "disconnected" | "message" | "error"
    payload: dict[str, Any]


class WindowsHubClient:
    """Asyncio WebSocket client for the Windows Shimeji with auto-reconnect and thread-safe API."""

    def __init__(
        self,
        hub_url: str,
        *,
        device_id: str | None = None,
        device_name: str | None = None,
        screen_width: int = 1920,
        screen_height: int = 1080,
        capabilities: list[str] | None = None,
        protocol: ProtocolConfig | None = None,
        on_event: Callable[[HubClientEvent], None] | None = None,
    ) -> None:
        self.hub_url = hub_url
        self.protocol = protocol or load_protocol_config()
        self.device_id = device_id or f"win-{uuid.getnode():x}"
        self.device_name = device_name or f"{platform.node() or 'Windows-PC'}"
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.capabilities = capabilities or ["desktop_pet", "mouse_drag", "click_confirm"]
        self._on_event = on_event

        self._connected: bool = False
        self._running: bool = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._send_queue: asyncio.Queue[str] | None = None
        self._events_inbox: queue.Queue[HubClientEvent] = queue.Queue()
        self._stop_event: asyncio.Event | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def drain_events(self) -> list[HubClientEvent]:
        """Pop all queued events safely from the GUI thread."""
        items: list[HubClientEvent] = []
        while True:
            try:
                items.append(self._events_inbox.get_nowait())
            except queue.Empty:
                break
        return items

    def _emit(self, event: HubClientEvent) -> None:
        self._events_inbox.put(event)
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                logger.exception("Error inside WindowsHubClient on_event callback")

    def start_background(self) -> None:
        """Start the asyncio client inside a dedicated daemon thread (for PySide6 integration)."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_thread_loop, name="gcs-win-ws", daemon=True)
        self._thread.start()

    def stop_background(self, timeout: float = 2.5) -> None:
        """Stop the background WebSocket client cleanly."""
        self._running = False
        loop = self._loop
        stop_ev = self._stop_event
        if loop is not None and stop_ev is not None and loop.is_running():
            loop.call_soon_threadsafe(stop_ev.set)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def _run_thread_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.run_forever())
        finally:
            loop.close()
            self._loop = None

    async def run_forever(self) -> None:
        """Asyncio main loop with exponential backoff reconnection."""
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._send_queue = asyncio.Queue(maxsize=256)
        self._stop_event = asyncio.Event()

        delay = self.protocol.reconnect_initial_delay_seconds
        max_delay = self.protocol.reconnect_max_delay_seconds

        while self._running and not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    self.hub_url,
                    ping_interval=self.protocol.heartbeat_interval_seconds,
                    ping_timeout=self.protocol.heartbeat_timeout_seconds,
                ) as ws:
                    self._connected = True
                    delay = self.protocol.reconnect_initial_delay_seconds
                    self._emit(HubClientEvent("connected", {"hub_url": self.hub_url}))

                    # Immediately register device
                    reg_msg = make_envelope(
                        "register_device",
                        {
                            "device_id": self.device_id,
                            "device_type": "windows",
                            "device_name": self.device_name,
                            "screen_width": self.screen_width,
                            "screen_height": self.screen_height,
                            "capabilities": self.capabilities,
                        },
                    )
                    await ws.send(json.dumps(reg_msg))

                    sender_task = asyncio.create_task(self._sender_loop(ws))
                    receiver_task = asyncio.create_task(self._receiver_loop(ws))
                    stop_task = asyncio.create_task(self._stop_event.wait())

                    done, pending = await asyncio.wait(
                        {sender_task, receiver_task, stop_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

                    if stop_task in done:
                        await ws.close()
                        break
            except (OSError, ConnectionClosed, asyncio.TimeoutError) as exc:
                logger.info("Hub connection closed/unavailable (%s); retrying in %.1fs", exc, delay)
                self._emit(HubClientEvent("error", {"message": str(exc)}))
            except Exception as exc:
                logger.exception("Unexpected WindowsHubClient error: %s", exc)
                self._emit(HubClientEvent("error", {"message": str(exc)}))
            finally:
                if self._connected:
                    self._connected = False
                    self._emit(HubClientEvent("disconnected", {}))

            if not self._running or self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                delay = min(max_delay, delay * 1.8)

    async def _sender_loop(self, ws: Any) -> None:
        assert self._send_queue is not None
        while True:
            raw = await self._send_queue.get()
            await ws.send(raw)

    async def _receiver_loop(self, ws: Any) -> None:
        async for raw in ws:
            if not isinstance(raw, str):
                continue
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self._emit(HubClientEvent("message", envelope))

    def send_message(self, msg_type: str, payload: dict[str, Any], message_id: str | None = None) -> bool:
        """Thread-safe enqueue of an outgoing protocol envelope."""
        loop = self._loop
        q = self._send_queue
        if not self._connected or loop is None or q is None or not loop.is_running():
            return False
        raw = json.dumps(make_envelope(msg_type, payload, message_id=message_id))

        def _enqueue() -> None:
            try:
                q.put_nowait(raw)
            except asyncio.QueueFull:
                # Drop oldest low-priority update when saturated
                try:
                    q.get_nowait()
                    q.put_nowait(raw)
                except Exception:
                    pass

        loop.call_soon_threadsafe(_enqueue)
        return True

    def send_state_update(
        self,
        *,
        x: float,
        y: float,
        animation: str,
        state: str,
        facing: str,
        emotion: str = "neutral",
    ) -> bool:
        return self.send_message(
            "state_update",
            {
                "device_id": self.device_id,
                "position": {"x": round(x, 2), "y": round(y, 2)},
                "animation": animation,
                "state": state,
                "facing": facing,
                "emotion": emotion,
            },
        )

    def send_handoff_request(
        self,
        *,
        edge: str,
        normalized_y: float,
        target_device_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> str | None:
        idem = idempotency_key or f"win-ho-{uuid.uuid4().hex[:12]}"
        payload: dict[str, Any] = {
            "source_device_id": self.device_id,
            "edge": edge,
            "normalized_y": max(0.0, min(1.0, normalized_y)),
            "idempotency_key": idem,
        }
        if target_device_id:
            payload["target_device_id"] = target_device_id
        ok = self.send_message("handoff_request", payload)
        return idem if ok else None

    def send_handoff_confirm(
        self,
        *,
        handoff_id: str,
        accepted: bool,
        confirmation_source: str = "click",
        landing_x: float | None = None,
        landing_y: float | None = None,
    ) -> bool:
        payload: dict[str, Any] = {
            "handoff_id": handoff_id,
            "device_id": self.device_id,
            "accepted": accepted,
            "confirmation_source": confirmation_source,
        }
        if landing_x is not None and landing_y is not None:
            payload["landing_position"] = {"x": round(landing_x, 2), "y": round(landing_y, 2)}
        return self.send_message("handoff_confirm", payload)


class EmbeddedHubRunner:
    """Runs GCSLocalHub on a background asyncio thread inside the Windows desktop app."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8765,
        handoff_timeout_seconds: float = 6.0,
        enable_mdns: bool = True,
    ) -> None:
        self.hub = GCSLocalHub(
            host=host,
            port=port,
            handoff_timeout_seconds=handoff_timeout_seconds,
            enable_mdns=enable_mdns,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self._startup_error: Exception | None = None

    def start(self, timeout: float = 5.0) -> None:
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._thread_main, name="gcs-local-hub", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise TimeoutError("Timed out starting embedded GCS Local Hub.")
        if self._startup_error is not None:
            raise self._startup_error

    def stop(self, timeout: float = 3.0) -> None:
        if self._loop is not None and self._stop_event is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._stop_event = asyncio.Event()
        try:
            loop.run_until_complete(self._serve())
        finally:
            loop.close()
            self._loop = None

    async def _serve(self) -> None:
        try:
            await self.hub.start()
        except Exception as exc:
            self._startup_error = exc
            self._ready.set()
            return
        self._ready.set()
        assert self._stop_event is not None
        await self._stop_event.wait()
        await self.hub.stop()
