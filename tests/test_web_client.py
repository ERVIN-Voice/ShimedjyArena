"""Integration tests for the embedded local Web Companion server (server/web_server.py + web/)."""

from __future__ import annotations

import asyncio
import json

import pytest

from server.hub import GCSLocalHub


async def _http_get(host: str, port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        req = f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode("ascii")
        writer.write(req)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=5.0)
    finally:
        writer.close()
        await writer.wait_closed()

    header_blob, _, body = raw.partition(b"\r\n\r\n")
    lines = header_blob.decode("iso-8859-1").split("\r\n")
    status_code = int(lines[0].split()[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status_code, headers, body


@pytest.mark.asyncio
async def test_hub_serves_android_web_client_assets_and_api_config() -> None:
    hub = GCSLocalHub(host="127.0.0.1", port=0, enable_mdns=False, enable_web=True)
    await hub.start()
    try:
        assert hub.actual_port > 0
        assert hub.actual_web_port > 0
        assert f"web_port={hub.actual_web_port}" in hub.pairing_uri
        assert hub.web_url == f"http://127.0.0.1:{hub.actual_web_port}"

        # 1) HTML shell
        status, headers, body = await _http_get("127.0.0.1", hub.actual_web_port, "/")
        assert status == 200
        assert "text/html" in headers.get("content-type", "")
        html = body.decode("utf-8")
        assert "GCS Shimeji" in html
        assert "/app.js" in html

        # 2) CSS enforces nearest-neighbor pixel rendering
        status, headers, body = await _http_get("127.0.0.1", hub.actual_web_port, "/style.css")
        assert status == 200
        assert "text/css" in headers.get("content-type", "")
        assert "image-rendering: pixelated" in body.decode("utf-8")

        # 3) JS client implements exact Hub protocol & Android WebView bridge
        status, headers, body = await _http_get("127.0.0.1", hub.actual_web_port, "/app.js")
        assert status == 200
        js = body.decode("utf-8")
        assert "register_device" in js
        assert "handoff_confirm" in js
        assert "GcsAndroidBridge" in js

        # 4) Dynamic /api/config points to the live WebSocket port and avatar animations
        status, headers, body = await _http_get("127.0.0.1", hub.actual_web_port, "/api/config")
        assert status == 200
        assert "application/json" in headers.get("content-type", "")
        cfg = json.loads(body.decode("utf-8"))
        assert cfg["ws_port"] == hub.actual_port
        assert cfg["http_port"] == hub.actual_web_port
        assert cfg["sprite_sheet_url"] == "/assets/avatar/spritesheet.png"
        assert "idle" in cfg["avatar_config"]["animations"]
        assert "receive" in cfg["avatar_config"]["animations"]

        # 5) Active sprite sheet PNG + dynamic 192x192 PWA icon served cleanly
        status, headers, body = await _http_get("127.0.0.1", hub.actual_web_port, "/assets/avatar/spritesheet.png")
        assert status == 200
        assert headers.get("content-type") == "image/png"
        assert body.startswith(b"\x89PNG\r\n\x1a\n")

        status, headers, body = await _http_get("127.0.0.1", hub.actual_web_port, "/assets/avatar/icon.png")
        assert status == 200
        assert headers.get("content-type") == "image/png"
        assert body.startswith(b"\x89PNG\r\n\x1a\n")

        status, headers, body = await _http_get("127.0.0.1", hub.actual_web_port, "/manifest.json")
        assert status == 200
        manifest = json.loads(body.decode("utf-8"))
        assert manifest["display"] == "standalone"
        assert manifest["icons"][0]["src"] == "/assets/avatar/icon.png"

        # 6) Health endpoint + path traversal protection
        status, _, body = await _http_get("127.0.0.1", hub.actual_web_port, "/health")
        assert status == 200
        assert json.loads(body.decode("utf-8"))["status"] == "ok"

        status, _, _ = await _http_get("127.0.0.1", hub.actual_web_port, "/../server/hub.py")
        assert status in (403, 404)
    finally:
        await hub.stop()
