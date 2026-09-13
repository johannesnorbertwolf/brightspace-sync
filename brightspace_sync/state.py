"""Persistent state used to detect what is new since the last sync."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

STATE_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    / "brightspace-sync"
)
STATE_PATH = STATE_DIR / "state.json"

_EMPTY: dict = {
    "version": 1,
    "last_run": None,
    "courses": {},
    "files": {},
    "announcements": {},
    "assignments": {},
    "grades": {},
    "notifications": {},
}


class State:
    """A small JSON document with atomic saves.

    Keys are stable server-side identifiers so that renames or reordering do
    not produce false "new" events.
    """

    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self.data = json.loads(json.dumps(_EMPTY))

    def load(self) -> "State":
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text())
            except (OSError, ValueError):
                loaded = {}
            for key, default in _EMPTY.items():
                self.data[key] = loaded.get(key, json.loads(json.dumps(default)))
        return self

    def save(self) -> None:
        self.data["last_run"] = int(time.time())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    # -- convenience accessors --------------------------------------------

    def course(self, course_id: str) -> dict:
        return self.data["courses"].setdefault(
            course_id, {"name": "", "first_seen": None}
        )

    def files(self, course_id: str) -> dict:
        return self.data["files"].setdefault(course_id, {})

    def announcements(self, course_id: str) -> dict:
        return self.data["announcements"].setdefault(course_id, {})

    def assignments(self, course_id: str) -> dict:
        return self.data["assignments"].setdefault(course_id, {})

    def grades(self, course_id: str) -> dict:
        return self.data["grades"].setdefault(course_id, {})

    def summary(self) -> dict:
        return {
            "courses": len(self.data["courses"]),
            "files": sum(len(v) for v in self.data["files"].values()),
            "announcements": sum(
                len(v) for v in self.data["announcements"].values()
            ),
            "assignments": sum(len(v) for v in self.data["assignments"].values()),
            "grades": sum(len(v) for v in self.data["grades"].values()),
            "last_run": self.data.get("last_run"),
        }
