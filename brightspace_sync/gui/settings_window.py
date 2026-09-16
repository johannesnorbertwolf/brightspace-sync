"""A single native settings window used for first-run setup and preferences."""

from __future__ import annotations

import os
from pathlib import Path

import objc
from AppKit import (
    NSAlert,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSColor,
    NSFont,
    NSMakeRect,
    NSOpenPanel,
    NSPopUpButton,
    NSScrollView,
    NSSwitchButton,
    NSTextField,
    NSURL,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject
from PyObjCTools import AppHelper

from .. import config as config_mod
from . import platform, workers

WIDTH = 560
MARGIN = 22
GAP = 18
BTN_BAR = 68
DOC_WIDTH = WIDTH - 2

INTERVALS = [
    ("Every 15 minutes", 15),
    ("Every 30 minutes", 30),
    ("Every hour", 60),
    ("Every 2 hours", 120),
    ("Every 6 hours", 360),
    ("Once a day", 1440),
]


def _label(text, frame, *, size=13, bold=False, color=None):
    field = NSTextField.alloc().initWithFrame_(frame)
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setFont_(
        NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
    )
    if color is not None:
        field.setTextColor_(color)
    return field


def _button(title, frame, action, target, *, key=""):
    button = NSButton.alloc().initWithFrame_(frame)
    button.setTitle_(title)
    button.setBezelStyle_(NSBezelStyleRounded)
    button.setTarget_(target)
    button.setAction_(action)
    if key:
        button.setKeyEquivalent_(key)
    return button


def _checkbox(title, frame, action, target):
    button = NSButton.alloc().initWithFrame_(frame)
    button.setButtonType_(NSSwitchButton)
    button.setTitle_(title)
    button.setTarget_(target)
    button.setAction_(action)
    return button


def _text_field(value, frame):
    field = NSTextField.alloc().initWithFrame_(frame)
    field.setStringValue_(value or "")
    field.setEditable_(True)
    field.setBezeled_(True)
    field.setBezelStyle_(1)
    field.setDrawsBackground_(True)
    field.setFont_(NSFont.systemFontOfSize_(12))
    return field


class SettingsWindow(NSObject):
    """Setup wizard and preferences, sharing one layout."""

    def initWithController_mode_(self, controller, mode):
        self = objc.super(SettingsWindow, self).init()
        if self is None:
            return None
        self.controller = controller
        self.mode = mode
        self.cfg = controller.cfg
        self.courses = []
        self.course_buttons = {}
        self.selected_ids = set()
        self._ids_initialized = False
        self.signed_in = False
        self.account_name = ""
        self.busy = False
        self.course_status = ""
        self._post = lambda fn, *args: AppHelper.callAfter(fn, *args)
        self._doc_h = 100
        self._build_window()
        self._rebuild()
        self._maybe_load()
        return self

    # -- window construction ----------------------------------------------

    def _build_window(self):
        height = 700
        rect = NSMakeRect(0, 0, WIDTH, height)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_(
            "Set Up Brightspace Sync"
            if self.mode == "setup"
            else "Brightspace Sync Preferences"
        )
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        self.window.center()

        content = self.window.contentView()
        self.scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, BTN_BAR, WIDTH, height - BTN_BAR)
        )
        self.scroll.setHasVerticalScroller_(True)
        self.scroll.setDrawsBackground_(False)
        self.doc = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, DOC_WIDTH, 100))
        self.scroll.setDocumentView_(self.doc)
        content.addSubview_(self.scroll)

        bar = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, BTN_BAR))
        self.primary = _button(
            "Start Syncing" if self.mode == "setup" else "Save",
            NSMakeRect(WIDTH - MARGIN - 140, 18, 140, 32),
            b"primaryAction:",
            self,
            key="\r",
        )
        bar.addSubview_(self.primary)
        self.secondary = _button(
            "Quit" if self.mode == "setup" else "Close",
            NSMakeRect(MARGIN, 18, 90, 32),
            b"secondaryAction:",
            self,
        )
        bar.addSubview_(self.secondary)
        content.addSubview_(bar)

    def _frame_for(self, x, top, w, h):
        return NSMakeRect(x, self._doc_h - top - h, w, h)

    def _section_label(self, text, top):
        self.doc.addSubview_(
            _label(text, self._frame_for(MARGIN, top, DOC_WIDTH - 2 * MARGIN, 20), bold=True)
        )

    # -- layout ------------------------------------------------------------

    def _rebuild(self):
        for view in list(self.doc.subviews()):
            view.removeFromSuperview()
        self.course_buttons = {}

        rows = max(1, len(self.courses))
        heights = {
            "header": 60,
            "storage": 64,
            "account": 64,
            "courses": 44 + rows * 24 + 4,
            "notify": 118,
            "schedule": 72,
        }
        order = ["header", "account", "storage", "courses", "notify", "schedule"]
        doc_h = (
            MARGIN
            + sum(heights[key] for key in order)
            + GAP * (len(order) - 1)
            + MARGIN
        )
        self._doc_h = doc_h
        self.doc.setFrame_(NSMakeRect(0, 0, DOC_WIDTH, doc_h))

        top = MARGIN
        for key in order:
            height = heights[key]
            getattr(self, f"_build_{key}")(top)
            top += height + GAP

        self._update_primary()

    def _update_primary(self):
        """Keep the bottom-right button in step with the sign-in state."""
        if getattr(self, "primary", None) is None:
            return
        if self.busy:
            title, enabled = "Signing in…", False
        elif self.mode == "setup" and not self.signed_in:
            title, enabled = "Sign In to Continue", True
        else:
            title = "Start Syncing" if self.mode == "setup" else "Save"
            enabled = True
        self.primary.setTitle_(title)
        self.primary.setEnabled_(enabled)

    def _build_header(self, top):
        self.doc.addSubview_(
            _label("Brightspace Sync", self._frame_for(MARGIN, top, DOC_WIDTH, 26), size=18, bold=True)
        )
        self.doc.addSubview_(
            _label(
                "Keep a copy of all your course material on this Mac.",
                self._frame_for(MARGIN, top + 30, DOC_WIDTH, 18),
                size=12,
                color=NSColor.secondaryLabelColor(),
            )
        )

    def _build_storage(self, top):
        self._section_label("Where to save files", top)
        field_w = DOC_WIDTH - 2 * MARGIN - 100
        self.path_field = _text_field(
            self.cfg.get("out_dir", "~/Brightspace"),
            self._frame_for(MARGIN, top + 26, field_w, 24),
        )
        self.doc.addSubview_(self.path_field)
        self.doc.addSubview_(
            _button(
                "Choose…",
                self._frame_for(MARGIN + field_w + 8, top + 24, 92, 28),
                b"chooseFolder:",
                self,
            )
        )

    def _build_account(self, top):
        self._section_label("Brightspace account", top)
        text = (
            f"Signed in as {self.account_name}."
            if self.signed_in
            else "Not signed in yet - sign in to continue."
        )
        color = NSColor.systemGreenColor() if self.signed_in else NSColor.secondaryLabelColor()
        self.signin_status = _label(
            text, self._frame_for(MARGIN, top + 30, DOC_WIDTH - 2 * MARGIN - 120, 20), size=12, color=color
        )
        self.doc.addSubview_(self.signin_status)
        self.signin_button = _button(
            "Signing in…" if self.busy else "Sign In…",
            self._frame_for(DOC_WIDTH - MARGIN - 110, top + 24, 110, 28),
            b"signIn:",
            self,
        )
        self.signin_button.setEnabled_(not self.busy)
        self.doc.addSubview_(self.signin_button)

    def _build_courses(self, top):
        self._section_label("Courses to sync", top)
        self.doc.addSubview_(
            _button("All", self._frame_for(DOC_WIDTH - MARGIN - 116, top + 2, 52, 24), b"selectAll:", self)
        )
        self.doc.addSubview_(
            _button("None", self._frame_for(DOC_WIDTH - MARGIN - 60, top + 2, 60, 24), b"selectNone:", self)
        )
        y = top + 32
        if not self.courses:
            self.doc.addSubview_(
                _label(
                    self.course_status or "Sign in to load your courses.",
                    self._frame_for(MARGIN, y, DOC_WIDTH - 2 * MARGIN, 20),
                    size=12,
                    color=NSColor.secondaryLabelColor(),
                )
            )
            return
        for course in self.courses:
            box = _checkbox(
                course["name"], self._frame_for(MARGIN, y, DOC_WIDTH - 2 * MARGIN, 20), b"", self
            )
            if course["id"] in self.selected_ids:
                box.setState_(1)
            self.course_buttons[course["id"]] = box
            self.doc.addSubview_(box)
            y += 24

    def _build_notify(self, top):
        self._section_label("Notifications", top)
        self.macos_check = _checkbox(
            "Show macOS notifications",
            self._frame_for(MARGIN, top + 26, 280, 20),
            b"",
            self,
        )
        self.macos_check.setState_(1 if self.cfg["notify"].get("macos", True) else 0)
        self.doc.addSubview_(self.macos_check)

        imessage = self.cfg["notify"].get("imessage") or {}
        self.imessage_check = _checkbox(
            "Also send me an iMessage",
            self._frame_for(MARGIN, top + 50, 230, 20),
            b"imessageToggled:",
            self,
        )
        self.imessage_check.setState_(1 if imessage.get("enabled") else 0)
        self.doc.addSubview_(self.imessage_check)
        self.imessage_field = _text_field(
            imessage.get("recipient", ""),
            self._frame_for(MARGIN + 236, top + 47, DOC_WIDTH - 2 * MARGIN - 236, 24),
        )
        self.doc.addSubview_(self.imessage_field)
        self.doc.addSubview_(
            _button(
                "Send Test",
                self._frame_for(DOC_WIDTH - MARGIN - 96, top + 80, 96, 26),
                b"sendTest:",
                self,
            )
        )

    def _build_schedule(self, top):
        self._section_label("Schedule", top)
        self.interval_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            self._frame_for(MARGIN, top + 26, 160, 26), False
        )
        self.interval_popup.addItemsWithTitles_([title for title, _ in INTERVALS])
        current = int(self.cfg.get("app", {}).get("interval_minutes", 60))
        index = next(
            (i for i, (_, value) in enumerate(INTERVALS) if value == current), 2
        )
        self.interval_popup.selectItemAtIndex_(index)
        self.doc.addSubview_(self.interval_popup)

        self.login_check = _checkbox(
            "Start automatically when I log in",
            self._frame_for(MARGIN + 180, top + 30, 320, 20),
            b"",
            self,
        )
        self.login_check.setState_(1 if self.cfg["app"].get("launch_at_login") else 0)
        self.doc.addSubview_(self.login_check)

    # -- helpers -----------------------------------------------------------

    def show(self):
        self.window.makeKeyAndOrderFront_(None)
        from AppKit import NSApp

        NSApp.activateIgnoringOtherApps_(True)

    def close(self):
        self.window.orderOut_(None)

    def domain(self):
        return self.cfg.get("domain") or "brightspace.rug.nl"

    def cookies_path(self):
        return config_mod.resolve_cookies_file(self.cfg)

    def _show_alert(self, title, text):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(text)
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    def _maybe_load(self):
        if self.controller.token_ready():
            self.signed_in = True
            self.account_name = self.controller.account_name
            self.course_status = "Loading courses…"
            self._rebuild()
            self._fetch_courses()

    def _fetch_courses(self):
        workers.start_courses(
            self.domain(),
            self.cookies_path(),
            on_done=self._courses_loaded,
            on_error=self._courses_failed,
            post=self._post,
        )

    # -- callbacks ---------------------------------------------------------

    def _on_login_line(self, line):
        if getattr(self, "signin_status", None) is not None:
            self.signin_status.setStringValue_(line[:70])

    def _on_login_done(self, name):
        self.busy = False
        self.signed_in = True
        self.account_name = name
        self.course_status = "Loading courses…"
        self.controller.account_name = name
        self._rebuild()
        self._fetch_courses()

    def _on_login_failed(self, message):
        self.busy = False
        self.course_status = "Sign-in failed."
        self._rebuild()
        self._show_alert("Sign-in failed", message)

    def _courses_loaded(self, courses):
        self.courses = courses
        self.signed_in = True
        self.busy = False
        if not self._ids_initialized:
            configured = self.cfg.get("courses", {}).get("ids") or []
            if configured:
                self.selected_ids = set(configured)
            else:
                self.selected_ids = {course["id"] for course in courses}
            self._ids_initialized = True
        self.course_status = ""
        self._rebuild()

    def _courses_failed(self, message):
        self.course_status = "Sign in to load your courses."
        self._rebuild()

    # -- actions -----------------------------------------------------------

    def chooseFolder_(self, sender):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(False)
        panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False)
        panel.setCanCreateDirectories_(True)
        panel.setPrompt_("Choose")
        panel.setMessage_("Where should Brightspace Sync save your course material?")
        current = Path(os.path.expanduser(self.path_field.stringValue()))
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(current.parent)))
        if panel.runModal() == 1:
            self.path_field.setStringValue_(panel.URL().path())

    def signIn_(self, sender):
        self._start_sign_in()

    def _start_sign_in(self):
        if self.busy:
            return
        allow_fallback = False
        if platform.find_chrome() is None:
            choice = platform.confirm_login_without_chrome()
            if choice != "continue":
                return
            allow_fallback = True
        self.busy = True
        self.course_status = (
            "Opening your browser…" if allow_fallback else "Opening Chrome…"
        )
        self._rebuild()
        workers.start_login(
            self.domain(),
            self.cookies_path(),
            allow_fallback=allow_fallback,
            on_line=self._on_login_line,
            on_done=self._on_login_done,
            on_error=self._on_login_failed,
            post=self._post,
        )

    def selectAll_(self, sender):
        self.selected_ids = {course["id"] for course in self.courses}
        for box in self.course_buttons.values():
            box.setState_(1)

    def selectNone_(self, sender):
        self.selected_ids = set()
        for box in self.course_buttons.values():
            box.setState_(0)

    def imessageToggled_(self, sender):
        if self.imessage_check.state() != 1 or self.imessage_field.stringValue().strip():
            return
        self.imessage_field.setStringValue_("Looking up your address…")

        def work():
            handle = platform.detect_imessage_handle()
            self._post(self._set_imessage_handle, handle)

        import threading

        threading.Thread(target=work, daemon=True).start()

    def _set_imessage_handle(self, handle):
        self.imessage_field.setStringValue_(handle or "")

    def sendTest_(self, sender):
        self._apply()
        self._show_alert("Test sent", "Check your notifications (and iMessage if enabled).")
        workers.start_test_notify(
            self.cfg["notify"], on_done=lambda: None, post=self._post
        )

    def _apply(self):
        self.cfg["out_dir"] = self.path_field.stringValue().strip() or "~/Brightspace"
        self.cfg["notify"]["macos"] = self.macos_check.state() == 1
        self.cfg["notify"]["imessage"] = {
            "enabled": self.imessage_check.state() == 1,
            "recipient": self.imessage_field.stringValue().strip(),
        }
        self.cfg["courses"]["ids"] = [
            course_id
            for course_id, box in self.course_buttons.items()
            if box.state() == 1
        ]
        self.cfg["app"]["interval_minutes"] = INTERVALS[
            self.interval_popup.indexOfSelectedItem()
        ][1]
        self.cfg["app"]["launch_at_login"] = self.login_check.state() == 1

    def primaryAction_(self, sender):
        if self.mode == "setup" and not self.signed_in:
            self._start_sign_in()
            return
        self._apply()
        if self.mode == "setup":
            self.cfg["app"]["setup_complete"] = True
            config_mod.save(self.cfg)
            self.close()
            self.controller.after_setup()
        else:
            config_mod.save(self.cfg)
            self.close()
            self.controller.after_prefs_saved()

    def secondaryAction_(self, sender):
        if self.mode == "setup":
            self.controller.quit_app()
        else:
            self.close()

    def windowShouldClose_(self, sender):
        if self.mode == "setup":
            self.controller.quit_app()
        return True
