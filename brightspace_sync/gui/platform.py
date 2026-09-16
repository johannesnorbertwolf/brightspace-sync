"""macOS integration helpers: Chrome detection, login item, Finder, iMessage."""

from __future__ import annotations

import getpass
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .. import browser

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
LOG_DIR = Path.home() / "Library" / "Logs" / "brightspace-sync"
APP_LABEL = "com.brightspace-sync.app"


def find_chrome() -> str | None:
    return browser.find_chrome()


def chrome_download_url() -> str:
    return "https://www.google.com/chrome/"


def confirm_login_without_chrome() -> str:
    """Warn that signing in without Chrome means some files are link-only.

    Returns ``"continue"`` to sign in without Chrome, ``"get-chrome"`` after
    opening the download page, or ``"cancel"`` to stop.
    """
    from AppKit import NSAlert

    alert = NSAlert.alloc().init()
    alert.setMessageText_("Google Chrome is not installed")
    alert.setInformativeText_(browser.NO_CHROME_NOTE)
    alert.addButtonWithTitle_("Continue Without Chrome")
    alert.addButtonWithTitle_("Get Chrome")
    alert.addButtonWithTitle_("Cancel")
    response = alert.runModal()
    if response == 1001:  # second button
        open_url(chrome_download_url())
        return "get-chrome"
    if response == 1000:  # first button, the default
        return "continue"
    return "cancel"


def open_url(url: str) -> None:
    subprocess.run(["open", url], check=False)


def reveal(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["open", str(path)], check=False)


def _app_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "brightspace_sync", "app"]


def launch_at_login_enabled() -> bool:
    return (LAUNCH_AGENTS / f"{APP_LABEL}.plist").exists()


def set_launch_at_login(enabled: bool) -> None:
    """Install or remove a login item that starts the menu bar app."""
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LAUNCH_AGENTS / f"{APP_LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", f"{domain}/{APP_LABEL}"],
        check=False,
        capture_output=True,
    )
    if not enabled:
        path.unlink(missing_ok=True)
        return
    plist = {
        "Label": APP_LABEL,
        "ProgramArguments": _app_command(),
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(LOG_DIR / "app.log"),
        "StandardErrorPath": str(LOG_DIR / "app.err.log"),
    }
    path.write_bytes(plistlib.dumps(plist))
    subprocess.run(
        ["launchctl", "bootstrap", domain, str(path)],
        check=False,
        capture_output=True,
    )


def remove_legacy_agent() -> bool:
    """Remove the old CLI launchd job now that the app schedules syncs."""
    label = f"com.{getpass.getuser()}.brightspace-sync"
    domain = f"gui/{os.getuid()}"
    path = LAUNCH_AGENTS / f"{label}.plist"
    if not path.exists():
        return False
    subprocess.run(
        ["launchctl", "bootout", f"{domain}/{label}"],
        check=False,
        capture_output=True,
    )
    path.unlink(missing_ok=True)
    return True


def detect_imessage_handle() -> str:
    """Best-effort: the address this Mac's iMessage account is registered to."""
    script = (
        'tell application "Messages" to get description of '
        "(1st account whose service type = iMessage)"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = result.stdout.strip()
    if text.startswith(("E:", "P:")):
        return text[2:]
    return text
