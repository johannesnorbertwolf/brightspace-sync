"""Draws the menu bar icon programmatically (no binary assets needed)."""

from __future__ import annotations

from AppKit import NSBezierPath, NSColor, NSImage, NSMakeRect, NSMakeSize

BASE = 18.0


def _stroke(path, width: float) -> None:
    path.setLineWidth_(width)
    path.setLineCapStyle_(1)
    path.setLineJoinStyle_(1)
    path.stroke()


def _dot(cx: float, cy: float, radius: float) -> None:
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - radius, cy - radius, radius * 2, radius * 2)
    ).fill()


def _arc(start, end, control1, control2, width: float) -> None:
    path = NSBezierPath.bezierPath()
    path.moveToPoint_(start)
    path.curveToPoint_controlPoint1_controlPoint2_(end, control1, control2)
    _stroke(path, width)


def status_image(attention: bool = False, size: float = BASE) -> NSImage:
    """A small template image: a stylised face.

    Template images are recoloured by macOS to match the menu bar, so they
    look native in both light and dark mode. ``size`` exists so previews can
    render the same drawing large.
    """
    f = size / BASE
    image = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    image.lockFocus()
    NSColor.blackColor().set()

    def p(x: float, y: float) -> tuple[float, float]:
        return (x * f, y * f)

    # Head outline.
    head = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(4.0 * f, 3.0 * f, 10.0 * f, 12.0 * f)
    )
    _stroke(head, 1.5 * f)

    # Hair: an arc over the top of the head.
    _arc(
        p(4.4, 11.2),
        p(13.6, 11.2),
        p(6.5, 15.6),
        p(11.5, 15.6),
        1.5 * f,
    )

    # Eyes.
    _dot(6.9 * f, 9.4 * f, 0.95 * f)
    _dot(11.1 * f, 9.4 * f, 0.95 * f)

    # Smile.
    _arc(
        p(6.8, 6.7),
        p(11.2, 6.7),
        p(7.7, 4.9),
        p(10.3, 4.9),
        1.5 * f,
    )

    if attention:
        _dot(15.3 * f, 15.3 * f, 2.2 * f)

    image.unlockFocus()
    image.setTemplate_(True)
    return image
