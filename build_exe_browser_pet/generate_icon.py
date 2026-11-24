"""Generate crisp pixel-art .ico icon for Shimeji-Browser-Server-Windows.exe."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT_ICO = Path(__file__).resolve().parent / "browser_pet.ico"


def generate_icon(out_path: Path = OUT_ICO) -> Path:
    base = Image.new("RGBA", (32, 32), (16, 26, 48, 255))
    d = ImageDraw.Draw(base)
    # Neon portal borders (cyan left, pink right)
    d.rectangle((0, 0, 2, 31), fill=(76, 201, 240, 255))
    d.rectangle((29, 0, 31, 31), fill=(255, 110, 168, 255))
    # Cute pixel pet body
    d.rectangle((8, 9, 23, 24), fill=(86, 198, 224, 255))
    d.rectangle((10, 10, 21, 13), fill=(146, 232, 248, 255))
    d.rectangle((8, 22, 23, 24), fill=(48, 138, 174, 255))
    # Outline
    d.rectangle((7, 8, 24, 25), outline=(18, 24, 38, 255), width=1)
    # Eyes + blush
    d.rectangle((11, 14, 13, 17), fill=(18, 24, 38, 255))
    d.rectangle((18, 14, 20, 17), fill=(18, 24, 38, 255))
    d.point((12, 15), fill=(255, 255, 255, 255))
    d.point((19, 15), fill=(255, 255, 255, 255))
    d.rectangle((9, 18, 10, 19), fill=(255, 110, 168, 255))
    d.rectangle((21, 18, 22, 19), fill=(255, 110, 168, 255))

    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icons = [base.resize(s, Image.Resampling.NEAREST) for s in sizes]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    icons[-1].save(out_path, format="ICO", sizes=sizes)
    return out_path


if __name__ == "__main__":
    p = generate_icon()
    print(f"Generated icon: {p}")
