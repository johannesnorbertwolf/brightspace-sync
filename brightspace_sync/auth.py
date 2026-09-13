"""OAuth2 + PKCE authentication for Brightspace.

Students cannot mint their own D2L API keys, but the official Brightspace
Pulse mobile app authenticates with a public OAuth client and a custom
redirect scheme (``brightspacepulse://auth``).  We reuse that client, so the
login flow is the normal university SSO and the resulting refresh token can
be reused for headless, scheduled runs.

The guided login drives the user's installed Google Chrome over the DevTools
Protocol (see :mod:`brightspace_sync.browser`).  This captures the OAuth code
from the redirect AND the browser session cookies needed for browser-only
course files, without bundling a browser or using Playwright.

Access/refresh tokens are cached per tenant under
``~/.cache/brightspace-sync/<tenantId>.json``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import select
import subprocess
import sys
import tempfile
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

LANDLORD = "https://landlord.brightspace.com/v1/tenants"
INSTITUTION_SEARCH = "https://lms-disco.api.brightspace.com/institutions"
AUTHORIZE = "https://auth.brightspace.com/oauth2/auth"
TOKEN = "https://auth.brightspace.com/core/connect/token"

CLIENT_ID = "73b7099f-d148-46f7-95cc-4b957cdf0f75"
REDIRECT_URI = "brightspacepulse://auth"
SCOPE = "core:*:* content:topics:read content:file:read"

CACHE_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    / "brightspace-sync"
)

DEFAULT_COOKIES_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "brightspace-sync"
    / "cookies.txt"
)

BROWSER_PROFILE_DIR = DEFAULT_COOKIES_PATH.parent / "chrome-profile"

# macOS URL-scheme handler applet and the file it writes the redirect to.
HANDLER_APP = Path.home() / "Applications" / "BSPulseLogin.app"
REDIRECT_FILE = Path(tempfile.gettempdir()) / "brightspace-sync-redirect.txt"


class AuthError(RuntimeError):
    """Raised when authentication cannot be completed."""


class ReauthRequired(AuthError):
    """Raised when a headless run needs an interactive login again."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def search_institutions(query: str) -> list[tuple[str, str]]:
    """Return ``(name, domain)`` tuples for institutions matching ``query``."""
    try:
        r = requests.get(INSTITUTION_SEARCH, params={"contains": query}, timeout=8)
        r.raise_for_status()
    except requests.RequestException:
        return []
    out: list[tuple[str, str]] = []
    for entity in r.json().get("entities", []):
        name = entity.get("properties", {}).get("name", "")
        for link in entity.get("links", []):
            if "lms" in link.get("rel", []):
                out.append((name, urllib.parse.urlparse(link["href"]).netloc))
                break
    return out


