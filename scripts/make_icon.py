#!/usr/bin/env python3
"""Generate the app icon (.icns) with Pillow, no binary assets in the repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
ICONSET = BUILD / "AppIcon.iconset"
ICNS = BUILD / "AppIcon.icns"

SIZES = [16, 32, 64, 128, 256, 512, 1024]

BG = (37, 99, 235, 255)       # blue rounded square
SKIN = (247, 206, 170, 255)   # face
SHADE = (232, 184, 146, 255)  # ears and neck
HAIR = (58, 44, 42, 255)      # hair
FEATURE = (58, 44, 42, 255)   # brows, eyes, nose, mouth
SHIRT = (237, 242, 249, 255)  # shoulders


def render(size: int) -> Image.Image:
    """A flat, friendly portrait: the stylised face used across the app."""
    scale = 4
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def u(v: float) -> float:
        return v * s

    def box(x0, y0, x1, y1):
        return (u(x0), u(y0), u(x1), u(y1))

    def line_width(v: float) -> int:
        return max(1, int(u(v)))

    # Rounded-square background.
    margin = u(0.06)
    draw.rounded_rectangle(
        (margin, margin, s - margin, s - margin), radius=u(0.22), fill=BG
    )

    # Shoulders, neck, ears, head.
    draw.ellipse(box(0.08, 0.72, 0.92, 1.30), fill=SHIRT)
    draw.rectangle(box(0.42, 0.50, 0.58, 0.78), fill=SHADE)
    draw.ellipse(box(0.255, 0.36, 0.335, 0.46), fill=SHADE)
    draw.ellipse(box(0.665, 0.36, 0.745, 0.46), fill=SHADE)
    draw.ellipse(box(0.28, 0.16, 0.72, 0.64), fill=SKIN)

    # Hair as a cap over the top of the head.
    draw.pieslice(box(0.255, 0.08, 0.745, 0.46), 180, 360, fill=HAIR)

    # Brows.
    for cx in (0.41, 0.59):
        draw.arc(
            box(cx - 0.05, 0.335, cx + 0.05, 0.39),
            180,
            360,
            fill=FEATURE,
            width=line_width(0.011),
        )
    # Eyes.
    for cx in (0.41, 0.59):
        draw.ellipse(box(cx - 0.028, 0.40, cx + 0.028, 0.445), fill=FEATURE)
    # Nose.
    draw.line(
        [(u(0.5), u(0.44)), (u(0.5), u(0.50))],
        fill=FEATURE,
        width=line_width(0.009),
    )
    # Smile.
    draw.arc(
        box(0.42, 0.47, 0.58, 0.575),
        0,
        180,
        fill=FEATURE,
        width=line_width(0.013),
    )

    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    ICONSET.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        render(size).save(ICONSET / f"icon_{size}x{size}.png")
        if size <= 512:
            render(size * 2).save(ICONSET / f"icon_{size}x{size}@2x.png")
    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True
    )
    print(f"wrote {ICNS}")


if __name__ == "__main__":
    main()
