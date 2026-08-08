#!/usr/bin/env python3
"""Generate the PaperTrace pixel-art logo.

Deterministic: draws on a 24x24 grid with hard pixels, then scales with
nearest-neighbour. Motif: a manuscript page with one line boxed in red — the
tool's evidence mark — under a magnifying glass.

Outputs:
  assets/logo.png      384x384  (24px grid x16)
  assets/logo_small.png 96x96   (x4, for favicons / inline use)
"""

from pathlib import Path

from PIL import Image, ImageDraw

GRID = 24
OUT = Path(__file__).resolve().parent.parent / "assets"

# palette
BADGE = (27, 39, 51, 255)        # dark slate badge
BADGE_HI = (44, 62, 80, 255)     # subtle top-left bevel
PAGE = (245, 242, 234, 255)      # warm paper
PAGE_SHADOW = (208, 202, 188, 255)
INK = (138, 148, 166, 255)       # text lines
RED = (229, 72, 77, 255)         # the evidence box
RING = (224, 168, 60, 255)       # magnifier ring (amber)
RING_DARK = (166, 118, 32, 255)  # handle shading
GLASS = (188, 224, 244, 140)     # lens tint (translucent)


def px(draw: ImageDraw.ImageDraw, x: int, y: int, c) -> None:
    draw.point((x, y), fill=c)


def rect(draw, x0, y0, x1, y1, c):
    draw.rectangle([x0, y0, x1, y1], fill=c)


def main() -> None:
    img = Image.new("RGBA", (GRID, GRID), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # --- badge: rounded square (corners cut 2px, pixel-art style) ----------
    rect(d, 2, 0, 21, 23, BADGE)
    rect(d, 0, 2, 23, 21, BADGE)
    rect(d, 1, 1, 22, 22, BADGE)
    # bevel highlight along top and left
    rect(d, 2, 0, 21, 0, BADGE_HI)
    rect(d, 0, 2, 0, 21, BADGE_HI)
    px(d, 1, 1, BADGE_HI)

    # --- manuscript page ---------------------------------------------------
    rect(d, 4, 3, 14, 19, PAGE)
    # folded corner (top-right)
    for i in range(3):
        rect(d, 12 + i, 3, 14, 3 + i, BADGE)  # cut the corner
    rect(d, 12, 4, 13, 5, PAGE_SHADOW)        # the fold itself
    px(d, 12, 3, PAGE_SHADOW)
    # right/bottom page shadow
    rect(d, 14, 6, 14, 19, PAGE_SHADOW)
    rect(d, 4, 19, 14, 19, PAGE_SHADOW)

    # --- text lines --------------------------------------------------------
    rect(d, 6, 5, 11, 5, INK)
    rect(d, 6, 7, 12, 7, INK)
    rect(d, 6, 9, 10, 9, INK)
    rect(d, 6, 11, 12, 11, INK)
    rect(d, 6, 17, 9, 17, INK)

    # --- magnifying glass over the lower half ------------------------------
    # lens interior: pale glass, opaque (reads as glass over paper)
    d.ellipse([8, 9, 19, 20], fill=(207, 227, 239, 255))
    # the magnified evidence mark: one line, boxed in red — inside the lens
    rect(d, 10, 12, 17, 12, RED)
    rect(d, 10, 16, 17, 16, RED)
    rect(d, 10, 12, 10, 16, RED)
    rect(d, 17, 12, 17, 16, RED)
    rect(d, 11, 13, 16, 15, INK)
    # ring: single bold amber ring
    d.ellipse([8, 9, 19, 20], outline=RING, width=2)
    # handle: chunky diagonal to the corner
    rect(d, 18, 18, 19, 19, RING)
    rect(d, 19, 19, 20, 20, RING)
    rect(d, 20, 20, 21, 21, RING)
    px(d, 21, 21, RING_DARK)

    OUT.mkdir(parents=True, exist_ok=True)
    img.resize((GRID * 16, GRID * 16), Image.NEAREST).save(OUT / "logo.png")
    img.resize((GRID * 4, GRID * 4), Image.NEAREST).save(OUT / "logo_small.png")
    print(f"wrote {OUT/'logo.png'} ({GRID*16}px) and logo_small.png ({GRID*4}px)")


if __name__ == "__main__":
    main()
