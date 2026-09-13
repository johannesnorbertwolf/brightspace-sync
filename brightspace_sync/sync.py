"""Core sync: download course files and detect new announcements/assignments/grades."""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import auth, config as config_mod
from .api import BrightspaceClient, sanitize
from .notify import Notifier
from .state import State


@dataclass
class Event:
    kind: str
    course: str
    detail: str


@dataclass
class SyncResult:
    events: list[Event] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    courses: int = 0
    first_run: bool = False
    cookie_warning: bool = False


def notify_throttled(
    state: State,
    key: str,
    notifier: Notifier,
    title: str,
    message: str,
    *,
    hours: float = 12,
) -> bool:
    """Send a notification at most once per ``hours``.

    Prevents an hourly job from nagging every run while a problem persists.
    """
    now = time.time()
    last = state.data.setdefault("notifications", {}).get(key, 0)
    if now - last < hours * 3600:
        return False
    notifier.send(title, message)
    state.data["notifications"][key] = now
    return True


def _format_grade(grade: dict) -> str:
    displayed = grade.get("DisplayedGrade")
    if displayed:
        return str(displayed)
    numerator = grade.get("PointsNumerator")
    denominator = grade.get("PointsDenominator")
    if numerator is not None and denominator is not None:
        return f"{numerator}/{denominator}"
    return "posted"


def _format_due(value) -> str:
    if not value:
        return "no due date"
    return str(value).replace("T", " ")[:16]


def run_sync(
    cfg: dict,
    *,
    out_dir: Path | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    allow_interactive: bool = False,
    allow_fallback: bool = True,
    notifier: Notifier | None = None,
) -> SyncResult:
    domain = cfg["domain"]
    out_root = out_dir or config_mod.resolve_out_dir(cfg)
    track = cfg.get("track") or {}
    notifier = notifier or Notifier(cfg.get("notify"), verbose=verbose)

    def log(message: str) -> None:
        print(message, flush=True)

    token = auth.ensure_token(
        domain,
        allow_interactive=allow_interactive,
        allow_fallback=allow_fallback,
        cookies_path=config_mod.resolve_cookies_file(cfg),
    )
    client = BrightspaceClient(
        domain, token, cookies_file=config_mod.resolve_cookies_file(cfg)
    )
    state = State().load()
    result = SyncResult()
    result.first_run = not state.data["courses"] and not state.data["files"]

    courses = [c for c in client.courses() if config_mod.course_selected(cfg, c)]
    result.courses = len(courses)
    log(f"Brightspace sync for {domain}: {len(courses)} course(s) selected")
    if dry_run:
        log("Dry run: nothing will be written or notified.")

    counts = {"get": 0, "skip": 0, "link": 0, "err": 0, "new": 0, "updated": 0}
    seen_ids = {c["id"] for c in courses}
    for stale in list(state.data["files"]):
        if stale not in seen_ids:
            del state.data["files"][stale]

    for course in courses:
        course_id = course["id"]
        course_name = course["name"]
        entry = state.course(course_id)
        entry["name"] = course_name
        if entry.get("first_seen") is None:
            entry["first_seen"] = int(time.time())
        log(f"\n=== {course_name} ===")

        course_prefix = None
        if track.get("files", True):
            course_prefix = _sync_files(
                client, course, out_root, state, result, counts, dry_run, log
            )
            _sync_descriptions(
                client,
                course,
                out_root,
                state,
                result,
                counts,
                dry_run,
                log,
                course_prefix=course_prefix,
            )

        if track.get("announcements", True):
            _sync_announcements(client, course, state, result, dry_run)
        if track.get("assignments", True):
            _sync_assignments(client, course, state, result, dry_run)
        if track.get("grades", True):
            _sync_grades(client, course, state, result, dry_run)

        if not dry_run:
            for name in _write_reports(state, course, out_root):
                log(f"  ~ {name} (summary updated)")

    result.counts = counts
    if not dry_run:
        state.save()

    log(
        f"\nFiles: {counts['new']} new, {counts['updated']} updated, "
        f"{counts['skip']} up-to-date, {counts['link']} links, {counts['err']} errors"
    )
    if result.events:
        log(f"Changes detected: {len(result.events)}")
        for event in result.events:
            log(f"  - [{event.kind}] {event.course}: {event.detail}")

    should_notify = bool(result.events) and (
        not result.first_run or cfg.get("notify_initial", False)
    )
    if should_notify and not dry_run:
        title, message = _format_notification(result.events)
        notifier.send(title, message)
    elif result.first_run and result.events:
        log("First run: baseline recorded, notifications suppressed.")

    if result.cookie_warning and not dry_run:
        if notify_throttled(
            state,
            "cookie_expired",
            notifier,
            "Brightspace: reader needs a fresh login",
            "Run 'brightspace-sync login' to restore access to the reader "
            "and other browser-only files.",
        ):
            log("Notified: reader files need a fresh login.")
            state.save()

    return result


