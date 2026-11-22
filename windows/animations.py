"""Pixel-art sprite sheet loader and animation controller.

Enforces strict nearest-neighbor scaling (zero anti-aliasing) and computes
opaque alpha hit masks so the desktop window remains click-through outside
the character pixels.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from PIL import Image
import numpy as np

from shared.config_loader import AnimationSpec, AvatarConfig

logger = logging.getLogger(__name__)

# Original body palette of placeholder_spritesheet.png (kept untouched for eyes/outline/blush):
_BASE_BODY_RGB = (86, 198, 224)       # #56C6E0 main body
_BASE_HIGHLIGHT_RGB = (146, 232, 248) # #92E8F8 top highlight
_BASE_SHADE_RGB = (48, 138, 174)      # #308AAE bottom shade

# Unified 8-emotion pixel-art body palettes: (body, highlight, shade)
EMOTION_PALETTES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    "neutral": (_BASE_BODY_RGB, _BASE_HIGHLIGHT_RGB, _BASE_SHADE_RGB),
    "happy": ((255, 213, 79), (255, 241, 168), (212, 155, 26)),      # Sunny golden yellow
    "love": ((255, 110, 168), (255, 180, 210), (201, 59, 118)),      # Vibrant strawberry pink
    "excited": ((76, 232, 138), (166, 249, 197), (31, 164, 90)),     # Energetic emerald mint
    "scared": ((122, 148, 232), (185, 202, 252), (71, 94, 179)),     # Pale icy indigo
    "dizzy": ((180, 124, 255), (221, 194, 255), (123, 70, 199)),     # Swirly amethyst purple
    "angry": ((255, 77, 77), (255, 154, 154), (189, 34, 34)),        # Fiery crimson red
    "sleepy": ((136, 140, 224), (191, 194, 245), (85, 89, 168)),     # Calm night lavender
}


def recolor_sprite_for_emotion(image: Image.Image, emotion: str) -> Image.Image:
    """Apply an exact pixel-art palette swap to the pet's body colors for the given emotion.

    Preserves dark outlines, eyes, white specular highlights, and cheek accents untouched.
    """
    norm = str(emotion or "neutral").strip().lower()
    if norm == "neutral" or norm not in EMOTION_PALETTES:
        return image

    body_rgb, hi_rgb, sh_rgb = EMOTION_PALETTES[norm]
    arr = np.array(image.convert("RGBA"), copy=True)
    rgb = arr[:, :, :3]
    alpha_mask = arr[:, :, 3] > 0

    m_body = alpha_mask & (rgb[:, :, 0] == _BASE_BODY_RGB[0]) & (rgb[:, :, 1] == _BASE_BODY_RGB[1]) & (rgb[:, :, 2] == _BASE_BODY_RGB[2])
    m_hi = alpha_mask & (rgb[:, :, 0] == _BASE_HIGHLIGHT_RGB[0]) & (rgb[:, :, 1] == _BASE_HIGHLIGHT_RGB[1]) & (rgb[:, :, 2] == _BASE_HIGHLIGHT_RGB[2])
    m_sh = alpha_mask & (rgb[:, :, 0] == _BASE_SHADE_RGB[0]) & (rgb[:, :, 1] == _BASE_SHADE_RGB[1]) & (rgb[:, :, 2] == _BASE_SHADE_RGB[2])

    arr[m_body, 0], arr[m_body, 1], arr[m_body, 2] = body_rgb
    arr[m_hi, 0], arr[m_hi, 1], arr[m_hi, 2] = hi_rgb
    arr[m_sh, 0], arr[m_sh, 1], arr[m_sh, 2] = sh_rgb
    return Image.fromarray(arr, mode="RGBA")


@dataclass(frozen=True)
class RenderedFrame:
    """A single scaled pixel-art frame with its opaque hit-testing metadata."""

    image: Image.Image  # RGBA image scaled strictly via NEAREST
    width: int
    height: int
    opaque_rects: tuple[tuple[int, int, int, int], ...]  # Run-length merged (x, y, w, h) opaque bands
    bounding_box: tuple[int, int, int, int] | None  # (left, top, right, bottom) or None if fully transparent

    def contains_opaque_pixel(self, x: int, y: int) -> bool:
        """Return True if local coordinate (x, y) hits an opaque character pixel."""
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return False
        for rx, ry, rw, rh in self.opaque_rects:
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return True
        return False


def _compute_opaque_rects(
    image: Image.Image,
    alpha_threshold: int,
) -> tuple[tuple[tuple[int, int, int, int], ...], tuple[int, int, int, int] | None]:
    """Compute horizontal scanline spans of opaque pixels (alpha >= threshold).

    These spans directly convert into a crisp QRegion for click-through masking
    and allow exact hit-testing in headless environments without Qt.
    """
    rgba = image.convert("RGBA")
    width, height = rgba.size
    alpha_band = rgba.getchannel("A")
    raw_alpha = alpha_band.tobytes()

    rects: list[tuple[int, int, int, int]] = []
    min_x, min_y = width, height
    max_x, max_y = -1, -1

    for y in range(height):
        row_offset = y * width
        x = 0
        while x < width:
            if raw_alpha[row_offset + x] >= alpha_threshold:
                start_x = x
                x += 1
                while x < width and raw_alpha[row_offset + x] >= alpha_threshold:
                    x += 1
                run_w = x - start_x
                rects.append((start_x, y, run_w, 1))
                if start_x < min_x:
                    min_x = start_x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y + 1 > max_y:
                    max_y = y + 1
            else:
                x += 1

    bbox = (min_x, min_y, max_x, max_y) if max_x >= 0 else None
    return tuple(rects), bbox


class SpriteSheet:
    """Loads a pixel-art sprite sheet and provides nearest-neighbor scaled frames."""

    def __init__(self, config: AvatarConfig, scale: int | None = None) -> None:
        self.config = config
        self._scale = self._clamp_scale(scale if scale is not None else config.visual.default_scale)
        self._sheet: Image.Image = self._load_sheet()
        self._raw_frames: dict[str, list[Image.Image]] = {}
        self._scaled_cache: dict[tuple[str, int, str, int, str], RenderedFrame] = {}
        self._slice_all_animations()

    def _clamp_scale(self, scale: int) -> int:
        v = self.config.visual
        return max(v.min_scale, min(v.max_scale, int(scale)))

    def _load_sheet(self) -> Image.Image:
        path = self.config.visual.sprite_sheet
        if not path.exists():
            raise FileNotFoundError(
                f"Avatar sprite sheet not found at '{path}'. "
                "Generate or configure a valid sprite sheet in shared/avatar_config.json."
            )
        with Image.open(path) as img:
            sheet = img.convert("RGBA")
        return sheet

    def _slice_all_animations(self) -> None:
        fw = self.config.visual.frame_width
        fh = self.config.visual.frame_height
        sw, sh = self._sheet.size

        for anim_name, spec in self.config.animations.items():
            required_w = spec.frames * fw
            required_h = (spec.row + 1) * fh
            if required_w > sw or required_h > sh:
                raise ValueError(
                    f"Sprite sheet ({sw}x{sh}) is too small for animation '{anim_name}' "
                    f"(row={spec.row}, frames={spec.frames}, frame_size={fw}x{fh})."
                )
            frames: list[Image.Image] = []
            for col in range(spec.frames):
                box = (col * fw, spec.row * fh, (col + 1) * fw, (spec.row + 1) * fh)
                frames.append(self._sheet.crop(box))
            self._raw_frames[anim_name] = frames

    @property
    def scale(self) -> int:
        return self._scale

    @scale.setter
    def scale(self, new_scale: int) -> None:
        clamped = self._clamp_scale(new_scale)
        if clamped != self._scale:
            self._scale = clamped
            self._scaled_cache.clear()

    @property
    def scaled_width(self) -> int:
        return self.config.visual.frame_width * self._scale

    @property
    def scaled_height(self) -> int:
        return self.config.visual.frame_height * self._scale

    def get_frame(
        self,
        animation: str,
        frame_index: int,
        facing: str = "right",
        emotion: str = "neutral",
    ) -> RenderedFrame:
        """Return a nearest-neighbor scaled RenderedFrame for the requested animation, facing & emotion."""
        if animation not in self._raw_frames:
            raise KeyError(f"Unknown animation '{animation}'. Available: {sorted(self._raw_frames)}")
        frames = self._raw_frames[animation]
        idx = frame_index % len(frames)
        norm_facing = "left" if facing == "left" else "right"
        norm_emotion = emotion.lower() if isinstance(emotion, str) and emotion.lower() in EMOTION_PALETTES else "neutral"
        cache_key = (animation, idx, norm_facing, self._scale, norm_emotion)

        cached = self._scaled_cache.get(cache_key)
        if cached is not None:
            return cached

        raw = frames[idx]
        if norm_emotion != "neutral":
            raw = recolor_sprite_for_emotion(raw, norm_emotion)
        if norm_facing == "left":
            raw = raw.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

        target_size = (self.scaled_width, self.scaled_height)
        # Strict nearest-neighbor resampling preserves crisp pixel edges without smoothing.
        scaled = raw.resize(target_size, resample=Image.Resampling.NEAREST)
        rects, bbox = _compute_opaque_rects(scaled, self.config.visual.alpha_hit_threshold)
        rendered = RenderedFrame(
            image=scaled,
            width=target_size[0],
            height=target_size[1],
            opaque_rects=rects,
            bounding_box=bbox,
        )
        self._scaled_cache[cache_key] = rendered
        return rendered


class AnimationController:
    """Advances pixel animations according to configured FPS, looping, and transitions."""

    def __init__(
        self,
        config: AvatarConfig,
        scale: int | None = None,
        on_animation_complete: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.sprite_sheet = SpriteSheet(config, scale=scale)
        self._on_animation_complete = on_animation_complete

        self._current_name: str = "idle"
        self._frame_index: int = 0
        self._elapsed: float = 0.0
        self._facing: str = "right"
        self._emotion: str = "neutral"
        self._finished: bool = False

    @property
    def current_animation(self) -> str:
        return self._current_name

    @property
    def frame_index(self) -> int:
        return self._frame_index

    @property
    def facing(self) -> str:
        return self._facing

    @facing.setter
    def facing(self, value: str) -> None:
        if value in ("left", "right"):
            self._facing = value

    @property
    def emotion(self) -> str:
        return self._emotion

    @emotion.setter
    def emotion(self, value: str) -> None:
        norm = str(value or "neutral").strip().lower()
        self._emotion = norm if norm in EMOTION_PALETTES else "neutral"

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def frame_size(self) -> tuple[int, int]:
        return (self.sprite_sheet.scaled_width, self.sprite_sheet.scaled_height)

    def set_scale(self, scale: int) -> None:
        self.sprite_sheet.scale = scale

    def reload_avatar(self, new_config: AvatarConfig, scale: int | None = None) -> None:
        """Hot-swap the avatar visuals/config without touching desktop pet logic."""
        target_scale = scale if scale is not None else self.sprite_sheet.scale
        self.config = new_config
        self.sprite_sheet = SpriteSheet(new_config, scale=target_scale)
        if self._current_name not in new_config.animations:
            self._current_name = "idle"
        self._frame_index = 0
        self._elapsed = 0.0
        self._finished = False
        logger.info("Reloaded avatar visuals: %s", new_config.visual.avatar_id)

    def play(self, animation: str, *, restart: bool = False) -> None:
        """Switch to the specified animation."""
        if animation not in self.config.animations:
            logger.warning("Ignoring unknown animation '%s'; falling back to 'idle'", animation)
            animation = "idle"
        if animation == self._current_name and not restart:
            return
        self._current_name = animation
        self._frame_index = 0
        self._elapsed = 0.0
        self._finished = False

    def update(self, dt: float) -> RenderedFrame:
        """Advance animation timer by dt seconds and return the active frame."""
        if dt < 0:
            dt = 0.0
        spec: AnimationSpec = self.config.animations[self._current_name]
        frame_duration = 1.0 / max(0.1, spec.fps)
        self._elapsed += dt

        while self._elapsed >= frame_duration:
            self._elapsed -= frame_duration
            if self._frame_index + 1 < spec.frames:
                self._frame_index += 1
            elif spec.loop:
                self._frame_index = 0
            else:
                completed = self._current_name
                if not self._finished:
                    self._finished = True
                    if self._on_animation_complete is not None:
                        self._on_animation_complete(completed)
                if spec.next_animation and spec.next_animation in self.config.animations:
                    self.play(spec.next_animation, restart=True)
                    spec = self.config.animations[self._current_name]
                    frame_duration = 1.0 / max(0.1, spec.fps)
                else:
                    self._frame_index = spec.frames - 1
                    self._elapsed = 0.0
                    break

        return self.current_frame()

    def current_frame(self) -> RenderedFrame:
        """Return the current nearest-neighbor scaled RenderedFrame."""
        return self.sprite_sheet.get_frame(
            self._current_name,
            self._frame_index,
            self._facing,
            self._emotion,
        )
