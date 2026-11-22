"""Generate a crisp placeholder 32x32 pixel-art sprite sheet for GCS Shimeji.

Layout (6 columns x 9 rows = 192x288 RGBA PNG, matching shared/avatar_config.json):
  Row 0: idle     (4 frames) - gentle breathing / blinking
  Row 1: walk     (6 frames) - cheerful stepping cycle
  Row 2: run      (6 frames) - tilted sprint with speed lines
  Row 3: jump     (4 frames) - crouch -> launch -> airborne -> peak
  Row 4: sleep    (4 frames) - curled up with rising pixel 'Z'
  Row 5: grab     (4 frames) - held by top, dangling feet
  Row 6: handoff  (6 frames) - glowing cyan portal beam / dissolve out
  Row 7: receive  (6 frames) - glowing emerald materialize in
  Row 8: landing  (4 frames) - squash -> bounce -> recover

This is an intentionally replaceable placeholder sprite sheet.
"""

from __future__ import annotations

from pathlib import Path
from PIL import Image

FRAME_W = 32
FRAME_H = 32
COLS = 6
ROWS = 9

# Strict retro pixel palette (RGBA, no anti-aliasing)
TRANSPARENT = (0, 0, 0, 0)
OUTLINE = (24, 28, 42, 255)
BODY_MAIN = (86, 198, 224, 255)
BODY_LIGHT = (146, 232, 248, 255)
BODY_SHADOW = (48, 138, 174, 255)
CHEEK = (248, 132, 164, 255)
EYE = (22, 24, 34, 255)
EYE_SHINE = (255, 255, 255, 255)
ANTENNA = (255, 206, 84, 255)
PORTAL_CYAN = (102, 252, 241, 220)
PORTAL_MINT = (114, 245, 166, 220)
ZZZ_COLOR = (232, 242, 255, 255)
SPEED_COLOR = (255, 230, 130, 210)


