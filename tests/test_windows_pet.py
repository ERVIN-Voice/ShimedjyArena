"""Tests for Stage 1: pixel graphics, nearest-neighbor scaling, animations,
click-through alpha masks, multi-monitor desktop bounds, dragging, and position persistence.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from PIL import Image

from shared.config_loader import load_avatar_config
from windows.animations import AnimationController, SpriteSheet
from windows.pet_engine import DesktopBounds, EdgeHandoffEvent, PetEngine, ScreenRect


REQUIRED_ANIMATIONS = {
    "idle",
    "walk",
    "run",
    "jump",
    "sleep",
    "grab",
    "handoff",
    "receive",
    "landing",
}


def test_avatar_config_contains_all_required_animations() -> None:
    cfg = load_avatar_config()
    assert cfg.visual.scaling_mode == "nearest"
    assert REQUIRED_ANIMATIONS.issubset(set(cfg.animations.keys()))
    assert cfg.visual.sprite_sheet.exists()


def _pixel_set(img: Image.Image) -> set[tuple[int, int, int, int]]:
    getter = getattr(img, "get_flattened_data", img.getdata)
    return set(getter())


def test_nearest_neighbor_scaling_preserves_exact_pixel_palette() -> None:
    cfg = load_avatar_config()
    with Image.open(cfg.visual.sprite_sheet) as raw_sheet:
        raw_colors = _pixel_set(raw_sheet.convert("RGBA"))

    sheet = SpriteSheet(cfg, scale=4)
    assert sheet.scaled_width == cfg.visual.frame_width * 4
    assert sheet.scaled_height == cfg.visual.frame_height * 4

    for anim in REQUIRED_ANIMATIONS:
        frame = sheet.get_frame(anim, 0, facing="right")
        scaled_colors = _pixel_set(frame.image)
        # Strict nearest-neighbor must never introduce blended/anti-aliased colors
        assert scaled_colors.issubset(raw_colors), f"Anti-aliased colors detected in '{anim}'"
        assert frame.bounding_box is not None
        # Corners outside the character must remain transparent (click-through)
        assert not frame.contains_opaque_pixel(0, 0)
        assert not frame.contains_opaque_pixel(frame.width - 1, 0)
        # Center of the mascot must be hit-testable
        cx = (frame.bounding_box[0] + frame.bounding_box[2]) // 2
        cy = (frame.bounding_box[1] + frame.bounding_box[3]) // 2
        assert frame.contains_opaque_pixel(cx, cy)


def test_animation_controller_transitions_and_hot_swap(tmp_path: Path) -> None:
    cfg = load_avatar_config()
    completed: list[str] = []
    ctrl = AnimationController(cfg, scale=3, on_animation_complete=completed.append)

    # One-shot receive -> landing -> idle chain
    ctrl.play("receive", restart=True)
    for _ in range(30):
        ctrl.update(0.05)
    assert "receive" in completed
    assert "landing" in completed
    assert ctrl.current_animation == "idle"

    # Hot-swap to a custom sprite sheet without changing controller logic
    alt_sheet_path = tmp_path / "alt_sheet.png"
    alt_img = Image.new("RGBA", (192, 288), (0, 0, 0, 0))
    for r in range(9):
        for c in range(6):
            for y in range(10, 22):
                for x in range(10, 22):
                    alt_img.putpixel((c * 32 + x, r * 32 + y), (255, 0, 128, 255))
    alt_img.save(alt_sheet_path)

    alt_cfg_path = tmp_path / "alt_avatar_config.json"
    raw = json.loads((Path(__file__).resolve().parent.parent / "shared" / "avatar_config.json").read_text("utf-8"))
    raw["avatar"]["id"] = "custom_pink_block"
    raw["avatar"]["sprite_sheet"] = str(alt_sheet_path)
    alt_cfg_path.write_text(json.dumps(raw), encoding="utf-8")

    ctrl.reload_avatar(load_avatar_config(alt_cfg_path))
    frame = ctrl.current_frame()
    assert (255, 0, 128, 255) in _pixel_set(frame.image)


def test_multi_monitor_bounds_and_outer_edge_detection() -> None:
    # Left monitor at negative coordinates (-1920..0), primary monitor at (0..1920)
    left_mon = ScreenRect(-1920, 0, 1920, 1080, name="DP-1", is_primary=False)
    right_mon = ScreenRect(0, 0, 1920, 1080, name="DP-2", is_primary=True)
    bounds = DesktopBounds([left_mon, right_mon])

    # Internal seam between left_mon (x=0) and right_mon (x=0) is NOT an outer edge
    edge, _ = bounds.detect_outer_edge(2.0, 500.0, 128, 128, margin_px=14)
    assert edge is None

    # Far left outer edge of left_mon IS detected
    edge_left, norm_y = bounds.detect_outer_edge(-1915.0, 540.0, 128, 128, margin_px=14)
    assert edge_left == "left"
    assert 0.45 <= norm_y <= 0.65

    # Far right outer edge of right_mon IS detected
    edge_right, _ = bounds.detect_outer_edge(1920 - 128 - 4, 400.0, 128, 128, margin_px=14)
    assert edge_right == "right"


def test_pet_engine_drag_edge_handoff_and_gravity(tmp_path: Path) -> None:
    cfg = load_avatar_config()
    bounds = DesktopBounds([ScreenRect(0, 0, 1280, 720, is_primary=True)])
    events: list[EdgeHandoffEvent] = []

    engine = PetEngine(
        cfg,
        bounds,
        pet_width=128,
        pet_height=128,
        state_file=tmp_path / "state.json",
        rng=random.Random(42),
        on_edge_handoff=events.append,
    )

    # Start dragging to right outer edge and hold beyond dwell time
    engine.start_drag(1280 - 128, 300)
    assert engine.state == "DRAGGING"
    assert engine.animation == "grab"

    for _ in range(45):
        engine.tick(1.0 / 60.0)

    assert len(events) == 1
    assert events[0].edge == "right"

    # Release mid-air -> gravity drops pet onto floor and transitions to landing
    engine.end_drag()
    assert engine.state == "ACTIVE"
    for _ in range(120):
        engine.tick(1.0 / 60.0)
    assert engine.y == pytest.approx(720 - 128, abs=1.0)


def test_pet_position_persistence_and_monitor_clamping(tmp_path: Path) -> None:
    cfg = load_avatar_config()
    state_file = tmp_path / "pet_state.json"

    two_monitors = DesktopBounds(
        [
            ScreenRect(0, 0, 1920, 1080, is_primary=True),
            ScreenRect(1920, 0, 1920, 1080, is_primary=False),
        ]
    )
    engine1 = PetEngine(cfg, two_monitors, 128, 128, state_file=state_file)
    engine1.set_position_immediate(2600.0, 600.0)
    engine1.facing = "left"
    assert engine1.save_position(scale=5) is True

    # Simulate restarting after disconnecting the second monitor -> clamps safely onto primary
    one_monitor = DesktopBounds([ScreenRect(0, 0, 1920, 1080, is_primary=True)])
    engine2 = PetEngine(cfg, one_monitor, 128, 128, state_file=state_file)
    loaded = engine2.load_saved_position()
    assert loaded is not None
    assert loaded["scale"] == 5
    assert engine2.facing == "left"
    assert 0.0 <= engine2.x <= 1920 - 128
    assert 0.0 <= engine2.y <= 1080 - 128
