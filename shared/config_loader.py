"""Typed configuration loaders for shared/avatar_config.json and shared/protocol.json."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AVATAR_CONFIG_PATH = PROJECT_ROOT / "shared" / "avatar_config.json"
DEFAULT_PROTOCOL_PATH = PROJECT_ROOT / "shared" / "protocol.json"


@dataclass(frozen=True)
class AnimationSpec:
    """Configuration for a single animation row in the sprite sheet."""

    name: str
    row: int
    frames: int
    fps: float
    loop: bool = True
    next_animation: str | None = None


@dataclass(frozen=True)
class AvatarVisualConfig:
    """Sprite sheet geometry and rendering settings."""

    avatar_id: str
    name: str
    sprite_sheet: Path
    frame_width: int
    frame_height: int
    default_scale: int
    min_scale: int
    max_scale: int
    scaling_mode: str
    alpha_hit_threshold: int


@dataclass(frozen=True)
class BehaviorConfig:
    """Movement, physics, edge-handoff, and persistence settings."""

    tick_hz: int
    walk_speed_px_per_sec: float
    run_speed_px_per_sec: float
    gravity_px_per_sec2: float
    jump_velocity_px_per_sec: float
    smooth_follow_factor: float
    idle_to_sleep_seconds: float
    wander_min_seconds: float
    wander_max_seconds: float
    edge_handoff_margin_px: int
    edge_handoff_hold_seconds: float
    position_state_file: str


@dataclass(frozen=True)
class AvatarConfig:
    """Top-level loaded avatar configuration."""

    schema_version: str
    visual: AvatarVisualConfig
    animations: dict[str, AnimationSpec]
    behavior: BehaviorConfig


@dataclass(frozen=True)
class ProtocolConfig:
    """Loaded WebSocket protocol & network configuration."""

    protocol_name: str
    protocol_version: str
    default_host: str
    default_port: int
    mdns_service_type: str
    mdns_service_name: str
    heartbeat_interval_seconds: float
    heartbeat_timeout_seconds: float
    reconnect_initial_delay_seconds: float
    reconnect_max_delay_seconds: float
    qr_scheme: str
    handoff_default_timeout_seconds: float
    handoff_min_timeout_seconds: float
    handoff_max_timeout_seconds: float
    allowed_confirmation_sources: frozenset[str] = field(default_factory=frozenset)
    allowed_edges: frozenset[str] = field(default_factory=frozenset)
    device_types: frozenset[str] = field(default_factory=frozenset)
    avatar_states: frozenset[str] = field(default_factory=frozenset)
    animations: frozenset[str] = field(default_factory=frozenset)
    facing_values: frozenset[str] = field(default_factory=frozenset)
    emotions: frozenset[str] = field(default_factory=frozenset)
    message_types: frozenset[str] = field(default_factory=frozenset)


def resolve_project_path(raw_path: str | Path, base_dir: Path | None = None) -> Path:
    """Resolve a relative asset/config path against the project root or custom base directory."""
    path = Path(raw_path)
    if path.is_absolute():
        return path
    root = base_dir if base_dir is not None else PROJECT_ROOT
    return (root / path).resolve()


def load_avatar_config(path: str | Path | None = None) -> AvatarConfig:
    """Load and validate avatar_config.json."""
    config_path = Path(path) if path is not None else DEFAULT_AVATAR_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = json.load(fh)

    av = raw["avatar"]
    scaling_mode = str(av.get("scaling_mode", "nearest")).lower()
    if scaling_mode != "nearest":
        raise ValueError(f"Unsupported scaling_mode '{scaling_mode}'; pixel art requires 'nearest'.")

    sprite_sheet_path = resolve_project_path(av["sprite_sheet"])
    visual = AvatarVisualConfig(
        avatar_id=str(av["id"]),
        name=str(av["name"]),
        sprite_sheet=sprite_sheet_path,
        frame_width=int(av["frame_width"]),
        frame_height=int(av["frame_height"]),
        default_scale=int(av["default_scale"]),
        min_scale=int(av.get("min_scale", 1)),
        max_scale=int(av.get("max_scale", 10)),
        scaling_mode=scaling_mode,
        alpha_hit_threshold=int(av.get("alpha_hit_threshold", 16)),
    )

    animations: dict[str, AnimationSpec] = {}
    for anim_name, anim_raw in raw["animations"].items():
        animations[anim_name] = AnimationSpec(
            name=anim_name,
            row=int(anim_raw["row"]),
            frames=int(anim_raw["frames"]),
            fps=float(anim_raw["fps"]),
            loop=bool(anim_raw.get("loop", True)),
            next_animation=anim_raw.get("next"),
        )

    required_animations = {
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
    missing = required_animations - set(animations.keys())
    if missing:
        raise ValueError(f"avatar_config.json missing required animations: {sorted(missing)}")

    beh = raw["behavior"]
    behavior = BehaviorConfig(
        tick_hz=int(beh.get("tick_hz", 60)),
        walk_speed_px_per_sec=float(beh.get("walk_speed_px_per_sec", 95.0)),
        run_speed_px_per_sec=float(beh.get("run_speed_px_per_sec", 210.0)),
        gravity_px_per_sec2=float(beh.get("gravity_px_per_sec2", 1400.0)),
        jump_velocity_px_per_sec=float(beh.get("jump_velocity_px_per_sec", -520.0)),
        smooth_follow_factor=float(beh.get("smooth_follow_factor", 0.28)),
        idle_to_sleep_seconds=float(beh.get("idle_to_sleep_seconds", 30.0)),
        wander_min_seconds=float(beh.get("wander_min_seconds", 2.5)),
        wander_max_seconds=float(beh.get("wander_max_seconds", 6.5)),
        edge_handoff_margin_px=int(beh.get("edge_handoff_margin_px", 14)),
        edge_handoff_hold_seconds=float(beh.get("edge_handoff_hold_seconds", 0.35)),
        position_state_file=str(beh.get("position_state_file", ".gcs_shimeji_state.json")),
    )

    return AvatarConfig(
        schema_version=str(raw.get("schema_version", "1.0.0")),
        visual=visual,
        animations=animations,
        behavior=behavior,
    )


def load_protocol_config(path: str | Path | None = None) -> ProtocolConfig:
    """Load and validate protocol.json."""
    config_path = Path(path) if path is not None else DEFAULT_PROTOCOL_PATH
    with config_path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = json.load(fh)

    net = raw["network"]
    ho = raw["handoff"]
    enums = raw["enums"]

    return ProtocolConfig(
        protocol_name=str(raw["protocol_name"]),
        protocol_version=str(raw["protocol_version"]),
        default_host=str(net["default_host"]),
        default_port=int(net["default_port"]),
        mdns_service_type=str(net["mdns_service_type"]),
        mdns_service_name=str(net["mdns_service_name"]),
        heartbeat_interval_seconds=float(net["heartbeat_interval_seconds"]),
        heartbeat_timeout_seconds=float(net["heartbeat_timeout_seconds"]),
        reconnect_initial_delay_seconds=float(net["reconnect_initial_delay_seconds"]),
        reconnect_max_delay_seconds=float(net["reconnect_max_delay_seconds"]),
        qr_scheme=str(net["qr_scheme"]),
        handoff_default_timeout_seconds=float(ho["default_timeout_seconds"]),
        handoff_min_timeout_seconds=float(ho["min_timeout_seconds"]),
        handoff_max_timeout_seconds=float(ho["max_timeout_seconds"]),
        allowed_confirmation_sources=frozenset(ho["allowed_confirmation_sources"]),
        allowed_edges=frozenset(ho["allowed_edges"]),
        device_types=frozenset(enums["device_types"]),
        avatar_states=frozenset(enums["avatar_states"]),
        animations=frozenset(enums["animations"]),
        facing_values=frozenset(enums["facing"]),
        emotions=frozenset(enums.get("emotions", ["neutral", "happy", "love", "excited", "scared", "dizzy", "angry", "sleepy"])),
        message_types=frozenset(raw["messages"].keys()),
    )


def make_envelope(
    msg_type: str,
    payload: dict[str, Any],
    message_id: str | None = None,
) -> dict[str, Any]:
    """Create a wire-format GCS WebSocket protocol message envelope."""
    return {
        "type": msg_type,
        "message_id": message_id or uuid.uuid4().hex,
        "timestamp": time.time(),
        "payload": payload,
    }
