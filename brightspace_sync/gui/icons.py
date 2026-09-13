"""Draws the menu bar icon programmatically (no binary assets needed)."""

from __future__ import annotations

from AppKit import NSBezierPath, NSColor, NSImage, NSMakeRect, NSMakeSize


def _stroke(path, width: float = 1.6) -> None:
    path.setLineWidth_(width)
    path.setLineCapStyle_(1)
    path.setLineJoinStyle_(1)
    path.stroke()


def status_image(attention: bool = False) -> NSImage:
    """A small template image: a download arrow into a tray.

    Template images are recoloured by macOS to match the menu bar, so they
    look native in both light and dark mode.
    """
    image = NSImage.alloc().initWithSize_(NSMakeSize(18, 18))
    image.lockFocus()
    NSColor.blackColor().set()

    shaft = NSBezierPath.bezierPath()
    shaft.moveToPoint_((9.0, 13.5))
    shaft.lineToPoint_((9.0, 6.5))
    _stroke(shaft)

    head = NSBezierPath.bezierPath()
    head.moveToPoint_((5.5, 10.0))
    head.lineToPoint_((9.0, 6.5))
    head.lineToPoint_((12.5, 10.0))
    _stroke(head)

    tray = NSBezierPath.bezierPath()
    tray.moveToPoint_((3.5, 8.5))
    tray.lineToPoint_((3.5, 3.5))
    tray.lineToPoint_((14.5, 3.5))
    tray.lineToPoint_((14.5, 8.5))
    _stroke(tray)

    if attention:
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(12.0, 12.0, 6.0, 6.0)
        ).fill()

    image.unlockFocus()
    image.setTemplate_(True)
    return image
