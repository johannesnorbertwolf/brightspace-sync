"""Background workers so network work never blocks the menu bar UI."""

from __future__ import annotations

import contextlib
import io
import threading

from .. import auth
from ..api import BrightspaceClient
from ..notify import Notifier
from ..sync import run_sync


class _Stream(io.TextIOBase):
    def __init__(self, emit):
        self._emit = emit
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._emit(line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buf.strip():
            self._emit(self._buf.rstrip())
        self._buf = ""


def _start(target) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def start_sync(cfg, *, interactive, on_line, on_done, on_error, post) -> None:
    def work() -> None:
        stream = _Stream(lambda line: post(on_line, line))
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(
                stream
            ):
                result = run_sync(
                    cfg, allow_interactive=interactive, allow_fallback=False
                )
            post(on_done, result)
        except auth.ReauthRequired as exc:
            post(on_error, str(exc), True)
        except Exception as exc:  # noqa: BLE001 - surface to the UI
            post(on_error, str(exc), False)

    _start(work)


def start_login(
    domain, cookies_path, *, allow_fallback=False, on_line, on_done, on_error, post
) -> None:
    def work() -> None:
        try:
            token = auth.ensure_token(
                domain,
                force_login=True,
                cookies_path=cookies_path,
                allow_fallback=allow_fallback,
                log=lambda line: post(on_line, line),
            )
            who = BrightspaceClient(
                domain, token, cookies_file=cookies_path
            ).whoami()
            name = " ".join(
                filter(None, [who.get("FirstName"), who.get("LastName")])
            ) or who.get("UniqueName") or "your account"
            post(on_done, name)
        except Exception as exc:  # noqa: BLE001 - surface to the UI
            post(on_error, str(exc))

    _start(work)


def start_courses(domain, cookies_path, *, on_done, on_error, post) -> None:
    def work() -> None:
        try:
            token = auth.ensure_token(
                domain, allow_interactive=False, cookies_path=cookies_path
            )
            courses = BrightspaceClient(
                domain, token, cookies_file=cookies_path
            ).courses()
            post(on_done, courses)
        except Exception as exc:  # noqa: BLE001 - surface to the UI
            post(on_error, str(exc))

    _start(work)


def start_test_notify(notify_cfg, *, on_done, post) -> None:
    def work() -> None:
        try:
            Notifier(notify_cfg, verbose=True).send(
                "Brightspace Sync", "Test notification - everything is wired up."
            )
        except Exception:  # noqa: BLE001 - best effort
            pass
        post(on_done)

    _start(work)
