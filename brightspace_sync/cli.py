"""Command line interface for brightspace-sync."""

from __future__ import annotations

import argparse
import getpass
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from . import auth, config as config_mod
from .api import BrightspaceClient
from .notify import Notifier
from .state import State
from .sync import notify_throttled, run_sync

AGENT_LABEL = "com.{user}.brightspace-sync"
LOG_DIR = Path.home() / "Library" / "Logs" / "brightspace-sync"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


def _project_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _domain(args, cfg) -> str:
    if args.domain:
        return args.domain
    domain = cfg.get("domain")
    if not domain:
        domain = input("Brightspace domain (e.g. brightspace.rug.nl): ").strip()
        cfg["domain"] = domain
        config_mod.save(cfg)
    return domain


def cmd_login(args) -> int:
    cfg = config_mod.load()
    domain = _domain(args, cfg)
    print(f"Logging in to {domain} ...")
    token = auth.ensure_token(
        domain,
        force_login=True,
        cookies_path=config_mod.resolve_cookies_file(cfg),
    )
    who = BrightspaceClient(domain, token).whoami()
    name = " ".join(
        filter(None, [who.get("FirstName"), who.get("LastName")])
    ) or who.get("UniqueName")
    print(f"Logged in as {name} ({who.get('UniqueName')}).")
    return 0


def cmd_sync(args) -> int:
    cfg = config_mod.load()
    cfg["domain"] = _domain(args, cfg)
    try:
        result = run_sync(
            cfg,
            out_dir=Path(args.out).expanduser() if args.out else None,
            dry_run=args.dry_run,
            verbose=args.verbose,
            allow_interactive=not args.non_interactive,
        )
    except auth.ReauthRequired as exc:
        print(f"error: {exc}", file=sys.stderr)
        if not args.dry_run:
            state = State().load()
            if notify_throttled(
                state,
                "reauth",
                Notifier(cfg.get("notify")),
                "Brightspace login expired",
                "Run 'brightspace-sync login' to sign in again.",
            ):
                state.save()
        return 2
    except Exception as exc:  # noqa: BLE001 - report and exit non-zero
        print(f"error: {exc}", file=sys.stderr)
        if not args.dry_run:
            state = State().load()
            if notify_throttled(
                state,
                "sync_error",
                Notifier(cfg.get("notify")),
                "Brightspace sync failed",
                str(exc)[:200],
            ):
                state.save()
        return 1
    return 0 if result.counts.get("err", 0) == 0 else 1


def cmd_courses(args) -> int:
    cfg = config_mod.load()
    domain = _domain(args, cfg)
    token = auth.ensure_token(domain, allow_interactive=not args.non_interactive)
    courses = BrightspaceClient(domain, token).courses()
    for course in courses:
        flag = "" if course["active"] else "  (inactive)"
        pin = "* " if course["pinned"] else "  "
        print(f"{pin}{course['name']}{flag}")
    return 0


def cmd_status(args) -> int:
    cfg = config_mod.load()
    domain = cfg.get("domain", "(unset)")
    print(f"Config : {config_mod.CONFIG_PATH}")
    print(f"Domain : {domain}")
    print(f"Output : {config_mod.resolve_out_dir(cfg)}")
    print(f"State  : {State().load().path}")
    cookies = config_mod.resolve_cookies_file(cfg)
    if cookies:
        exists = "present" if cookies.exists() else "missing"
        print(f"Cookies: {cookies} ({exists})")
    if cfg.get("domain"):
        status = auth.token_status(cfg["domain"])
        if status.get("error"):
            print(f"Token  : error - {status['error']}")
        elif not status.get("has_token"):
            print("Token  : none (run 'brightspace-sync login')")
        else:
            left = int(status.get("expires_at", 0) - time.time())
            state = "expired" if status.get("expired") else f"valid ~{left // 60} min"
            refresh = "yes" if status.get("has_refresh_token") else "no"
            print(f"Token  : {state} (refresh token: {refresh})")
    summary = State().load().summary()
    print(
        "Tracked: "
        f"{summary['courses']} courses, {summary['files']} files, "
        f"{summary['announcements']} announcements, "
        f"{summary['assignments']} assignments, {summary['grades']} grades"
    )
    if summary.get("last_run"):
        print(f"Last run: {time.strftime('%Y-%m-%d %H:%M', time.localtime(summary['last_run']))}")
    return 0


def cmd_test_notify(args) -> int:
    cfg = config_mod.load()
    Notifier(cfg.get("notify"), verbose=True).send(
        "Brightspace sync", "Test notification - everything is wired up."
    )
    print("Sent test notification.")
    return 0


def _plist_path() -> Path:
    return LAUNCH_AGENTS / f"{AGENT_LABEL.format(user=getpass.getuser())}.plist"


def cmd_install_agent(args) -> int:
    user = getpass.getuser()
    label = AGENT_LABEL.format(user=user)
    project = _project_dir()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)

    plist = {
        "Label": label,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "brightspace_sync",
            "sync",
            "--non-interactive",
        ],
        "WorkingDirectory": str(project),
        "EnvironmentVariables": {"PYTHONPATH": str(project)},
        "StartInterval": int(args.interval),
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_DIR / "sync.log"),
        "StandardErrorPath": str(LOG_DIR / "sync.err.log"),
    }
    path = _plist_path()
    path.write_bytes(plistlib.dumps(plist))
    print(f"Wrote {path}")

    domain_target = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", f"{domain_target}/{label}"],
        check=False,
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", domain_target, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or "launchctl bootstrap failed", file=sys.stderr)
        return 1
    subprocess.run(
        ["launchctl", "enable", f"{domain_target}/{label}"], check=False
    )
    print(f"Loaded agent '{label}' (every {int(args.interval) // 60} min).")
    return 0


def cmd_uninstall_agent(args) -> int:
    user = getpass.getuser()
    label = AGENT_LABEL.format(user=user)
    domain_target = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", f"{domain_target}/{label}"],
        check=False,
        capture_output=True,
    )
    path = _plist_path()
    if path.exists():
        path.unlink()
        print(f"Removed {path}")
    else:
        print("No agent installed.")
    return 0


def cmd_app(args) -> int:
    from .gui.app import main as gui_main

    return gui_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brightspace-sync",
        description="Keep Brightspace course material downloaded and notify on changes.",
    )
    parser.add_argument("--domain", help="Brightspace domain override")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Log in via SSO and cache a refresh token")

    p_sync = sub.add_parser("sync", help="Download new material and notify")
    p_sync.add_argument("--out", help="Download root (default: from config)")
    p_sync.add_argument("--dry-run", action="store_true", help="Do not write or notify")
    p_sync.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt; fail if re-login is required",
    )

    p_courses = sub.add_parser("courses", help="List enrolled courses")
    p_courses.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt; fail if re-login is required",
    )

    sub.add_parser("status", help="Show config, token and state")

    sub.add_parser("test-notify", help="Send a test notification")

    p_install = sub.add_parser("install-agent", help="Install the hourly launchd agent")
    p_install.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="Seconds between runs (default: 3600)",
    )

    sub.add_parser("uninstall-agent", help="Remove the launchd agent")

    sub.add_parser("app", help="Launch the native menu bar app")
    return parser


def main(argv: list[str] | None = None) -> int:
    config_mod.load_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "login": cmd_login,
        "sync": cmd_sync,
        "courses": cmd_courses,
        "status": cmd_status,
        "test-notify": cmd_test_notify,
        "install-agent": cmd_install_agent,
        "uninstall-agent": cmd_uninstall_agent,
        "app": cmd_app,
    }
    return handlers[args.command](args)
