"""Unit & end-to-end tests for Browser Shimeji (cross-tab & cross-PC server)."""

from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path

import pytest
import websockets

from browser_pet.main import main as browser_pet_main
from browser_pet.room_state import MAX_PETS, RoomState
from browser_pet.server import BrowserPetServer
from build_exe_browser_pet.generate_icon import generate_icon


def test_room_state_ribbon_throws_anti_duplicate_and_disconnect_rescue() -> None:
    room = RoomState()
    t1, assigned1 = room.register_tab(tab_id="pc1", label="Офис ПК", pc_name="Windows")
    assert len(assigned1) == 1
    pet = assigned1[0]
    assert pet.owner_tab_id == "pc1"

    t2, assigned2 = room.register_tab(tab_id="pc2", label="Ноутбук", pc_name="Mac")
    t3, assigned3 = room.register_tab(tab_id="pc3", label="Телефон", pc_name="Android")
    assert assigned2 == [] and assigned3 == []

    # Circular left/right neighbors for pc1: left=pc3, right=pc2
    left_nb, right_nb = room.get_neighbors("pc1")
    assert left_nb is not None and left_nb.tab_id == "pc3"
    assert right_nb is not None and right_nb.tab_id == "pc2"

    # Non-owner cannot throw pet (atomic ownership)
    assert room.throw_pet(pet.pet_id, "pc2", edge="right") is None

    # Owner throws right -> arrives at pc2 from left edge with note & velocity
    res = room.throw_pet(
        pet.pet_id,
        "pc1",
        edge="right",
        ny=0.4,
        vx=900.0,
        vy=-250.0,
        emotion="excited",
        note_text="Пошли за кофе!",
    )
    assert res is not None
    assert res.to_tab_id == "pc2"
    assert res.entry_edge == "left"
    assert res.vx >= 460.0
    assert res.pet.owner_tab_id == "pc2"
    assert res.pet.emotion == "excited"
    assert res.note_text == "Пошли за кофе!"
    assert res.note_from == "Офис ПК"
    assert room.total_throws == 1

    # Reorder pc3 to the middle
    assert room.move_tab("pc3", -1) is True
    assert [t.tab_id for t in room.tabs_ordered()] == ["pc1", "pc3", "pc2"]

    # Disconnect pc2 -> pet is automatically rescued to another live tab!
    rescued = room.unregister_tab("pc2")
    assert len(rescued) == 1
    assert rescued[0].to_tab_id in ("pc1", "pc3")
    assert pet.owner_tab_id == rescued[0].to_tab_id

    # Spawn up to MAX_PETS
    for _ in range(MAX_PETS + 2):
        room.spawn_pet("pc1")
    assert room.pet_count == MAX_PETS

    # Ball transfer across edge
    ball = room.throw_ball("pc1", edge="right", ny=0.5, vx=780.0, vy=-180.0, color="#ffd166")
    assert ball is not None
    assert ball.to_tab_id == "pc3"
    assert ball.entry_edge == "left"


@pytest.mark.asyncio
async def test_browser_pet_server_http_and_two_pc_websocket_throw_and_rescue() -> None:
    server = BrowserPetServer(host="127.0.0.1", port=0)
    port = await server.start()
    try:
        # 1) HTTP static assets & API
        def _http_get(path: str) -> tuple[int, bytes]:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
                return resp.status, resp.read()

        code_html, body_html = await asyncio.to_thread(_http_get, "/")
        assert code_html == 200 and b"petCanvas" in body_html

        code_js, body_js = await asyncio.to_thread(_http_get, "/app.js")
        assert code_js == 200 and b"throw_pet" in body_js

        code_png, body_png = await asyncio.to_thread(_http_get, "/assets/spritesheet.png")
        assert code_png == 200 and body_png[:8] == b"\x89PNG\r\n\x1a\n"

        code_info, body_info = await asyncio.to_thread(_http_get, "/api/info")
        assert code_info == 200
        info = json.loads(body_info.decode("utf-8"))
        assert info["port"] == port

        # 2) Connect PC-A and PC-B over WebSocket
        ws_url = f"ws://127.0.0.1:{port}/ws"
        async with websockets.connect(ws_url) as ws_a, websockets.connect(ws_url) as ws_b:
            await ws_a.send(json.dumps({"type": "hello", "tab_id": "pc-a", "label": "ПК-А"}))
            await ws_b.send(json.dumps({"type": "hello", "tab_id": "pc-b", "label": "ПК-Б"}))

            pet_id = None
            for _ in range(12):
                msg = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=3.0))
                if msg.get("type") == "room_state" and len(msg.get("tabs", [])) == 2 and msg.get("pets"):
                    pet_id = msg["pets"][0]["pet_id"]
                    break
            assert pet_id is not None

            # PC-A throws pet right -> arrives on PC-B from left edge
            await ws_a.send(
                json.dumps(
                    {
                        "type": "throw_pet",
                        "pet_id": pet_id,
                        "edge": "right",
                        "ny": 0.5,
                        "vx": 880.0,
                        "vy": -210.0,
                        "note_text": "Лови Чубрика!",
                    }
                )
            )
            arrived_b = None
            for _ in range(12):
                msg = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=3.0))
                if msg.get("type") == "pet_arrive":
                    arrived_b = msg
                    break
            assert arrived_b is not None
            assert arrived_b["entry_edge"] == "left"
            assert arrived_b["note_text"] == "Лови Чубрика!"

            # PC-A also throws a bouncy ball -> arrives on PC-B
            await ws_a.send(
                json.dumps(
                    {
                        "type": "throw_ball",
                        "edge": "right",
                        "ny": 0.35,
                        "vx": 920.0,
                        "vy": -150.0,
                        "color": "#4cc9f0",
                    }
                )
            )
            ball_b = None
            for _ in range(12):
                msg = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=3.0))
                if msg.get("type") == "ball_arrive":
                    ball_b = msg
                    break
            assert ball_b is not None
            assert ball_b["entry_edge"] == "left"
            assert ball_b["color"] == "#4cc9f0"

            # Close PC-B -> pet is automatically rescued back onto PC-A!
            await ws_b.close()
            rescued_a = None
            for _ in range(12):
                msg = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=3.0))
                if msg.get("type") == "pet_arrive":
                    rescued_a = msg
                    break
            assert rescued_a is not None
            assert rescued_a["pet"]["owner_tab_id"] == "pc-a"
    finally:
        await server.stop()


def test_browser_pet_cli_self_test_and_icon_generation(tmp_path: Path) -> None:
    ico = generate_icon(tmp_path / "browser_pet.ico")
    assert ico.exists() and ico.stat().st_size > 1000

    log_file = tmp_path / "self_test.log"
    rc = browser_pet_main(["--self-test", "--self-test-log", str(log_file)])
    assert rc == 0
    text = log_file.read_text(encoding="utf-8")
    assert "SELF-TEST OK" in text and "cross_pc_throw=OK" in text