def discover_tenant(domain: str) -> str:
    """Resolve a Brightspace domain (e.g. brightspace.rug.nl) to a tenant id."""
    r = requests.get(LANDLORD, params={"domain": domain}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise AuthError(f"No Brightspace tenant found for domain {domain!r}")
    return data[0]["tenantId"]


def _cache_path(tenant_id: str) -> Path:
    return CACHE_DIR / f"{tenant_id}.json"


def _load_cached_token(tenant_id: str) -> dict | None:
    path = _cache_path(tenant_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _save_cached_token(tenant_id: str, token: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(tenant_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(token))
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _token_from_response(payload: dict) -> dict:
    payload["expires_at"] = int(time.time()) + int(payload.get("expires_in", 0))
    return payload


def _refresh(token: dict) -> dict | None:
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        return None
    try:
        r = requests.post(
            TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
            },
            timeout=20,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    new = _token_from_response(r.json())
    new.setdefault("refresh_token", refresh_token)
    return new


def _exchange_code(code: str, verifier: str) -> dict:
    r = requests.post(
        TOKEN,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=20,
    )
    if r.status_code != 200:
        raise AuthError(f"Token exchange failed: HTTP {r.status_code} {r.text[:300]}")
    return _token_from_response(r.json())


def _authorize_url(tenant_id: str, state: str, challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "tenant_id": tenant_id,
    }
    return f"{AUTHORIZE}?{urllib.parse.urlencode(params)}"


# -- macOS URL-scheme handler ------------------------------------------------


def _build_handler_app() -> None:
    """Create an applet that captures brightspacepulse:// URLs to a file."""
    script = (
        "on open location this_URL\n"
        "  do shell script \"/bin/echo \" & quoted form of this_URL & "
        "\" >> \" & quoted form of "
        f'"{REDIRECT_FILE}"\n'
        "  quit\n"
        "end open location\n"
    )
    HANDLER_APP.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".applescript", delete=False
    ) as handle:
        handle.write(script)
        source = handle.name
    try:
        subprocess.run(
            ["osacompile", "-o", str(HANDLER_APP), source],
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(source)

    plist = HANDLER_APP / "Contents" / "Info.plist"
    pb = "/usr/libexec/PlistBuddy"
    commands = [
        "Set :CFBundleIdentifier com.brightspace-sync.login",
        "Add :CFBundleURLTypes array",
        "Add :CFBundleURLTypes:0 dict",
        "Add :CFBundleURLTypes:0:CFBundleURLName string com.brightspace-sync.pulse",
        "Add :CFBundleURLTypes:0:CFBundleURLSchemes array",
        "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string brightspacepulse",
    ]
    for command in commands:
        subprocess.run(
            [pb, "-c", command, str(plist)], check=False, capture_output=True
        )


def _ensure_url_handler() -> bool:
    """Make sure macOS routes brightspacepulse:// to our applet."""
    if sys.platform != "darwin":
        return False
    try:
        if not HANDLER_APP.exists():
            _build_handler_app()
        lsregister = (
            "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
            "LaunchServices.framework/Support/lsregister"
        )
        subprocess.run(
            [lsregister, "-f", str(HANDLER_APP)], check=False, capture_output=True
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[auth] could not install URL handler: {exc}", flush=True)
        return False


def _read_redirect_file() -> str | None:
    if not REDIRECT_FILE.exists():
        return None
    try:
        lines = REDIRECT_FILE.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if line.startswith(REDIRECT_URI):
            return line
    return None


def _read_clipboard() -> str:
    if sys.platform != "darwin":
        return ""
    try:
        return subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _browser_capture_redirect(url: str, timeout: int = 300) -> str:
    """Open the login URL in the system browser and wait for the redirect.

    The redirect is recovered from the URL-scheme handler applet, from the
    clipboard, or from a terminal paste, whichever happens first.
    """
    REDIRECT_FILE.unlink(missing_ok=True)
    handler_ok = _ensure_url_handler()

    print("\nOpening your browser for the Brightspace login.", flush=True)
    if handler_ok:
        print(
            "If macOS asks to open 'BSPulseLogin', click Open.", flush=True
        )
    try:
        webbrowser.open(url)
    except Exception:  # pragma: no cover - best effort
        pass
    print(
        f"If it does not complete automatically, copy the '{REDIRECT_URI}://auth?...' "
        "URL to the clipboard.\n",
        flush=True,
    )
    print(url, flush=True)

    deadline = time.time() + timeout
    while time.time() < deadline:
        found = _read_redirect_file()
        if found:
            print("[auth] captured redirect via URL handler", flush=True)
            return found

        clip = _read_clipboard()
        if clip.startswith(REDIRECT_URI):
            print("[auth] captured redirect via clipboard", flush=True)
            return clip

        if sys.stdin.isatty():
            readable, _, _ = select.select([sys.stdin], [], [], 0)
            if readable:
                pasted = sys.stdin.readline().strip()
                if pasted:
                    print("[auth] captured redirect via paste", flush=True)
                    return pasted

        time.sleep(1)

    raise AuthError("Timed out waiting for the Brightspace redirect URL.")


def _base_domain(domain: str) -> str:
    parts = domain.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def _save_browser_cookies(cookies: list[dict], domain: str, path: Path) -> None:
    base = _base_domain(domain)
    relevant = [
        c
        for c in cookies
        if base in (c.get("domain") or "") or domain in (c.get("domain") or "")
    ]
    if not relevant:
        relevant = cookies
    header = "; ".join(f"{c['name']}={c['value']}" for c in relevant)
    if not header:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _chrome_login(
    tenant_id: str,
    domain: str,
    cookies_path: Path | None,
    *,
    log=print,
    timeout: int = 300,
) -> dict:
    """Guided login through the user's installed Chrome (DevTools Protocol).

    Captures the OAuth code from the ``brightspacepulse://auth`` redirect and
    the browser session cookies needed for browser-only course files.
    """
    from . import browser

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    url = _authorize_url(tenant_id, state, challenge)

    log(
        "A Chrome window is opening. Sign in to Brightspace as usual "
        "(including any two-factor step). It closes by itself when done."
    )

    process = None
    cdp = None
    try:
        process, port = browser.launch_chrome(BROWSER_PROFILE_DIR)
        cdp = browser.CDP(browser.page_socket_url(port))
        cdp.call("Network.enable")
        cdp.call("Page.enable")
        cdp.call("Page.navigate", url=url)

        captured = cdp.wait_for(_redirect_from_event, timeout=timeout)
        if not captured:
            raise AuthError("Login was not completed in time.")

        cookies: list[dict] = []
        if cookies_path:
            try:
                result = cdp.call("Network.getAllCookies", timeout=30)
                cookies = result.get("cookies") or []
            except browser.CDPError:
                cookies = []
    finally:
        if cdp is not None:
            cdp.close()
        if process is not None:
            browser.stop_chrome(process)

    if cookies and cookies_path:
        _save_browser_cookies(cookies, domain, cookies_path)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(captured).query)
    if query.get("state", [None])[0] != state:
        raise AuthError("OAuth state mismatch; aborting login.")
    code = query.get("code", [None])[0]
    if not code:
        raise AuthError("No authorization code captured from the login.")
    return _exchange_code(code, verifier)


def _redirect_from_event(message: dict) -> str | None:
    """Return the redirect URL when a CDP event carries it, else None."""
    method = message.get("method")
    params = message.get("params") or {}
    if method == "Network.requestWillBeSent":
        candidate = (params.get("request") or {}).get("url", "")
    elif method in ("Page.frameRequestedNavigation", "Page.frameNavigated"):
        candidate = params.get("url", "")
    else:
        return None
    return candidate if candidate.startswith(REDIRECT_URI) else None


def interactive_login(
    tenant_id: str,
    domain: str,
    cookies_path: Path | None = None,
    *,
    allow_fallback: bool = True,
    log=print,
) -> dict:
    """Run the login flow and return a fresh token bundle.

    Prefers the guided Chrome login (which also captures cookies for the
    reader); optionally falls back to the helper-app flow.
    """
    try:
        return _chrome_login(tenant_id, domain, cookies_path, log=log)
    except Exception as exc:  # noqa: BLE001 - optionally fall back
        log(f"[auth] Chrome login did not complete: {exc}")
        if not allow_fallback:
            raise

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    url = _authorize_url(tenant_id, state, challenge)

    redirect = _browser_capture_redirect(url)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)
    if query.get("state", [None])[0] != state:
        raise AuthError("OAuth state mismatch; aborting login.")
    code = query.get("code", [None])[0]
    if not code:
        raise AuthError(f"No authorization code in redirect: {query}")

    return _exchange_code(code, verifier)


def get_access_token(
    tenant_id: str,
    *,
    allow_interactive: bool = True,
    domain: str | None = None,
    cookies_path: Path | None = None,
    allow_fallback: bool = True,
    log=print,
) -> str:
    """Return a valid access token, refreshing or logging in as needed."""
    cached = _load_cached_token(tenant_id)
    if cached:
        if time.time() < cached.get("expires_at", 0) - 60:
            return cached["access_token"]
        refreshed = _refresh(cached)
        if refreshed:
            _save_cached_token(tenant_id, refreshed)
            return refreshed["access_token"]

    if not allow_interactive:
        raise ReauthRequired(
            "Cached Brightspace credentials are missing or expired. "
            "Run 'brightspace-sync login' interactively."
        )

    token = interactive_login(
        tenant_id,
        domain or "brightspace.rug.nl",
        cookies_path,
        allow_fallback=allow_fallback,
        log=log,
    )
    _save_cached_token(tenant_id, token)
    return token["access_token"]


def ensure_token(
    domain: str,
    *,
    allow_interactive: bool = True,
    force_login: bool = False,
    cookies_path: Path | None = None,
    allow_fallback: bool = True,
    log=print,
) -> str:
    """Discover the tenant for ``domain`` and return an access token."""
    tenant_id = discover_tenant(domain)
    if force_login:
        if not allow_interactive:
            raise ReauthRequired("Cannot force a login in a non-interactive run.")
        token = interactive_login(
            tenant_id,
            domain,
            cookies_path,
            allow_fallback=allow_fallback,
            log=log,
        )
        _save_cached_token(tenant_id, token)
        return token["access_token"]
    return get_access_token(
        tenant_id,
        allow_interactive=allow_interactive,
        domain=domain,
        cookies_path=cookies_path,
        allow_fallback=allow_fallback,
        log=log,
    )


def token_status(domain: str) -> dict:
    """Describe the cached token state without performing network calls."""
    try:
        tenant_id = discover_tenant(domain)
    except (requests.RequestException, AuthError) as exc:
        return {"tenant_id": None, "error": str(exc)}
    cached = _load_cached_token(tenant_id)
    if not cached:
        return {"tenant_id": tenant_id, "has_token": False}
    return {
        "tenant_id": tenant_id,
        "has_token": True,
        "expires_at": cached.get("expires_at", 0),
        "expired": time.time() >= cached.get("expires_at", 0),
        "has_refresh_token": bool(cached.get("refresh_token")),
    }
