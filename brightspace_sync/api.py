"""Brightspace API client: Pulse GraphQL (navigation) + Valence REST (bytes).

GraphQL at ``usergraph.api.brightspace.com`` is used for enrollments and the
course content tree.  The tenant's own Valence REST API is used for file
bytes, grades, assignments and announcements, because those are not reliably
exposed by the Pulse GraphQL schema.

Everything here works with the scopes granted to the Pulse OAuth client
(``core:*:* content:topics:read content:file:read``).  Endpoints that need
extra scopes (quiz/discussion/classlist/submissions) are intentionally not
implemented.
"""

from __future__ import annotations

import http.cookiejar
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

GRAPHQL_ENDPOINT = "https://usergraph.api.brightspace.com/graphql"

# Pulse pins specific Valence versions.  Content/grades work on 1.40; news and
# dropbox folders are documented against 1.74.  Both are supported on RUG.
LE_CONTENT = "1.40"
LE_MISC = "1.74"
LP = "1.43"

CONTENT_ITEM_FRAGMENT = """
  __typename
  ... on ContentModule { id title descriptionHtmlRichContent }
  ... on ContentTopic {
    id title type fileName viewHref downloadHref pdfHref modifiedDate
  }
"""

ENROLLMENT_QUERY = """
query($id: String) {
  enrollmentPage(id: $id) {
    next
    enrollments {
      pinned
      organization { id name isActive }
    }
  }
}
"""

ROOT_QUERY = f"""
query($id: String!) {{
  contentRoot(organizationId: $id) {{
    modules {{
      id title descriptionHtmlRichContent
      children {{ {CONTENT_ITEM_FRAGMENT} }}
    }}
  }}
}}
"""

MODULE_QUERY = f"""
query($id: String!) {{
  contentModule(moduleId: $id) {{
    id title
    children {{ {CONTENT_ITEM_FRAGMENT} }}
  }}
}}
"""

_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str) -> str:
    """Make a title safe to use as a single filesystem path component."""
    name = _INVALID_FS.sub("_", name or "").strip().rstrip(".")
    return name or "_"


def parse_topic_id(topic_id: str) -> tuple[str, str]:
    """Split a GraphQL topic id into ``(ouId, topicId)``.

    Ids look like ``https://<tenant>/<ouId>/content/topics/<topicId>`` but we
    treat them defensively: the segment after ``topics`` is the topic id and
    the org unit is the segment before ``content`` (falling back to the
    segment directly before ``topics``).
    """
    parts = [p for p in urlparse(topic_id).path.strip("/").split("/") if p]
    if "topics" in parts:
        idx = parts.index("topics")
        topic = parts[idx + 1] if idx + 1 < len(parts) else parts[-1]
        if idx >= 2 and parts[idx - 1] == "content":
            return parts[idx - 2], topic
        if idx >= 1:
            return parts[idx - 1], topic
    if len(parts) >= 2:
        return parts[0], parts[-1]
    raise ValueError(f"Cannot parse topic id: {topic_id!r}")


