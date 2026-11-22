"""Generate a crisp multi-resolution pixel-art Windows .ico from frame 0 of the sprite sheet."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SPRITESHEET_PATH = REPO_ROOT / "assets" / "avatar" / "placeholder_spritesheet.png"
DEFAULT_OUTPUT = REPO_ROOT / "build_exe" / "gcs_shimeji.ico"


def generate_icon(output_path: Path = DEFAULT_OUTPUT) -> Path:
    if not SPRITESHEET_PATH.exists():
        raise FileNotFoundError(f"Spritesheet not found: {SPRITESHEET_PATH}")

    sheet = Image.open(SPRITESHEET_PATH).convert("RGBA")
    # Frame 0 is top-left 32x32 cell
    frame0 = sheet.crop((0, 0, 32, 32))
    # Upscale cleanly with nearest-neighbor to 256x256 so pixel art stays razor-sharp
    base = frame0.resize((256, 256), Image.Resampling.NEAREST)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(output_path, format="ICO", sizes=sizes)
    return output_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    saved = generate_icon(out)
    print(f"Generated Windows icon: {saved} ({saved.stat().st_size} bytes)")
