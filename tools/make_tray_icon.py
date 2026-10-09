"""Draw the tray icon: a pink brain with a yellow "D" (for Darius) outlined
in orange-red.

Run: .venv\\Scripts\\python.exe tools/make_tray_icon.py

Drawn, not traced from a picture: it has to read at 16 px in the
notification area, where a detailed render turns to mush. So the shapes are
few and bold — two lobes with a handful of dark folds, and a heavy letter
that covers most of the brain — drawn at 512 px and downsampled.

Outputs (committed, so nothing runs at install time):
  src/dariusai/viz/static/tray.png  — the pystray image (256 px)
  src/dariusai/viz/static/tray.ico  — 16/24/32/48/64/256 for anything else
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "dariusai" / "viz" / "static"
S = 512

PINK = (244, 160, 190, 255)
PINK_LIGHT = (255, 205, 222, 255)
FOLD = (196, 86, 128, 255)
YELLOW = (255, 214, 10, 255)
OUTLINE = (232, 72, 24, 255)      # orange-red


def font(size: int):
    for name in ("seguibl.ttf", "ariblk.ttf", "impact.ttf", "segoeuib.ttf"):
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def brain_mask() -> Image.Image:
    """Two hemispheres with a bumpy rim — the bumps are what make it read
    as a brain rather than a cloud at small sizes."""
    import math
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    hemis = [(40, 78, 252, 448), (260, 78, 472, 448)]
    for x0, y0, x1, y1 in hemis:
        d.ellipse((x0, y0, x1, y1), fill=255)
        cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
        for k in range(16):                      # gyri bumps round the rim
            a = k / 16 * 2 * math.pi
            bx, by = cx + (rx - 10) * math.cos(a), cy + (ry - 10) * math.sin(a)
            d.ellipse((bx - 30, by - 30, bx + 30, by + 30), fill=255)
    return m.filter(ImageFilter.GaussianBlur(1.5))


def folds(mask: Image.Image) -> Image.Image:
    """Wavy gyri inside each hemisphere plus the central fissure, clipped
    to the brain so no line runs off its edge."""
    import math
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for hx0, hx1, phase in ((60, 236, 0.0), (276, 452, 1.7)):
        for i, y in enumerate(range(130, 430, 52)):
            pts = [(x, y + 13 * math.sin(x / 19 + phase + i)) for x in range(hx0, hx1, 6)]
            d.line(pts, fill=FOLD, width=11, joint="curve")
    fissure = [(256 + 9 * math.sin(y / 23), y) for y in range(70, 455, 6)]
    d.line(fissure, fill=FOLD, width=16, joint="curve")
    clipped = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    clipped.paste(layer, (0, 0), Image.composite(layer.getchannel("A"), Image.new("L", (S, S), 0), mask))
    return clipped


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = brain_mask()

    # body with a lighter top-left, so it reads as round rather than flat
    body = Image.new("RGBA", (S, S), PINK)
    light = Image.new("L", (S, S), 0)
    ImageDraw.Draw(light).ellipse((60, 40, 330, 300), fill=150)
    light = light.filter(ImageFilter.GaussianBlur(60))
    body = Image.composite(Image.new("RGBA", (S, S), PINK_LIGHT), body, light)
    img.paste(body, (0, 0), mask)
    img = Image.alpha_composite(img, folds(mask))

    # rim
    edge = mask.point(lambda v: 255 if v > 128 else 0).filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(9))
    img.paste(Image.new("RGBA", (S, S), FOLD), (0, 0), edge)

    # the D: thick orange-red stroke under a yellow fill, with a soft dark
    # shadow so it lifts off the pink
    d = ImageDraw.Draw(img)
    f = font(280)
    bbox = d.textbbox((0, 0), "D", font=f, stroke_width=20)
    x = (S - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = (S - (bbox[3] - bbox[1])) / 2 - bbox[1] + 8
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x + 7, y + 10), "D", font=f, fill=(60, 10, 30, 170), stroke_width=20, stroke_fill=(60, 10, 30, 170))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    d.text((x, y), "D", font=f, fill=YELLOW, stroke_width=20, stroke_fill=OUTLINE)
    return img


def main() -> None:
    img = draw()
    png = img.resize((256, 256), Image.LANCZOS)
    png.save(STATIC / "tray.png", optimize=True)
    img.save(STATIC / "tray.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    print("wrote", STATIC / "tray.png", "and", STATIC / "tray.ico")


if __name__ == "__main__":
    main()