class BrightspaceClient:
    def __init__(
        self,
        domain: str,
        token: str,
        *,
        timeout: int = 30,
        cookies_file: str | Path | None = None,
    ):
        self.domain = domain
        self.token = token
        self.timeout = timeout
        self.cookies_file = Path(cookies_file) if cookies_file else None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )
        self._browser_session: requests.Session | None = None

    # -- low level ---------------------------------------------------------

    def gql(self, query: str, variables: dict | None = None) -> dict:
        r = self.session.post(
            GRAPHQL_ENDPOINT,
            json={"query": query, "variables": variables or {}},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise RuntimeError(f"GraphQL HTTP {r.status_code}: {r.text[:300]}")
        body = r.json() or {}
        if "errors" in body:
            raise RuntimeError(f"GraphQL errors: {body['errors']}")
        return body.get("data") or {}

    def rest(self, path: str, **kwargs) -> requests.Response:
        url = f"https://{self.domain}{path}"
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(url, **kwargs)

    def rest_json(self, path: str):
        r = self.rest(path)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} for {path}: {r.text[:200]}")
        return r.json()

    # -- browser-cookie access (private file area) -------------------------

    def _browser(self) -> requests.Session | None:
        """Session using exported browser cookies, or None if unavailable.

        The course reader and other "course files" are served only to a
        logged-in browser session, not to our OAuth token.
        """
        if self._browser_session is not None:
            return self._browser_session
        if not self.cookies_file or not self.cookies_file.exists():
            return None
        try:
            text = self.cookies_file.read_text(errors="replace").strip()
        except OSError:
            return None
        if not text:
            return None
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125 Safari/537.36"
                ),
                "Accept-Encoding": "identity",
            }
        )
        # A Netscape-format export has tab-separated fields; anything else is
        # treated as a raw "Cookie:" header value pasted from the browser.
        if "\t" in text:
            try:
                jar = http.cookiejar.MozillaCookieJar(str(self.cookies_file))
                jar.load(ignore_discard=True, ignore_expires=True)
                session.cookies = jar
            except (OSError, http.cookiejar.LoadError):
                return None
        else:
            session.headers["Cookie"] = text
        self._browser_session = session
        return session

    def has_browser_cookies(self) -> bool:
        return self._browser() is not None

    def download_browser_file(
        self, url: str, dest_dir: Path, filename_hint: str | None = None
    ) -> dict:
        session = self._browser()
        if session is None:
            return {"status": "nocookie", "filename": ""}
        if not url.startswith("http"):
            url = f"https://{self.domain}{url}"
        try:
            r = session.get(
                url, stream=True, timeout=self.timeout, allow_redirects=True
            )
        except requests.RequestException as exc:
            return {"status": "err", "filename": "", "detail": str(exc)}
        ctype = r.headers.get("Content-Type", "").lower()
        if (
            r.status_code != 200
            or "text/html" in ctype
            or "signon" in r.url
        ):
            r.close()
            return {
                "status": "nocookie",
                "filename": "",
                "detail": f"HTTP {r.status_code} {ctype[:30]}",
            }
        filename = self._filename_from_headers(r, filename_hint or "file")
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / filename
        remote_size = int(r.headers.get("Content-Length") or 0)
        if path.exists() and remote_size and path.stat().st_size == remote_size:
            r.close()
            return {"status": "skip", "filename": filename, "size": remote_size}
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(64 * 1024):
                    if chunk:
                        fh.write(chunk)
            tmp.replace(path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            return {"status": "err", "filename": filename, "detail": str(exc)}
        finally:
            r.close()
        return {
            "status": "get",
            "filename": filename,
            "size": path.stat().st_size,
        }

    # -- identity / enrollments -------------------------------------------

    def whoami(self) -> dict:
        return self.rest_json(f"/d2l/api/lp/{LP}/users/whoami")

    def courses(self) -> list[dict]:
        out: list[dict] = []
        next_id: str | None = None
        while True:
            page = self.gql(ENROLLMENT_QUERY, {"id": next_id})["enrollmentPage"]
            for entry in page["enrollments"]:
                org = entry["organization"]
                out.append(
                    {
                        "id": org["id"],
                        "num": org["id"].rsplit("/", 1)[-1],
                        "name": org["name"],
                        "pinned": entry["pinned"],
                        "active": org["isActive"],
                    }
                )
            next_id = page.get("next")
            if not next_id:
                break
        out.sort(key=lambda c: (not c["pinned"], not c["active"], c["name"].lower()))
        return out

    # -- content tree ------------------------------------------------------

    def _root_modules(self, course_id: str) -> list[dict]:
        return self.gql(ROOT_QUERY, {"id": course_id})["contentRoot"]["modules"]

    def _module_children(self, module_id: str) -> list[dict]:
        data = self.gql(MODULE_QUERY, {"id": module_id}).get("contentModule") or {}
        return data.get("children") or []

    def walk_course(self, course_id: str):
        """Yield ``(folder_parts, topic)`` for every topic in a course.

        ``folder_parts`` is the sanitized module path below the course root.
        """
        for module in self._root_modules(course_id):
            yield from self._walk(module, [])

    def _walk(self, module: dict, path: list[str]):
        here = path + [sanitize(module["title"])]
        children = module.get("children")
        if children is None:
            children = self._module_children(module["id"])
        for child in children:
            if child["__typename"] == "ContentModule":
                yield from self._walk(child, here)
            else:
                yield here, child

    def walk_course_modules(self, course_id: str):
        """Yield ``(folder_parts, module)`` for modules with a description.

        Some material (e.g. a course reader) is only linked from a module's
        rich description, never attached as a topic.
        """
        for module in self._root_modules(course_id):
            yield from self._walk_modules(module, [])

    def _walk_modules(self, module: dict, path: list[str]):
        here = path + [sanitize(module["title"])]
        if (module.get("descriptionHtmlRichContent") or "").strip():
            yield here, module
        children = module.get("children")
        if children is None:
            children = self._module_children(module["id"])
        for child in children:
            if child["__typename"] == "ContentModule":
                yield from self._walk_modules(child, here)

    # -- topic download ----------------------------------------------------

    def _topic_meta(self, ou_id: str, topic_id: str) -> dict:
        return self.rest_json(
            f"/d2l/api/le/{LE_CONTENT}/{ou_id}/content/topics/{topic_id}"
        )

    def download_topic(
        self,
        topic: dict,
        dest_dir: Path,
        *,
        prev: dict | None = None,
    ) -> dict:
        """Download one topic into ``dest_dir``.

        Returns a dict with ``status`` in ``get``/``skip``/``link``/``err``,
        ``filename``, ``size``, ``modified`` and ``new`` (True if the topic had
        never been recorded before).
        """
        ou_id, topic_id = parse_topic_id(topic["id"])
        base = f"/d2l/api/le/{LE_CONTENT}/{ou_id}/content/topics/{topic_id}"
        try:
            meta = self._topic_meta(ou_id, topic_id)
        except RuntimeError as exc:
            return {"status": "err", "filename": "", "detail": str(exc)}

        title = sanitize(meta.get("Title") or topic.get("title") or "topic")
        modified = meta.get("ModifiedDate") or topic.get("modifiedDate")
        type_id = str(meta.get("TypeIdentifier") or "")
        topic_type = meta.get("TopicType")
        is_file = type_id == "File" or (not type_id and topic_type == 1)

        dest_dir.mkdir(parents=True, exist_ok=True)

        if not is_file:
            filename = f"{title}.url"
            path = dest_dir / filename
            url = self._shortcut_url(meta)
            if not path.exists() or path.read_text(errors="replace") != (
                f"[InternetShortcut]\nURL={url}\n"
            ):
                path.write_text(f"[InternetShortcut]\nURL={url}\n")
            return {
                "status": "link",
                "filename": filename,
                "size": path.stat().st_size,
                "modified": modified,
                "new": prev is None,
            }

        try:
            r = self.rest(
                f"{base}/file",
                stream=True,
                headers={"Accept-Encoding": "identity"},
            )
        except requests.RequestException as exc:
            return {"status": "err", "filename": "", "detail": str(exc)}
        if r.status_code != 200:
            return {
                "status": "err",
                "filename": "",
                "detail": f"HTTP {r.status_code}",
            }

        filename = self._filename_from_headers(r, title)
        path = dest_dir / filename
        remote_size = int(r.headers.get("Content-Length") or 0)

        prev_modified = (prev or {}).get("modified")
        content_changed = bool(prev) and prev_modified != modified
        # If the server omits Content-Length, fall back to "exists and
        # unchanged" rather than re-downloading on every run.
        size_matches = path.exists() and (
            remote_size == 0 or path.stat().st_size == remote_size
        )

        if size_matches and not content_changed:
            r.close()
            return {
                "status": "skip",
                "filename": filename,
                "size": remote_size,
                "modified": modified,
                "new": False,
                "url": meta.get("Url"),
            }

        tmp = path.with_suffix(path.suffix + ".part")
        try:
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(64 * 1024):
                    if chunk:
                        fh.write(chunk)
            tmp.replace(path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            return {"status": "err", "filename": filename, "detail": str(exc)}
        finally:
            r.close()

        return {
            "status": "get",
            "filename": filename,
            "size": path.stat().st_size,
            "modified": modified,
            "new": prev is None,
            "url": meta.get("Url"),
        }

    def _shortcut_url(self, meta: dict) -> str:
        url = meta.get("Url") or ""
        if url.startswith("http"):
            return url
        if url.startswith("/"):
            return f"https://{self.domain}{url}"
        return f"https://{self.domain}/"

    @staticmethod
    def _filename_from_headers(response: requests.Response, fallback: str) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        match = re.search(
            r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disposition
        )
        if match:
            return sanitize(unquote(match.group(1)))
        if Path(fallback).suffix:
            return sanitize(fallback)
        return f"{sanitize(fallback)}.bin"

    # -- announcements / assignments / grades ------------------------------

    def news(self, ou_id: str) -> list[dict]:
        try:
            return self.rest_json(f"/d2l/api/le/{LE_MISC}/{ou_id}/news/") or []
        except RuntimeError:
            return []

    def assignments(self, ou_id: str) -> list[dict]:
        try:
            return self.rest_json(
                f"/d2l/api/le/{LE_MISC}/{ou_id}/dropbox/folders/"
            ) or []
        except RuntimeError:
            return []

    def grades(self, ou_id: str) -> list[dict]:
        try:
            return self.rest_json(
                f"/d2l/api/le/{LE_CONTENT}/{ou_id}/grades/values/myGradeValues/"
            ) or []
        except RuntimeError:
            return []
