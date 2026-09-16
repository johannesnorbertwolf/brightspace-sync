"""Native macOS menu bar application for brightspace-sync."""

from __future__ import annotations

import fcntl
import os
import sys
import time
from pathlib import Path

import objc
from AppKit import (
    NSAlert,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
)
from Foundation import NSObject, NSTimer
from PyObjCTools import AppHelper

from .. import __version__, auth, config as config_mod
from ..notify import Notifier
from ..state import State
from . import icons, platform, workers
from .settings_window import SettingsWindow

LOCK_PATH = Path.home() / ".local" / "state" / "brightspace-sync" / "app.lock"
LOG_DIR = Path.home() / "Library" / "Logs" / "brightspace-sync"


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        handle = open(LOG_DIR / "app.log", "a", buffering=1)  # noqa: SIM115
    except OSError:
        return
    sys.stdout = handle
    sys.stderr = handle


def _acquire_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, "w")  # noqa: SIM115
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


class AppController(NSObject):
    def init(self):
        self = objc.super(AppController, self).init()
        if self is None:
            return None
        self.cfg = config_mod.load()
        self.account_name = ""
        self.syncing = False
        self.needs_login = False
        self.timer = None
        self.settings = None
        self.log_lines = []
        self._post = lambda fn, *args: AppHelper.callAfter(fn, *args)
        self._build_status_item()
        self._update_menu()
        return self

    # -- status item -------------------------------------------------------

    def _build_status_item(self):
        self.status_bar_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            -1
        )
        self.status_bar_item.button().setImage_(icons.status_image())
        self.status_bar_item.button().setToolTip_("Brightspace Sync")

        self.menu = NSMenu.alloc().init()
        self.status_bar_item.setMenu_(self.menu)

        self.status_line = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "", None, ""
        )
        self.status_line.setEnabled_(False)
        self.menu.addItem_(self.status_line)
        self.menu.addItem_(NSMenuItem.separatorItem())

        self.sync_item = self._add_item("Sync Now", b"syncNow:", "s")
        self.folder_item = self._add_item(
            "Open Brightspace Folder", b"openFolder:", ""
        )
        self.prefs_item = self._add_item("Preferences…", b"openPrefs:", ",")
        self.login_item = self._add_item("Sign In Again…", b"signIn:", "")
        self.menu.addItem_(NSMenuItem.separatorItem())
        self._add_item("About Brightspace Sync", b"about:", "")
        self._add_item("Quit Brightspace Sync", b"quit:", "q")

    def _add_item(self, title, action, key):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, action, key
        )
        item.setTarget_(self)
        self.menu.addItem_(item)
        return item

    def _update_menu(self):
        summary = State().load().summary()
        if self.syncing:
            status = "Syncing…"
        elif self.needs_login:
            status = "Needs sign-in"
        elif summary.get("last_run"):
            when = time.strftime("%H:%M", time.localtime(summary["last_run"]))
            status = f"Up to date - {when}"
        else:
            status = "Not synced yet"
        self.status_line.setTitle_(status)
        self.sync_item.setEnabled_(not self.syncing)
        self.status_bar_item.button().setImage_(
            icons.status_image(self.needs_login)
        )

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        if not self.cfg.get("app", {}).get("setup_complete"):
            self._open_settings("setup")
        else:
            self._reschedule()
            if not State().load().summary().get("last_run"):
                self._start_sync(interactive=False)

    def token_ready(self):
        domain = self.cfg.get("domain") or "brightspace.rug.nl"
        try:
            status = auth.token_status(domain)
        except Exception:  # noqa: BLE001
            return False
        return bool(status.get("has_token")) and not status.get("error")

    def after_setup(self):
        self.cfg = config_mod.load()
        platform.set_launch_at_login(bool(self.cfg["app"].get("launch_at_login")))
        if platform.remove_legacy_agent():
            self._log_message("Removed the old background job; the app now schedules syncs.")
        self._reschedule()
        self._start_sync(interactive=False)

    def after_prefs_saved(self):
        self.cfg = config_mod.load()
        platform.set_launch_at_login(bool(self.cfg["app"].get("launch_at_login")))
        self._reschedule()
        self._update_menu()

    def _reschedule(self):
        if self.timer is not None:
            self.timer.invalidate()
        minutes = int(self.cfg.get("app", {}).get("interval_minutes", 60))
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            minutes * 60, self, b"onTimer:", None, True
        )

    def onTimer_(self, sender):
        self._start_sync(interactive=False)

    def _open_settings(self, mode):
        self.settings = SettingsWindow.alloc().initWithController_mode_(self, mode)
        self.settings.show()

    # -- sync --------------------------------------------------------------

    def _start_sync(self, *, interactive):
        if self.syncing:
            return
        self.syncing = True
        self.needs_login = False
        self._update_menu()
        workers.start_sync(
            self.cfg,
            interactive=interactive,
            on_line=self._on_line,
            on_done=self._on_sync_done,
            on_error=self._on_sync_error,
            post=self._post,
        )

    def _on_line(self, line):
        self.log_lines.append(line)
        if len(self.log_lines) > 500:
            del self.log_lines[:100]

    def _on_sync_done(self, result):
        self.syncing = False
        self.needs_login = False
        self._update_menu()
        if result.first_run and result.counts.get("new"):
            self._send_notification(
                "Brightspace Sync is set up",
                f"Downloaded {result.counts.get('new', 0)} items across "
                f"{result.courses} courses.",
            )

    def _on_sync_error(self, message, reauth):
        self.syncing = False
        self.needs_login = bool(reauth)
        self._update_menu()
        if reauth:
            self._send_notification(
                "Brightspace sign-in needed",
                "Choose 'Sign In Again' from the menu to reconnect.",
            )
        else:
            self._send_notification("Brightspace sync failed", message[:150])

    def _send_notification(self, title, message):
        try:
            Notifier(self.cfg.get("notify")).send(title, message)
        except Exception:  # noqa: BLE001
            pass

    # -- menu actions ------------------------------------------------------

    def syncNow_(self, sender):
        self._start_sync(interactive=True)

    def openFolder_(self, sender):
        platform.reveal(config_mod.resolve_out_dir(self.cfg))

    def openPrefs_(self, sender):
        self._open_settings("prefs")

    def signIn_(self, sender):
        if self.syncing:
            return
        allow_fallback = False
        if platform.find_chrome() is None:
            if platform.confirm_login_without_chrome() != "continue":
                return
            allow_fallback = True
        self.syncing = True
        self.needs_login = False
        self._update_menu()
        workers.start_login(
            self.cfg.get("domain") or "brightspace.rug.nl",
            config_mod.resolve_cookies_file(self.cfg),
            allow_fallback=allow_fallback,
            on_line=self._on_line,
            on_done=self._on_login_done,
            on_error=self._on_login_error,
            post=self._post,
        )

    def _on_login_done(self, name):
        self.account_name = name
        self.syncing = False
        self._update_menu()
        self._start_sync(interactive=False)

    def _on_login_error(self, message):
        self.syncing = False
        self.needs_login = True
        self._update_menu()
        self._send_notification("Brightspace sign-in failed", message[:150])

    def about_(self, sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Brightspace Sync")
        alert.setInformativeText_(
            f"Version {__version__}\n\n"
            "Keeps a copy of all your Brightspace course material on this Mac "
            "and notifies you when something changes."
        )
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    def quit_(self, sender):
        NSApplication.sharedApplication().terminate_(None)

    def quit_app(self):
        NSApplication.sharedApplication().terminate_(None)

    def _log_message(self, message):
        self.log_lines.append(message)


def main(argv=None) -> int:
    lock = _acquire_lock()
    if lock is None:
        return 0
    _setup_logging()
    config_mod.load_env()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    controller = AppController.alloc().init()
    controller.start()
    AppHelper.runEventLoop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