def _sync_files(
    client: BrightspaceClient,
    course: dict,
    out_root: Path,
    state: State,
    result: SyncResult,
    counts: dict,
    dry_run: bool,
    log,
) -> str | None:
    course_dir = out_root / sanitize(course["name"])
    previous = state.files(course["id"])
    enforced_prefix: str | None = None

    for folder_parts, topic in client.walk_course(course["id"]):
        topic_id = topic["id"]
        prev = previous.get(topic_id)
        dest_dir = course_dir.joinpath(*folder_parts)

        if dry_run:
            if prev is None:
                counts["new"] += 1
                result.events.append(
                    Event("file-new", course["name"], topic.get("title", "?"))
                )
            else:
                counts["skip"] += 1
            continue

        try:
            outcome = client.download_topic(topic, dest_dir, prev=prev)
        except Exception as exc:  # keep one bad topic from killing the run
            counts["err"] = counts.get("err", 0) + 1
            log(f"  ! {topic.get('title')}: {exc}")
            continue
        counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1

        if not enforced_prefix:
            raw_url = outcome.get("url") or ""
            if raw_url.startswith("/content/enforced/"):
                parts = raw_url.split("/")
                if len(parts) >= 4:
                    enforced_prefix = "/".join(parts[:4]) + "/"

        if outcome["status"] == "err":
            log(f"  ! {topic.get('title')}: {outcome.get('detail', 'error')}")
            continue

        if outcome["status"] == "get":
            if outcome["new"]:
                counts["new"] += 1
                result.events.append(
                    Event("file-new", course["name"], outcome["filename"])
                )
                log(f"  + {Path(*folder_parts) / outcome['filename']}")
            else:
                counts["updated"] += 1
                result.events.append(
                    Event("file-updated", course["name"], outcome["filename"])
                )
                log(f"  ~ {Path(*folder_parts) / outcome['filename']} (updated)")
        elif outcome["status"] == "link":
            if outcome["new"]:
                counts["new"] += 1
                result.events.append(
                    Event("link-new", course["name"], outcome["filename"])
                )

        rel = Path(*folder_parts) / outcome["filename"] if outcome["filename"] else ""
        previous[topic_id] = {
            "title": topic.get("title"),
            "path": str(rel),
            "size": outcome.get("size"),
            "modified": outcome.get("modified"),
        }

    return enforced_prefix


_LINK_RE = re.compile(r'href="([^"]+)"')


def _extract_links(html: str | None) -> list[str]:
    text = (html or "").replace("&amp;", "&")
    out: list[str] = []
    for href in _LINK_RE.findall(text):
        href = href.strip()
        if href and href not in out:
            out.append(href)
    return out


def _classify_link(href: str) -> str:
    if href.startswith("/content/enforced/"):
        return "file"
    if "/quickLink/" in href:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        if query.get("type", [""])[0] == "coursefile":
            return "file"
        return "tool"
    if href.startswith("http"):
        return "external"
    return "skip"


