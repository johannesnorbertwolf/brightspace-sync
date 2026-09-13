"""Minimal Chrome DevTools Protocol client used for the guided login.

We drive the user's installed Google Chrome directly (no Playwright, no
bundled browser) so that the resulting login captures BOTH the OAuth code
(from the redirect to ``brightspacepulse://auth``) and the browser session
cookies needed to fetch the course reader and other browser-only files.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


class CDPError(RuntimeError):
    """Raised when Chrome cannot be launched or controlled."""


def find_chrome() -> str | None:
    """Return the path to an installed Chrome/Chromium, or None."""
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("google-chrome") or shutil.which("chromium")


def _free_profile_ok(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    # A stale port file from a previous run would point at a dead port.
    (profile_dir / "DevToolsActivePort").unlink(missing_ok=True)
    lock = profile_dir / "SingletonLock"
    lock.unlink(missing_ok=True)


def launch_chrome(profile_dir: Path, *, headless: bool = False):
    """Launch Chrome with remote debugging and return ``(process, port)``."""
    executable = find_chrome()
    if not executable:
        raise CDPError(
            "Google Chrome was not found. Install it from https://www.google.com/chrome/"
        )
    _free_profile_ok(profile_dir)

    args = [
        executable,
        "--remote-debugging-port=0",
        f"--user-data-dir={profile_dir}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,OptimizationHints,MediaRouter",
        "--disable-sync",
        "--no-service-autorun",
    ]
    if headless:
        args.append("--headless=new")
    args.append("about:blank")

    process = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    port_file = profile_dir / "DevToolsActivePort"
    for _ in range(200):
        if process.poll() is not None:
            raise CDPError("Chrome closed before it was ready.")
        if port_file.exists():
            lines = port_file.read_text(errors="replace").splitlines()
            if lines and lines[0].strip().isdigit():
                return process, int(lines[0].strip())
        time.sleep(0.1)
    process.terminate()
    raise CDPError("Chrome did not start in time.")


def page_socket_url(port: int, timeout: float = 15.0) -> str:
    """Return the websocket URL of the first controllable page target."""
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json", timeout=2
            ) as response:
                targets = json.loads(response.read())
            for target in targets:
                if target.get("type") == "page" and target.get(
                    "webSocketDebuggerUrl"
                ):
                    return target["webSocketDebuggerUrl"]
        except Exception as exc:  # noqa: BLE001 - retry until the deadline
            last_error = exc
        time.sleep(0.2)
    raise CDPError(f"Could not find a Chrome page to control ({last_error}).")


class CDP:
    """Tiny synchronous wrapper over a single DevTools websocket."""

    def __init__(self, ws_url: str, *, timeout: float = 30.0):
        self.ws = websocket.create_connection(
            ws_url, timeout=timeout, suppress_origin=True
        )
        self._id = 0

    def call(self, method: str, timeout: float = 30.0, **params) -> dict:
        self._id += 1
        message_id = self._id
        self.ws.send(
            json.dumps({"id": message_id, "method": method, "params": params})
        )
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise CDPError(f"Timed out waiting for {method}.")
            self.ws.settimeout(min(remaining, 5.0))
            try:
                message = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if message.get("id") == message_id:
                if "error" in message:
                    raise CDPError(f"{method} failed: {message['error']}")
                return message.get("result") or {}

    def wait_for(self, predicate, timeout: float):
        """Read events until ``predicate(message)`` is truthy or time runs out."""
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            self.ws.settimeout(min(remaining, 5.0))
            try:
                message = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            except websocket.WebSocketConnectionClosedException:
                return None
            if "method" in message:
                found = predicate(message)
                if found is not None:
                    return found

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def stop_chrome(process) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=8)
    except Exception:  # noqa: BLE001
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass
