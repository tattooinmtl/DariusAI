"""Regenerate every app icon from the one source image, brain.png.

Run: .venv\\Scripts\\python.exe tools/make_icons.py

brain.png is a glowing brain rendered on solid black at 1402x1122. Used
as-is it looks like a black postage stamp everywhere Windows shows it — a
black square on the taskbar, a black square in the tray, a black square on
the desktop shortcut. So this script lifts the brain off its background:

* alpha comes from luminance, ramped over a narrow band just above black,
  which gives a soft anti-aliased edge and keeps the warm rim-light glow as
  a translucent halo instead of a hard cut-out;
* interior shadows (the cerebellum, the fissures) would go semi-transparent
  under that rule alone and punch holes through the middle, so anything the
  background can't reach by flooding in from the border is forced opaque;
* the result is cropped to the brain and padded back to a square with a
  small margin, because Windows scales icons to their full box and an
  off-centre subject reads as a mistake at 16px.

Outputs (both committed, so nothing has to run at install time):
  src/dariusai/viz/static/favicon.png  — browser tab + pystray tray icon
  src/dariusai/viz/static/brain.ico    — window/taskbar icon, and the
                                         IconLocation of every shortcut
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "brain.png"
STATIC = ROOT / "src" / "dariusai" / "viz" / "static"

# Luminance band (0-255) over which the background fades up into the image.
ALPHA_FLOOR = 10
ALPHA_CEIL = 55
# Luma at or below this is "background" for the purposes of flood filling.
BACKGROUND_LUMA = 26
MARGIN = 0.04  # padding around the subject, as a fraction of the square
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _luma(image: Image.Image) -> Image.Image:
    return image.convert("L")


def _outside_mask(luma: Image.Image) -> set[int]:
    """Flood fill dark pixels inward from the border. What it reaches is the
    real background; dark pixels it can't reach are shadows *inside* the
    brain and must stay opaque."""
    width, height = luma.size
    pixels = luma.load()
    outside: set[int] = set()
    queue: deque[tuple[int, int]] = deque()

    def push(x: int, y: int) -> None:
        index = y * width + x
        if index in outside or pixels[x, y] > BACKGROUND_LUMA:
            return
        outside.add(index)
        queue.append((x, y))

    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)

    while queue:
        x, y = queue.popleft()
        if x > 0:
            push(x - 1, y)
        if x < width - 1:
            push(x + 1, y)
        if y > 0:
            push(x, y - 1)
        if y < height - 1:
            push(x, y + 1)
    return outside


def cutout(source: Image.Image) -> Image.Image:
    rgb = source.convert("RGB")
    luma = _luma(rgb)
    width, height = luma.size
    outside = _outside_mask(luma)

    luma_pixels = luma.load()
    alpha = Image.new("L", (width, height))
    alpha_pixels = alpha.load()
    span = ALPHA_CEIL - ALPHA_FLOOR
    for y in range(height):
        row = y * width
        for x in range(width):
            if row + x not in outside:
                alpha_pixels[x, y] = 255  # interior: always solid
                continue
            value = luma_pixels[x, y]
            if value <= ALPHA_FLOOR:
                alpha_pixels[x, y] = 0
            elif value >= ALPHA_CEIL:
                alpha_pixels[x, y] = 255
            else:
                alpha_pixels[x, y] = int(255 * (value - ALPHA_FLOOR) / span)

    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def square(image: Image.Image) -> Image.Image:
    """Crop to what's actually visible, then centre it in a square canvas."""
    box = image.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    subject = image.crop(box) if box else image
    side = int(max(subject.size) * (1 + 2 * MARGIN))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(subject, ((side - subject.width) // 2, (side - subject.height) // 2))
    return canvas


def main() -> None:
    art = square(cutout(Image.open(SOURCE)))

    favicon = art.resize((256, 256), Image.LANCZOS)
    favicon.save(STATIC / "favicon.png")

    # Pillow's multi-size .ico writer downsamples from the image it's given;
    # feeding it the 256px master keeps every size sharp.
    favicon.save(STATIC / "brain.ico", sizes=[(s, s) for s in ICO_SIZES])

    print(f"wrote {STATIC / 'favicon.png'} and {STATIC / 'brain.ico'} from {SOURCE.name}")


if __name__ == "__main__":
    main()