def _link_name(href: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    if query.get("fileId"):
        return sanitize(query["fileId"][0])
    parsed = urllib.parse.urlparse(href)
    base = urllib.parse.unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
    if base:
        return sanitize(base)
    return sanitize(parsed.netloc) or "link"


def _quicklink_fileid(href: str) -> str | None:
    match = re.search(r"[?&]fileId=([^&]+)", href)
    return match.group(1) if match else None


def _enforced_prefix(links: list[str]) -> str | None:
    for href in links:
        if href.startswith("/content/enforced/"):
            parts = href.split("/")
            if len(parts) >= 4:
                return "/".join(parts[:4]) + "/"
    return None


def _write_shortcut(dest_dir: Path, name: str, url: str) -> None:
    if not url.startswith("http"):
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / (name if name.endswith(".url") else f"{name}.url")
    content = f"[InternetShortcut]\nURL={url}\n"
    if not path.exists() or path.read_text(errors="replace") != content:
        path.write_text(content)


def _sync_descriptions(
    client: BrightspaceClient,
    course: dict,
    out_root: Path,
    state: State,
    result: SyncResult,
    counts: dict,
    dry_run: bool,
    log,
    course_prefix: str | None = None,
) -> None:
    """Download files linked from module descriptions (e.g. the course reader).

    These live in Brightspace's browser-only file area, so they need exported
    browser cookies.  Without cookies we save clickable shortcuts instead.
    """
    previous = state.files(course["id"])
    course_dir = out_root / sanitize(course["name"])
    warned = False

    for folder_parts, module in client.walk_course_modules(course["id"]):
        links = _extract_links(module.get("descriptionHtmlRichContent"))
        if not links:
            continue
        dest_dir = course_dir.joinpath(*folder_parts)
        prefix = _enforced_prefix(links) or course_prefix
        for href in links:
            kind = _classify_link(href)
            if kind in ("skip", "tool"):
                continue
            if kind == "external":
                target = (
                    href if href.startswith("http") else f"https://{client.domain}{href}"
                )
                if not dry_run:
                    _write_shortcut(dest_dir, _link_name(href), target)
                continue

            key = "desc:" + hashlib.sha1(href.encode()).hexdigest()
            prev = previous.get(key)

            if dry_run:
                if prev is None:
                    counts["new"] += 1
                    result.events.append(
                        Event("file-new", course["name"], _link_name(href))
                    )
                continue

            name = _link_name(href)
            download_url = href
            if "quickLink" in href and prefix:
                raw_id = _quicklink_fileid(href)
                if raw_id:
                    name = sanitize(urllib.parse.unquote_plus(raw_id))
                    download_url = prefix + urllib.parse.quote(name)

            outcome = client.download_browser_file(
                download_url, dest_dir, filename_hint=name
            )
            if outcome["status"] == "nocookie":
                if not warned:
                    log(
                        "  ! Reader/course files need browser cookies "
                        "(set cookies_file); saving links instead."
                    )
                    warned = True
                result.cookie_warning = True
                fallback = (
                    href if href.startswith("http") else f"https://{client.domain}{href}"
                )
                _write_shortcut(dest_dir, name, fallback)
                continue
            if outcome["status"] == "err":
                counts["err"] += 1
                log(f"  ! {name}: {outcome.get('detail', 'error')}")
                continue

            counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1
            if outcome["status"] in ("get", "skip"):
                # The real file is present; drop any clickable fallback.
                (dest_dir / f"{name}.url").unlink(missing_ok=True)
            if outcome["status"] == "get":
                rel = Path(*folder_parts) / outcome["filename"]
                if prev is None:
                    counts["new"] += 1
                    result.events.append(
                        Event("file-new", course["name"], outcome["filename"])
                    )
                    log(f"  + {rel}")
                else:
                    counts["updated"] += 1
                    result.events.append(
                        Event("file-updated", course["name"], outcome["filename"])
                    )
                    log(f"  ~ {rel} (updated)")
                previous[key] = {
                    "title": module.get("title"),
                    "path": str(rel),
                    "size": outcome.get("size"),
                    "modified": None,
                }


def _sync_announcements(client, course, state, result, dry_run) -> None:
    known = state.announcements(course["id"])
    for item in client.news(course["num"]):
        news_id = str(item.get("Id"))
        if news_id in known:
            continue
        title = item.get("Title") or "(untitled)"
        result.events.append(Event("announcement", course["name"], title))
        if not dry_run:
            known[news_id] = {
                "title": title,
                "created": item.get("CreatedDate"),
            }


def _sync_assignments(client, course, state, result, dry_run) -> None:
    known = state.assignments(course["id"])
    for item in client.assignments(course["num"]):
        folder_id = str(item.get("Id"))
        name = item.get("Name") or "(untitled)"
        due = item.get("DueDate")
        old = known.get(folder_id)
        if old is None:
            result.events.append(
                Event("assignment-new", course["name"], f"{name} (due {_format_due(due)})")
            )
        elif old.get("due") != due:
            result.events.append(
                Event(
                    "assignment-changed",
                    course["name"],
                    f"{name}: due {_format_due(old.get('due'))} -> {_format_due(due)}",
                )
            )
        if not dry_run:
            known[folder_id] = {"name": name, "due": due}


def _sync_grades(client, course, state, result, dry_run) -> None:
    known = state.grades(course["id"])
    for item in client.grades(course["num"]):
        grade_id = str(item.get("GradeObjectIdentifier"))
        if grade_id in ("", "None"):
            continue
        name = item.get("GradeObjectName") or "(untitled)"
        value = _format_grade(item)
        old = known.get(grade_id)
        if old is None:
            result.events.append(
                Event("grade-new", course["name"], f"{name}: {value}")
            )
        elif old.get("value") != value:
            result.events.append(
                Event(
                    "grade-changed",
                    course["name"],
                    f"{name}: {old.get('value')} -> {value}",
                )
            )
        if not dry_run:
            known[grade_id] = {"name": name, "value": value}


def _write_reports(state: State, course: dict, out_root: Path) -> list[str]:
    """Write human-readable summaries of grades/assignments/announcements.

    Only rewrites files whose content changed, so re-runs stay quiet.
    Returns the list of files that were updated.
    """
    course_dir = out_root / sanitize(course["name"])
    reports: list[tuple[str, str]] = []

    grades = state.grades(course["id"])
    if grades:
        lines = [f"# Grades - {course['name']}", ""]
        for item in sorted(grades.values(), key=lambda x: x.get("name") or ""):
            lines.append(f"- {item.get('name')}: {item.get('value')}")
        reports.append(("Grades.md", "\n".join(lines) + "\n"))

    assignments = state.assignments(course["id"])
    if assignments:
        lines = [f"# Assignments - {course['name']}", ""]
        ordered = sorted(
            assignments.values(), key=lambda x: x.get("due") or "9999"
        )
        for item in ordered:
            lines.append(
                f"- {item.get('name')} - due {_format_due(item.get('due'))}"
            )
        reports.append(("Assignments.md", "\n".join(lines) + "\n"))

    announcements = state.announcements(course["id"])
    if announcements:
        lines = [f"# Announcements - {course['name']}", ""]
        ordered = sorted(
            announcements.values(), key=lambda x: x.get("created") or ""
        )
        for item in ordered:
            lines.append(
                f"- {item.get('title')} ({_format_due(item.get('created'))})"
            )
        reports.append(("Announcements.md", "\n".join(lines) + "\n"))

    if not reports:
        return []

    course_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, content in reports:
        path = course_dir / name
        if not path.exists() or path.read_text(errors="replace") != content:
            path.write_text(content)
            written.append(str(path.relative_to(out_root)))
    return written


def _format_notification(events: list[Event]) -> tuple[str, str]:
    order = [
        ("file-new", "new file"),
        ("link-new", "new link"),
        ("file-updated", "updated file"),
        ("announcement", "announcement"),
        ("assignment-new", "new assignment"),
        ("assignment-changed", "assignment changed"),
        ("grade-new", "new grade"),
        ("grade-changed", "grade changed"),
    ]
    tally: dict[str, int] = {}
    for event in events:
        tally[event.kind] = tally.get(event.kind, 0) + 1

    parts = [f"{tally[kind]} {label}" for kind, label in order if tally.get(kind)]
    message = ", ".join(parts) if parts else "changes detected"

    highlights = [e for e in events if e.kind in ("file-new", "announcement", "grade-new")]
    if highlights:
        shown = ", ".join(f"{e.course}: {e.detail}" for e in highlights[:3])
        message = f"{message} - {shown}"
    return "Brightspace update", message[:250]
