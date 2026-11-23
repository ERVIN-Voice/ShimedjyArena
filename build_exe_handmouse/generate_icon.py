"""Generate a crisp multi-size reticle icon (`hand_mouse.ico`) for HandMouse-PC-Control-Windows.exe."""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw


def generate_icon(output_path: Path | None = None) -> Path:
    out = output_path or (Path(__file__).resolve().parent / "hand_mouse.ico")
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark slate circle background
    draw.ellipse((14, 14, size - 14, size - 14), fill=(15, 23, 42, 255), outline=(34, 211, 238, 255), width=14)
    # Inner confirmation green ring
    draw.ellipse((52, 52, size - 52, size - 52), outline=(52, 235, 138, 255), width=10)
    # Crosshair ticks
    c = size // 2
    draw.rounded_rectangle((c - 6, 28, c + 6, 86), radius=4, fill=(34, 211, 238, 255))
    draw.rounded_rectangle((c - 6, size - 86, c + 6, size - 28), radius=4, fill=(34, 211, 238, 255))
    draw.rounded_rectangle((28, c - 6, 86, c + 6), radius=4, fill=(34, 211, 238, 255))
    draw.rounded_rectangle((size - 86, c - 6, size - 28, c + 6), radius=4, fill=(34, 211, 238, 255))
    # Bright target dot
    draw.ellipse((c - 18, c - 18, c + 18, c + 18), fill=(255, 255, 255, 255), outline=(15, 23, 42, 255), width=4)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return out


if __name__ == "__main__":
    p = generate_icon()
    print(f"Generated icon: {p}")
