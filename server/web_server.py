"""Zero-dependency local asyncio HTTP server for the GCS Shimeji Web Client.

Serves the mobile-first HTML5/Canvas companion app (``web/``), dynamic runtime
configuration (``/api/config``), and the currently configured avatar sprite sheet
PNG (``/assets/avatar/spritesheet.png``) on the local LAN so Android devices can
run the exact synced companion either inside the Android WebView app or directly
in a mobile browser with zero internet access.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from PIL import Image

from shared.config_loader import PROJECT_ROOT, load_avatar_config, load_protocol_config

logger = logging.getLogger("gcs.web_server")

WEB_DIR = PROJECT_ROOT / "web"

_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class LocalWebServer:
    """Embedded asyncio HTTP/1.1 server for the Android Web companion client."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8766,
        ws_port_provider: Callable[[], int],
        web_dir: Path | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._ws_port_provider = ws_port_provider
        self._web_dir = (web_dir or WEB_DIR).resolve()
        self._server: asyncio.AbstractServer | None = None

    @property
    def bound_port(self) -> int:
        if self._server and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self.port

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.host,
            port=self.port,
        )
        logger.info("GCS Web Client server listening on http://%s:%d", self.host, self.bound_port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("GCS Web Client server stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not raw_line:
                return
            request_line = raw_line.decode("iso-8859-1", errors="replace").strip()
            parts = request_line.split()
            if len(parts) < 2:
                await self._send_response(writer, 400, "Bad Request", b"Bad Request", "text/plain; charset=utf-8")
                return

            method = parts[0].upper()
            raw_target = parts[1]

            # Drain HTTP headers (bounded to prevent abuse)
            for _ in range(64):
                hdr = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if not hdr or hdr in (b"\r\n", b"\n"):
                    break

            if method not in ("GET", "HEAD"):
                await self._send_response(
                    writer,
                    405,
                    "Method Not Allowed",
                    b"Method Not Allowed",
                    "text/plain; charset=utf-8",
                    head_only=False,
                )
                return

            path = urlsplit(raw_target).path or "/"
            head_only = method == "HEAD"
            await self._dispatch(writer, path, head_only=head_only)
        except asyncio.TimeoutError:
            pass
        except Exception as exc:  # pragma: no cover - defensive socket error handling
            logger.debug("Web client request error: %s", exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch(self, writer: asyncio.StreamWriter, path: str, *, head_only: bool) -> None:
        if path == "/health":
            payload = json.dumps({"status": "ok", "ws_port": self._ws_port_provider(), "http_port": self.bound_port}).encode("utf-8")
            await self._send_response(writer, 200, "OK", payload, "application/json; charset=utf-8", head_only=head_only)
            return

        if path == "/api/config":
            payload = json.dumps(self._build_api_config()).encode("utf-8")
            await self._send_response(writer, 200, "OK", payload, "application/json; charset=utf-8", head_only=head_only)
            return

        if path == "/assets/avatar/icon.png":
            icon_bytes = self._build_app_icon_png()
            if icon_bytes is not None:
                await self._send_response(
                    writer,
                    200,
                    "OK",
                    icon_bytes,
                    "image/png",
                    head_only=head_only,
                )
                return
            await self._send_response(writer, 404, "Not Found", b"Icon not available", "text/plain; charset=utf-8", head_only=head_only)
            return

        if path == "/assets/avatar/spritesheet.png":
            avatar_cfg = load_avatar_config()
            sheet_path = avatar_cfg.visual.sprite_sheet
            if sheet_path.is_file():
                await self._send_response(
                    writer,
                    200,
                    "OK",
                    sheet_path.read_bytes(),
                    "image/png",
                    head_only=head_only,
                )
                return
            await self._send_response(writer, 404, "Not Found", b"Sprite sheet not found", "text/plain; charset=utf-8", head_only=head_only)
            return

        # Static web assets from web/
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        if ".." in rel or "\\" in rel:
            await self._send_response(writer, 403, "Forbidden", b"Forbidden", "text/plain; charset=utf-8", head_only=head_only)
            return

        candidate = (self._web_dir / rel).resolve()
        try:
            candidate.relative_to(self._web_dir)
        except ValueError:
            await self._send_response(writer, 403, "Forbidden", b"Forbidden", "text/plain; charset=utf-8", head_only=head_only)
            return

        if not candidate.is_file():
            await self._send_response(writer, 404, "Not Found", b"Not Found", "text/plain; charset=utf-8", head_only=head_only)
            return

        content_type = _CONTENT_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
        await self._send_response(
            writer,
            200,
            "OK",
            candidate.read_bytes(),
            content_type,
            head_only=head_only,
        )

    def _build_app_icon_png(self) -> bytes | None:
        avatar_cfg = load_avatar_config()
        sheet_path = avatar_cfg.visual.sprite_sheet
        if not sheet_path.is_file():
            return None
        fw = avatar_cfg.visual.frame_width
        fh = avatar_cfg.visual.frame_height
        idle = avatar_cfg.animations.get("idle")
        row = idle.row if idle is not None else 0
        with Image.open(sheet_path) as sheet:
            rgba = sheet.convert("RGBA")
            frame = rgba.crop((0, row * fh, fw, (row + 1) * fh))
        canvas = Image.new("RGBA", (192, 192), (15, 20, 32, 255))
        scaled = frame.resize((160, 160), Image.Resampling.NEAREST)
        canvas.alpha_composite(scaled, (16, 16))
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()

    def _build_api_config(self) -> dict[str, Any]:
        avatar_cfg = load_avatar_config()
        proto_cfg = load_protocol_config()
        animations_dict = {
            name: {
                "row": anim.row,
                "frames": anim.frames,
                "fps": anim.fps,
                "loop": anim.loop,
            }
            for name, anim in avatar_cfg.animations.items()
        }
        return {
            "protocol_version": proto_cfg.protocol_version,
            "ws_port": self._ws_port_provider(),
            "http_port": self.bound_port,
            "sprite_sheet_url": "/assets/avatar/spritesheet.png",
            "avatar_config": {
                "name": avatar_cfg.visual.name,
                "visual": {
                    "frame_width": avatar_cfg.visual.frame_width,
                    "frame_height": avatar_cfg.visual.frame_height,
                    "scale": avatar_cfg.visual.default_scale,
                    "scaling_mode": avatar_cfg.visual.scaling_mode,
                },
                "animations": animations_dict,
            },
        }

    @staticmethod
    async def _send_response(
        writer: asyncio.StreamWriter,
        status_code: int,
        reason: str,
        body: bytes,
        content_type: str,
        *,
        head_only: bool = False,
    ) -> None:
        headers = (
            f"HTTP/1.1 {status_code} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Cache-Control: no-cache, no-store, must-revalidate\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("ascii")
        writer.write(headers)
        if not head_only:
            writer.write(body)
        await writer.drain()
