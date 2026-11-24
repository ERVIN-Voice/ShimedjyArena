"""Single-port HTTP + WebSocket server for Browser Shimeji (cross-tab & cross-PC).

Serves the HTML5 Canvas companion app, spritesheet PNG, avatar config JSON,
and real-time ``/ws`` WebSocket portal relay on ONE TCP port (default 8787)
so opening ``http://<LAN-IP>:8787`` from any PC or phone in the network works
immediately with zero CORS issues and a single firewall rule.
"""

from __future__ import annotations

import asyncio
import http
import json
import logging
import socket
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from browser_pet import (
    DEFAULT_PORT,
    get_avatar_config_path,
    get_spritesheet_path,
    get_static_dir,
)
from browser_pet.room_state import RoomState

logger = logging.getLogger("browser_pet.server")

_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def detect_lan_ips() -> list[str]:
    """Return prioritized non-loopback LAN IPv4 addresses for this machine."""
    ips: list[str] = []

    # Primary outbound interface probe (never sends any real network traffic)
    for target in ("192.168.1.1", "10.0.0.1", "8.8.8.8"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((target, 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
        except OSError:
            pass
        finally:
            sock.close()

    # Additional host interfaces
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = str(info[4][0])
            if ip and not ip.startswith("127.") and not ip.startswith("169.254.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass

    return ips


def build_share_urls(port: int) -> list[str]:
    """Return shareable URLs (LAN URLs first, localhost fallback)."""
    urls = [f"http://{ip}:{port}" for ip in detect_lan_ips()]
    urls.append(f"http://127.0.0.1:{port}")
    return urls


class BrowserPetServer:
    """Async single-port HTTP + WebSocket server coordinating Browser Shimeji tabs."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        static_dir: Path | None = None,
        spritesheet_path: Path | None = None,
        avatar_config_path: Path | None = None,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._static_dir = (static_dir or get_static_dir()).resolve()
        self._spritesheet_path = (spritesheet_path or get_spritesheet_path()).resolve()
        self._avatar_config_path = (avatar_config_path or get_avatar_config_path()).resolve()
        self._on_state_change = on_state_change

        self.room = RoomState()
        self._server: Server | None = None
        self._sockets: dict[str, ServerConnection] = {}
        self._bound_port: int = port
        self._hostname = socket.gethostname()

    @property
    def bound_port(self) -> int:
        return self._bound_port

    @property
    def share_urls(self) -> list[str]:
        return build_share_urls(self._bound_port)

    @property
    def primary_url(self) -> str:
        return self.share_urls[0]

    async def start(self) -> int:
        """Start listening on ``self.host:self.port`` and return the bound port."""
        self._server = await serve(
            self._ws_handler,
            self.host,
            self.port,
            process_request=self._http_handler,
            ping_interval=20.0,
            ping_timeout=20.0,
            max_size=2 * 1024 * 1024,
        )
        for sock in self._server.sockets:
            addr = sock.getsockname()
            if len(addr) >= 2:
                self._bound_port = int(addr[1])
                break
        logger.info("Browser Shimeji server listening on http://%s:%d", self.host, self._bound_port)
        self._notify_gui()
        return self._bound_port

    async def stop(self) -> None:
        if self._server is not None:
            for ws in list(self._sockets.values()):
                try:
                    await ws.close()
                except Exception:
                    pass
            self._sockets.clear()
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("Browser Shimeji server stopped")

    def _notify_gui(self) -> None:
        if self._on_state_change is None:
            return
        try:
            snap = self.room.snapshot_for_tab(None)
            snap["urls"] = self.share_urls
            snap["port"] = self._bound_port
            self._on_state_change(snap)
        except Exception:
            logger.debug("GUI state callback failed", exc_info=True)

    # ------------------------------------------------------------------ HTTP
    def _make_response(
        self,
        status: http.HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> Response:
        headers = Headers(
            {
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
                "Cache-Control": "no-cache",
                "Access-Control-Allow-Origin": "*",
                "Connection": "close",
            }
        )
        if extra_headers:
            for k, v in extra_headers.items():
                headers[k] = v
        return Response(status, status.phrase, headers, body)

    async def _http_handler(
        self,
        connection: ServerConnection,
        request: Request,
    ) -> Response | None:
        """Serve HTTP assets on all paths except ``/ws`` (which upgrades to WebSocket)."""
        parsed = urlsplit(request.path)
        path = parsed.path or "/"

        # Let WebSocket upgrade proceed on /ws
        upgrade_hdr = request.headers.get("Upgrade", "").lower()
        if path == "/ws" or upgrade_hdr == "websocket":
            return None

        if path == "/api/info":
            payload = {
                "app": "Shimeji Browser Server",
                "server_pc": self._hostname,
                "port": self._bound_port,
                "urls": self.share_urls,
                "tabs": self.room.tab_count,
                "pets": self.room.pet_count,
                "total_throws": self.room.total_throws,
            }
            return self._make_response(
                http.HTTPStatus.OK,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        if path == "/assets/spritesheet.png":
            if self._spritesheet_path.exists():
                return self._make_response(
                    http.HTTPStatus.OK,
                    self._spritesheet_path.read_bytes(),
                    "image/png",
                )
            return self._make_response(http.HTTPStatus.NOT_FOUND, b"Missing spritesheet", "text/plain")

        if path == "/assets/avatar_config.json":
            if self._avatar_config_path.exists():
                return self._make_response(
                    http.HTTPStatus.OK,
                    self._avatar_config_path.read_bytes(),
                    "application/json; charset=utf-8",
                )
            return self._make_response(http.HTTPStatus.NOT_FOUND, b"{}", "application/json")

        # Serve static files from browser_pet/static/
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        candidate = (self._static_dir / rel).resolve()
        if self._static_dir in candidate.parents and candidate.is_file():
            ctype = _CONTENT_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
            return self._make_response(http.HTTPStatus.OK, candidate.read_bytes(), ctype)

        return self._make_response(
            http.HTTPStatus.NOT_FOUND,
            b"Not Found",
            "text/plain; charset=utf-8",
        )

    # ------------------------------------------------------------------ WebSocket
    async def _send_json(self, ws: ServerConnection, data: dict[str, Any]) -> None:
        try:
            await ws.send(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    async def _send_to_tab(self, tab_id: str, data: dict[str, Any]) -> None:
        ws = self._sockets.get(tab_id)
        if ws is not None:
            await self._send_json(ws, data)

    async def broadcast_room_state(self) -> None:
        """Send customized ``room_state`` (including left/right neighbors) to every tab."""
        tasks = []
        for tab_id, ws in list(self._sockets.items()):
            snap = self.room.snapshot_for_tab(tab_id)
            tasks.append(self._send_json(ws, snap))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._notify_gui()

    async def spawn_pet_from_server(self) -> bool:
        """Spawn an additional pet from the desktop server GUI."""
        pet = self.room.spawn_pet()
        if pet is None:
            return False
        if pet.owner_tab_id:
            await self._send_to_tab(
                pet.owner_tab_id,
                {
                    "type": "pet_arrive",
                    "pet": pet.to_dict(),
                    "entry_edge": "top",
                    "ny": 0.16,
                    "vx": 0.0,
                    "vy": 120.0,
                    "note_text": f"Привет! Я {pet.name}!",
                    "note_from": "Сервер",
                },
            )
        await self.broadcast_room_state()
        return True

    async def _ws_handler(self, ws: ServerConnection) -> None:
        remote_ip = ""
        try:
            if ws.remote_address:
                remote_ip = str(ws.remote_address[0])
        except Exception:
            pass

        tab_id: str | None = None
        try:
            async for raw in ws:
                if not isinstance(raw, str):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = str(msg.get("type", ""))

                if mtype == "hello":
                    tab, assigned = self.room.register_tab(
                        tab_id=msg.get("tab_id"),
                        label=str(msg.get("label", "")),
                        pc_name=str(msg.get("pc_name", "")),
                        browser=str(msg.get("browser", "")),
                        remote_ip=remote_ip,
                        width=int(msg.get("width", 1280)),
                        height=int(msg.get("height", 720)),
                    )
                    tab_id = tab.tab_id
                    self._sockets[tab_id] = ws
                    await self._send_json(
                        ws,
                        {
                            "type": "welcome",
                            "tab_id": tab_id,
                            "label": tab.label,
                            "server_pc": self._hostname,
                            "urls": self.share_urls,
                        },
                    )
                    for pet in assigned:
                        await self._send_json(
                            ws,
                            {
                                "type": "pet_arrive",
                                "pet": pet.to_dict(),
                                "entry_edge": "top",
                                "ny": 0.22,
                                "vx": 0.0,
                                "vy": 100.0,
                                "note_text": "Кидай меня за край в другую вкладку!",
                                "note_from": "",
                            },
                        )
                    await self.broadcast_room_state()
                    continue

                if tab_id is None:
                    continue

                if mtype == "rename_tab":
                    self.room.rename_tab(tab_id, str(msg.get("label", "")))
                    await self.broadcast_room_state()

                elif mtype == "resize":
                    self.room.resize_tab(tab_id, int(msg.get("width", 1280)), int(msg.get("height", 720)))

                elif mtype == "move_tab":
                    target_tid = str(msg.get("tab_id") or tab_id)
                    direction = int(msg.get("direction", 0))
                    if self.room.move_tab(target_tid, direction):
                        await self.broadcast_room_state()

                elif mtype == "pet_state":
                    self.room.update_pet_state(
                        str(msg.get("pet_id", "")),
                        tab_id,
                        nx=msg.get("nx"),
                        ny=msg.get("ny"),
                        vx=msg.get("vx"),
                        vy=msg.get("vy"),
                        facing=msg.get("facing"),
                        animation=msg.get("animation"),
                        emotion=msg.get("emotion"),
                    )

                elif mtype == "throw_pet":
                    res = self.room.throw_pet(
                        str(msg.get("pet_id", "")),
                        tab_id,
                        edge=str(msg.get("edge", "right")),
                        target_tab_id=msg.get("target_tab_id"),
                        ny=float(msg.get("ny", 0.55)),
                        vx=float(msg.get("vx", 700.0)),
                        vy=float(msg.get("vy", -220.0)),
                        emotion=msg.get("emotion"),
                        note_text=str(msg.get("note_text", "")),
                    )
                    if res is not None:
                        await self._send_to_tab(
                            res.from_tab_id,
                            {
                                "type": "pet_departed",
                                "pet_id": res.pet.pet_id,
                                "to_tab_id": res.to_tab_id,
                            },
                        )
                        await self._send_to_tab(
                            res.to_tab_id,
                            {
                                "type": "pet_arrive",
                                "pet": res.pet.to_dict(),
                                "entry_edge": res.entry_edge,
                                "ny": res.ny,
                                "vx": res.vx,
                                "vy": res.vy,
                                "note_text": res.note_text,
                                "note_from": res.note_from,
                            },
                        )
                        await self.broadcast_room_state()

                elif mtype == "summon_pet":
                    pid = str(msg.get("pet_id", ""))
                    if not pid and self.room.all_pets():
                        pid = self.room.all_pets()[0].pet_id
                    res = self.room.summon_pet(pid, tab_id)
                    if res is not None:
                        if res.from_tab_id:
                            await self._send_to_tab(
                                res.from_tab_id,
                                {
                                    "type": "pet_departed",
                                    "pet_id": res.pet.pet_id,
                                    "to_tab_id": res.to_tab_id,
                                },
                            )
                        await self._send_to_tab(
                            res.to_tab_id,
                            {
                                "type": "pet_arrive",
                                "pet": res.pet.to_dict(),
                                "entry_edge": "top",
                                "ny": 0.16,
                                "vx": 0.0,
                                "vy": 160.0,
                                "note_text": "Прилетел по зову! ✨",
                                "note_from": "",
                            },
                        )
                        await self.broadcast_room_state()

                elif mtype == "spawn_pet":
                    pet = self.room.spawn_pet(
                        owner_tab_id=tab_id,
                        name=str(msg.get("name", "")),
                        emotion=str(msg.get("emotion", "")),
                    )
                    if pet is not None:
                        await self._send_to_tab(
                            tab_id,
                            {
                                "type": "pet_arrive",
                                "pet": pet.to_dict(),
                                "entry_edge": "top",
                                "ny": 0.18,
                                "vx": 0.0,
                                "vy": 120.0,
                                "note_text": f"Привет! Я {pet.name}!",
                                "note_from": "",
                            },
                        )
                        await self.broadcast_room_state()

                elif mtype == "remove_pet":
                    pid = str(msg.get("pet_id", ""))
                    if self.room.remove_pet(pid):
                        await self.broadcast_room_state()

                elif mtype == "throw_ball":
                    bres = self.room.throw_ball(
                        tab_id,
                        edge=str(msg.get("edge", "right")),
                        target_tab_id=msg.get("target_tab_id"),
                        ny=float(msg.get("ny", 0.5)),
                        vx=float(msg.get("vx", 750.0)),
                        vy=float(msg.get("vy", -240.0)),
                        color=str(msg.get("color", "#ff6b6b")),
                    )
                    if bres is not None:
                        await self._send_to_tab(
                            bres.to_tab_id,
                            {
                                "type": "ball_arrive",
                                "ball_id": bres.ball_id,
                                "entry_edge": bres.entry_edge,
                                "ny": bres.ny,
                                "vx": bres.vx,
                                "vy": bres.vy,
                                "color": bres.color,
                                "from_label": (self.room.get_tab(tab_id).label if self.room.get_tab(tab_id) else ""),
                            },
                        )

                elif mtype == "ping":
                    await self._send_json(ws, {"type": "pong", "t": msg.get("t", 0)})

        except ConnectionClosed:
            pass
        except Exception:
            logger.exception("WebSocket handler error for tab %s", tab_id)
        finally:
            if tab_id is not None:
                if self._sockets.get(tab_id) is ws:
                    self._sockets.pop(tab_id, None)
                rescued = self.room.unregister_tab(tab_id)
                for res in rescued:
                    await self._send_to_tab(
                        res.to_tab_id,
                        {
                            "type": "pet_arrive",
                            "pet": res.pet.to_dict(),
                            "entry_edge": "top",
                            "ny": res.ny,
                            "vx": res.vx,
                            "vy": res.vy,
                            "note_text": res.note_text,
                            "note_from": res.note_from,
                        },
                    )
                await self.broadcast_room_state()


class ServerThread:
    """Runs ``BrowserPetServer`` on a dedicated daemon asyncio thread for GUI apps."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        on_state_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.server = BrowserPetServer(
            host=host,
            port=port,
            on_state_change=on_state_change,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None

    def start(self, timeout: float = 5.0) -> int:
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._run, name="browser-pet-server", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise TimeoutError("Timed out waiting for BrowserPetServer startup")
        if self._startup_error is not None:
            raise self._startup_error
        return self.server.bound_port

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self.server.start())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            return
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.run_until_complete(self.server.stop())
            except Exception:
                pass
            self._loop.close()

    def spawn_pet(self) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.server.spawn_pet_from_server(), self._loop)

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
