"""Tell users when a newer release is available.

The menu bar app checks GitHub's public releases API on launch and once a
day.  When a newer version exists it surfaces the release notes so the user
knows what changed before deciding to download.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from . import __version__

REPO = "johannesnorbertwolf/brightspace-sync"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


@dataclass
class Update:
    version: str
    notes: str
    url: str
    download_url: str | None


def _numbers(version: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", version or "")
    return tuple(int(part) for part in parts) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    """True when ``candidate`` is a higher version than ``current``."""
    return _numbers(candidate) > _numbers(current)


def check(timeout: float = 8.0) -> Update | None:
    """Return the latest release if it is newer than this build, else None."""
    try:
        response = requests.get(
            API,
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return None

    version = str(data.get("tag_name") or "").lstrip("v")
    if not version or not is_newer(version):
        return None

    download = None
    for asset in data.get("assets") or []:
        if str(asset.get("name", "")).endswith(".dmg"):
            download = asset.get("browser_download_url")
            break

    return Update(
        version=version,
        notes=(data.get("body") or "").strip(),
        url=data.get("html_url") or RELEASES_PAGE,
        download_url=download,
    )
