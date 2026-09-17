"""Configuration loading and defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "brightspace-sync"
)
CONFIG_PATH = CONFIG_DIR / "config.json"


def load_env(extra_paths: list[Path] | None = None) -> None:
    """Load simple KEY=VALUE lines from .env files into os.environ.

    Existing environment variables always win.  This keeps the tool
    dependency-free while still supporting a project ``.env``.
    """
    paths = [Path.cwd() / ".env", CONFIG_DIR / ".env"]
    paths.extend(extra_paths or [])
    for path in paths:
        if not path.exists():
            continue
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


DEFAULTS: dict = {
    "domain": "brightspace.rug.nl",
    "out_dir": "~/Brightspace",
    "cookies_file": "~/.config/brightspace-sync/cookies.txt",
    "courses": {
        "ids": [],
        "include": [],
        "exclude": [],
        "include_inactive": False,
    },
    "app": {
        "setup_complete": False,
        "launch_at_login": True,
        "interval_minutes": 60,
    },
    "track": {
        "files": True,
        "descriptions": True,
        "announcements": True,
        "assignments": True,
        "grades": True,
    },
    "notify_initial": False,
    "notify": {
        "macos": True,
        "email": {
            "enabled": False,
            "to": "",
            "from": "",
            "smtp_host": "",
            "smtp_port": 587,
            "username": "",
            "password_env": "BRIGHTSPACE_SMTP_PASSWORD",
            "use_tls": True,
        },
        "webhook": {
            "enabled": False,
            "url_env": "BRIGHTSPACE_WEBHOOK_URL",
        },
        "telegram": {
            "enabled": False,
            "bot_token_env": "BRIGHTSPACE_TELEGRAM_BOT_TOKEN",
            "chat_id_env": "BRIGHTSPACE_TELEGRAM_CHAT_ID",
        },
        "imessage": {
            "enabled": False,
            "recipient": "",
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load(path: Path = CONFIG_PATH) -> dict:
    config = json.loads(json.dumps(DEFAULTS))
    if path.exists():
        try:
            config = _deep_merge(config, json.loads(path.read_text()))
        except (OSError, ValueError):
            pass
    return config


def save(config: dict, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2, sort_keys=True))
    tmp.replace(path)


def resolve_out_dir(config: dict) -> Path:
    return Path(os.path.expanduser(config["out_dir"])).resolve()


def resolve_cookies_file(config: dict) -> Path | None:
    value = config.get("cookies_file")
    if not value:
        return None
    return Path(os.path.expanduser(value))


def course_selected(config: dict, course: dict) -> bool:
    rules = config.get("courses") or {}
    name = course["name"].lower()
    if not rules.get("include_inactive", False) and not course.get("active", True):
        return False
    include = [s.lower() for s in rules.get("include") or []]
    exclude = [s.lower() for s in rules.get("exclude") or []]
    if include and not any(s in name for s in include):
        return False
    if exclude and any(s in name for s in exclude):
        return False
    return True