def _set_px(img: Image.Image, ox: int, oy: int, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if 0 <= x < FRAME_W and 0 <= y < FRAME_H:
        img.putpixel((ox + x, oy + y), color)


def _fill_rect(
    img: Image.Image,
    ox: int,
    oy: int,
    x0: int,
    y0: int,
    w: int,
    h: int,
    color: tuple[int, int, int, int],
) -> None:
    for yy in range(y0, y0 + h):
        for xx in range(x0, x0 + w):
            _set_px(img, ox, oy, xx, yy, color)


def _draw_pet_body(
    img: Image.Image,
    ox: int,
    oy: int,
    *,
    cx: int = 16,
    bottom_y: int = 29,
    body_w: int = 14,
    body_h: int = 13,
    left_leg_dy: int = 0,
    right_leg_dy: int = 0,
    eye_state: str = "open",
    tilt_x: int = 0,
    body_color: tuple[int, int, int, int] = BODY_MAIN,
    highlight_color: tuple[int, int, int, int] = BODY_LIGHT,
    shadow_color: tuple[int, int, int, int] = BODY_SHADOW,
    show_antenna: bool = True,
    dangling: bool = False,
) -> None:
    """Draw the temporary pixel mascot inside a 32x32 frame cell."""
    leg_h = 3 if not dangling else 5
    body_bottom = bottom_y - leg_h
    body_top = body_bottom - body_h
    left_x = cx - body_w // 2
    right_x = left_x + body_w - 1

    # Small pixel antenna on top
    if show_antenna:
        ax = cx + tilt_x
        _fill_rect(img, ox, oy, ax, body_top - 4, 2, 2, ANTENNA)
        _set_px(img, ox, oy, ax, body_top - 2, OUTLINE)
        _set_px(img, ox, oy, ax + 1, body_top - 2, OUTLINE)

    # Rounded pixel rectangle body + outline
    for y in range(body_top, body_bottom + 1):
        row_shift = tilt_x if y < (body_top + body_h // 2) else 0
        for x in range(left_x, right_x + 1):
            is_corner = (x in (left_x, right_x)) and (y in (body_top, body_bottom))
            if is_corner:
                continue
            px = x + row_shift
            is_border = (
                x in (left_x, right_x)
                or y in (body_top, body_bottom)
                or (x in (left_x + 1, right_x - 1) and y in (body_top, body_bottom))
            )
            if is_border:
                _set_px(img, ox, oy, px, y, OUTLINE)
            elif y <= body_top + 2:
                _set_px(img, ox, oy, px, y, highlight_color)
            elif y >= body_bottom - 2:
                _set_px(img, ox, oy, px, y, shadow_color)
            else:
                _set_px(img, ox, oy, px, y, body_color)

    # Eyes & cheeks
    eye_y = body_top + max(4, body_h // 2 - 1)
    ex1 = cx - 4 + tilt_x
    ex2 = cx + 2 + tilt_x
    if eye_state == "open":
        for ex in (ex1, ex2):
            _fill_rect(img, ox, oy, ex, eye_y, 2, 3, EYE)
            _set_px(img, ox, oy, ex, eye_y, EYE_SHINE)
    elif eye_state == "blink":
        _fill_rect(img, ox, oy, ex1, eye_y + 1, 2, 1, EYE)
        _fill_rect(img, ox, oy, ex2, eye_y + 1, 2, 1, EYE)
    elif eye_state == "sleep":
        _fill_rect(img, ox, oy, ex1, eye_y + 2, 3, 1, EYE)
        _fill_rect(img, ox, oy, ex2, eye_y + 2, 3, 1, EYE)
    elif eye_state == "surprised":
        for ex in (ex1, ex2):
            _fill_rect(img, ox, oy, ex, eye_y - 1, 2, 4, EYE)
            _set_px(img, ox, oy, ex, eye_y, EYE_SHINE)

    # Cheeks
    _fill_rect(img, ox, oy, ex1 - 2, eye_y + 3, 2, 1, CHEEK)
    _fill_rect(img, ox, oy, ex2 + 2, eye_y + 3, 2, 1, CHEEK)

    # Legs
    lx1 = cx - 4
    lx2 = cx + 2
    if dangling:
        _fill_rect(img, ox, oy, lx1 + left_leg_dy, body_bottom + 1, 2, 4, OUTLINE)
        _fill_rect(img, ox, oy, lx2 + right_leg_dy, body_bottom + 1, 2, 4, OUTLINE)
    else:
        _fill_rect(img, ox, oy, lx1, body_bottom + 1 + left_leg_dy, 3, 2, OUTLINE)
        _fill_rect(img, ox, oy, lx2, body_bottom + 1 + right_leg_dy, 3, 2, OUTLINE)


def _draw_z(img: Image.Image, ox: int, oy: int, x: int, y: int) -> None:
    for dx in range(4):
        _set_px(img, ox, oy, x + dx, y, ZZZ_COLOR)
        _set_px(img, ox, oy, x + dx, y + 3, ZZZ_COLOR)
    _set_px(img, ox, oy, x + 2, y + 1, ZZZ_COLOR)
    _set_px(img, ox, oy, x + 1, y + 2, ZZZ_COLOR)


def generate_placeholder_spritesheet(output_path: Path) -> Path:
    """Generate and save the placeholder pixel sprite sheet."""
    sheet = Image.new("RGBA", (COLS * FRAME_W, ROWS * FRAME_H), TRANSPARENT)

    # Row 0: idle (4 frames)
    for f in range(4):
        ox, oy = f * FRAME_W, 0 * FRAME_H
        squash = 1 if f == 2 else 0
        _draw_pet_body(
            sheet,
            ox,
            oy,
            bottom_y=30,
            body_w=14 + squash * 2,
            body_h=13 - squash,
            eye_state="blink" if f == 3 else "open",
        )

    # Row 1: walk (6 frames)
    walk_steps = [(-1, 0), (0, -1), (0, 0), (0, -1), (-1, 0), (0, 0)]
    for f in range(6):
        ox, oy = f * FRAME_W, 1 * FRAME_H
        l_dy, r_dy = walk_steps[f]
        bob = 1 if f in (1, 4) else 0
        _draw_pet_body(
            sheet,
            ox,
            oy,
            bottom_y=30 - bob,
            body_w=14,
            body_h=13,
            left_leg_dy=l_dy,
            right_leg_dy=r_dy,
            tilt_x=1 if f in (1, 2, 4, 5) else 0,
        )

    # Row 2: run (6 frames)
    for f in range(6):
        ox, oy = f * FRAME_W, 2 * FRAME_H
        bob = 2 if f % 2 == 1 else 0
        _draw_pet_body(
            sheet,
            ox,
            oy,
            cx=17,
            bottom_y=30 - bob,
            body_w=15,
            body_h=12,
            left_leg_dy=-1 if f % 2 == 0 else 0,
            right_leg_dy=0 if f % 2 == 0 else -1,
            tilt_x=2,
        )
        # Pixel speed lines behind
        _fill_rect(sheet, ox, oy, 2 + (f % 2), 16, 4, 1, SPEED_COLOR)
        _fill_rect(sheet, ox, oy, 3 + ((f + 1) % 2), 20, 3, 1, SPEED_COLOR)

    # Row 3: jump (4 frames)
    jump_specs = [
        (30, 16, 10, "open"),     # crouch
        (25, 12, 15, "surprised"),  # launch
        (21, 13, 14, "open"),     # midair
        (23, 14, 13, "open"),     # apex
    ]
    for f, (by, bw, bh, eye) in enumerate(jump_specs):
        ox, oy = f * FRAME_W, 3 * FRAME_H
        _draw_pet_body(sheet, ox, oy, bottom_y=by, body_w=bw, body_h=bh, eye_state=eye)

    # Row 4: sleep (4 frames)
    for f in range(4):
        ox, oy = f * FRAME_W, 4 * FRAME_H
        bh = 10 if f % 2 == 0 else 11
        _draw_pet_body(
            sheet,
            ox,
            oy,
            bottom_y=30,
            body_w=16,
            body_h=bh,
            eye_state="sleep",
            show_antenna=False,
        )
        _draw_z(sheet, ox, oy, 23, 9 - f)

    # Row 5: grab (4 frames)
    swing = [-1, 0, 1, 0]
    for f in range(4):
        ox, oy = f * FRAME_W, 5 * FRAME_H
        _draw_pet_body(
            sheet,
            ox,
            oy,
            cx=16 + swing[f],
            bottom_y=27,
            body_w=13,
            body_h=14,
            left_leg_dy=swing[f],
            right_leg_dy=-swing[f],
            eye_state="surprised",
            dangling=True,
        )

    # Row 6: handoff (6 frames) — shrinking into cyan warp beam
    for f in range(6):
        ox, oy = f * FRAME_W, 6 * FRAME_H
        beam_w = max(2, 16 - f * 2)
        _fill_rect(sheet, ox, oy, 16 - beam_w // 2, 4, beam_w, 24, PORTAL_CYAN)
        if f < 4:
            _draw_pet_body(
                sheet,
                ox,
                oy,
                cx=16 + f,
                bottom_y=28 - f,
                body_w=max(8, 14 - f * 2),
                body_h=max(8, 13 - f),
                eye_state="surprised",
            )

    # Row 7: receive (6 frames) — materializing from mint portal beam
    for f in range(6):
        ox, oy = f * FRAME_W, 7 * FRAME_H
        beam_w = max(2, 14 - abs(f - 2) * 3)
        _fill_rect(sheet, ox, oy, 16 - beam_w // 2, 4, beam_w, 24, PORTAL_MINT)
        if f >= 1:
            growth = min(4, f)
            _draw_pet_body(
                sheet,
                ox,
                oy,
                cx=16,
                bottom_y=24 + growth,
                body_w=8 + growth + 2,
                body_h=9 + growth,
                eye_state="open" if f >= 3 else "surprised",
            )

    # Row 8: landing (4 frames) — squash & recover
    land_specs = [
        (30, 18, 9),
        (30, 16, 11),
        (29, 13, 14),
        (30, 14, 13),
    ]
    for f, (by, bw, bh) in enumerate(land_specs):
        ox, oy = f * FRAME_W, 8 * FRAME_H
        _draw_pet_body(sheet, ox, oy, bottom_y=by, body_w=bw, body_h=bh, eye_state="open")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="PNG")
    return output_path


if __name__ == "__main__":
    default_out = Path(__file__).resolve().parent / "placeholder_spritesheet.png"
    out = generate_placeholder_spritesheet(default_out)
    print(f"Generated placeholder sprite sheet: {out} ({COLS * FRAME_W}x{ROWS * FRAME_H})")
