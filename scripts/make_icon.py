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


def _rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def render(size: int) -> Image.Image:
    scale = 4
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Blue rounded square.
    margin = int(s * 0.06)
    _rounded(
        draw,
        (margin, margin, s - margin, s - margin),
        int(s * 0.22),
        (37, 99, 235, 255),
    )

    # White download arrow.
    cx = s / 2
    top = s * 0.26
    mid = s * 0.58
    shaft_w = int(s * 0.075)
    draw.rounded_rectangle(
        (cx - shaft_w / 2, top, cx + shaft_w / 2, mid),
        radius=shaft_w / 2,
        fill=(255, 255, 255, 255),
    )
    head = [
        (cx - s * 0.17, mid - s * 0.06),
        (cx + s * 0.17, mid - s * 0.06),
        (cx, mid + s * 0.14),
    ]
    draw.polygon(head, fill=(255, 255, 255, 255))

    # Tray.
    tray_w = int(s * 0.09)
    left = cx - s * 0.24
    right = cx + s * 0.24
    bottom = s * 0.78
    draw.rounded_rectangle(
        (left, bottom - tray_w, right, bottom), radius=tray_w / 2,
        fill=(255, 255, 255, 255),
    )
    draw.rounded_rectangle(
        (left, s * 0.62, left + tray_w, bottom), radius=tray_w / 2,
        fill=(255, 255, 255, 255),
    )
    draw.rounded_rectangle(
        (right - tray_w, s * 0.62, right, bottom), radius=tray_w / 2,
        fill=(255, 255, 255, 255),
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
