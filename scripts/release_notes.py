#!/usr/bin/env python3
"""Print the CHANGELOG section for a version, for use as release notes.

The release workflow runs this with the pushed tag (for example ``v0.1.1``)
and feeds the output to the GitHub release. It exits non-zero when there is
no matching entry, so a release cannot go out without meaningful notes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"


def notes_for(version: str) -> str | None:
    text = CHANGELOG.read_text(encoding="utf-8")
    version = version.lstrip("v")
    match = re.search(rf"^##\s+{re.escape(version)}\b.*$", text, re.MULTILINE)
    if not match:
        return None
    start = match.end()
    following = re.search(r"^##\s+", text[start:], re.MULTILINE)
    end = start + following.start() if following else len(text)
    return text[start:end].strip()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_notes.py <version|tag>", file=sys.stderr)
        return 2
    body = notes_for(argv[1])
    if not body:
        print(
            f"error: no CHANGELOG.md entry for {argv[1]!r}. "
            "Add one before releasing.",
            file=sys.stderr,
        )
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
