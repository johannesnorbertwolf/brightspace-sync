# brightspace-sync

Keep every Brightspace (D2L) course file downloaded locally, and get a
notification when something new appears. Built for RUG (`brightspace.rug.nl`)
but works against any Brightspace tenant.

It runs on a schedule (hourly launchd agent by default), walks every enrolled
course's content tree, downloads new files, and diffs announcements,
assignment due dates and grades against the last run.

## Download

The friendly, no-terminal version is a native menu bar app. Grab the latest
build here — no GitHub account needed:

**https://github.com/johannesnorbertwolf/brightspace-sync/releases/latest/download/Brightspace-Sync.dmg**

Open the disk image, drag **Brightspace Sync** into Applications, eject, and the
first time you open it, right-click → Open. See [INSTALL.md](INSTALL.md) for the
picture-by-picture guide. Google Chrome is recommended but not required: it lets
the app download a few files (such as a course reader) that Brightspace only
serves to a signed-in browser. Without it everything else still works, but those
files are saved as links instead.

## How authentication works

Students can't create D2L API keys, so this tool authenticates the same way the
official **Brightspace Pulse** mobile app does: OAuth2 + PKCE with D2L's public
Pulse client.

`brightspace-sync login` opens a normal Chrome window and drives it over the
Chrome DevTools Protocol (no bundled browser). Log in there as usual, including
any two-factor step; the tool captures both the API token and the browser
cookies and closes the window by itself. It never sees your password.

The browser keeps a dedicated profile under
`~/.config/brightspace-sync/chrome-profile`, so it can remember the login and
password for next time. Tokens are cached at:

```
~/.cache/brightspace-sync/<tenantId>.json   (chmod 600)
```

The captured cookies (needed for the course reader and other browser-only
files) are saved to `~/.config/brightspace-sync/cookies.txt`.

If Chrome (or a compatible Chromium browser such as Edge) is not installed,
`login` falls back to your default browser and still captures the API token,
just not the browser cookies. In that case everything syncs except reader-type
files, which are saved as clickable links instead.

Scheduled runs reuse the refresh token. If it ever expires you get a
notification asking you to run `brightspace-sync login` again.

## Install

```bash
cd ~/code/brightspace-sync
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Menu bar app

For non-technical users there is a native menu bar app that runs the same
engine without a terminal. Start it with:

```bash
python -m brightspace_sync app      # or: ./brightspace-sync app
```

On first launch it checks for Chrome, asks where to save files, signs you in,
lets you pick courses, and sets up notifications. After that it lives in the
menu bar: click the icon for status, **Sync Now**, the download folder,
Preferences, and Sign In Again. It syncs on a schedule and at login, and
alerts you in plain language when the login lapses.

### Build a double-clickable app

```bash
pip install -r requirements-build.txt   # runtime deps + PyInstaller
./scripts/build_app.sh                  # -> "dist/Brightspace Sync.app" + .dmg
```

The app is intentionally small (~25 MB): it uses macOS's own AppKit for the
interface (via PyObjC) and drives your installed Chrome, so no Qt and no
bundled browser are shipped. The build is unsigned, so the first launch needs
a right-click -> Open; see `INSTALL.md` for the friend-facing guide.

## Quick start

```bash
# 1. One-time interactive SSO login
./brightspace-sync login

# 2. See what it found, without writing anything
./brightspace-sync courses
./brightspace-sync sync --dry-run

# 3. Real first sync (downloads everything; notifications suppressed so you
#    don't get spammed with your whole back catalogue)
./brightspace-sync sync

# 4. Run it every hour in the background
./brightspace-sync install-agent
```

Files land in `~/Brightspace/<course>/<module>/<file>`. Re-runs skip files
whose size and modified date are unchanged, and re-download files that changed
on the server.

## Commands

| Command | Purpose |
|---|---|
| `login` | Interactive SSO login, then verify the token with `whoami`. |
| `sync` | Download new material, diff everything, notify. |
| `sync --dry-run` | Show what would happen; writes nothing, notifies nobody. |
| `sync --non-interactive` | Never prompt (used by the scheduler). |
| `courses` | List enrolled courses. |
| `status` | Show config paths, token validity, and what is tracked. |
| `test-notify` | Send a test notification on every enabled channel. |
| `install-agent` | Install/refresh the hourly launchd agent. |
| `uninstall-agent` | Remove it. |
| `app` | Launch the native menu bar app (recommended). |

Global flags: `--domain`, `-v/--verbose`.

## Configuration

Config lives at `~/.config/brightspace-sync/config.json` (created on first
`login`). Start from `config.example.json`. Key options:

```jsonc
{
  "domain": "brightspace.rug.nl",
  "out_dir": "~/Brightspace",
  "courses": {
    "include": [],            // only courses whose name contains one of these
    "exclude": ["Old Course"],// skip courses whose name contains any of these
    "include_inactive": false
  },
  "track": {
    "files": true,
    "announcements": true,
    "assignments": true,
    "grades": true
  },
  "notify_initial": false,     // true = notify about existing material on first run
  "notify": { "macos": true, "email": { "enabled": false }, "webhook": { "enabled": false } }
}
```

### Reader and other browser-only files

Some material — notably the course **reader** — is not attached as normal
content. It is only linked from a module's description, pointing into
Brightspace's private file area, which the OAuth token cannot read. Those
files need a logged-in browser session.

The cookies captured by `brightspace-sync login` cover this automatically, so
normally there is nothing extra to do. The tool resolves each "course file"
link to its real address and downloads it, keeping the module's folder layout.

If the cookies are missing or expired, the tool still saves clickable
shortcuts for those files and notes it in the log; once a real file downloads,
its shortcut is removed. When the log says the reader needs cookies again,
just run `brightspace-sync login` once more.

If you don't have Chrome at all, the cookies can never be captured, so these
files always stay as links. The app says so up front and offers to continue
without Chrome. Install Chrome and sign in again if you later want the files
downloaded for real.

Advanced: if you prefer to supply cookies yourself, put either a Netscape
`cookies.txt` export or a raw `name=value; name2=value2` cookie string at
`~/.config/brightspace-sync/cookies.txt` (or point `cookies_file` elsewhere in
the config).

### Notifications

- **macOS** (`notify.macos`): Notification Center popup via `osascript`, or
  `terminal-notifier` if installed.
- **Email** (`notify.email.enabled`): set `to`/`from`/`smtp_host`/`username`
  in config and put the password in `BRIGHTSPACE_SMTP_PASSWORD` (in `.env`).
- **Webhook** (`notify.webhook.enabled`): set `BRIGHTSPACE_WEBHOOK_URL` (in
  `.env`). Posts `{"title": ..., "message": ...}`; works with Slack, Discord
  and Mattermost incoming webhooks.
- **Telegram** (`notify.telegram.enabled`): create a bot by messaging
  `@BotFather` on Telegram (`/newbot`), copy the token, then message your new
  bot once and get your numeric id from `@userinfobot`. Put both in `.env` as
  `BRIGHTSPACE_TELEGRAM_BOT_TOKEN` and `BRIGHTSPACE_TELEGRAM_CHAT_ID`.
- **iMessage** (`notify.imessage.enabled`, macOS): set `recipient` to your own
  phone number or Apple ID email. Messages are sent to you via the Messages
  app, so they arrive on all your Apple devices. The first send asks for
  permission to control Messages; click OK.

Login-break alerts (expired API login, lapsed reader cookies, or a failed
sync) are sent to every enabled channel, at most once every 12 hours each.

`.env` is read from the project folder or `~/.config/brightspace-sync/`; see
`.env.example`.

## Scheduling

`install-agent` writes
`~/Library/LaunchAgents/com.<user>.brightspace-sync.plist` and loads it with
`launchctl`. It runs `sync --non-interactive` every hour and appends output to
`~/Library/Logs/brightspace-sync/`. Change the cadence with
`./brightspace-sync install-agent --interval 21600` (6 hours).

To watch it: `tail -f ~/Library/Logs/brightspace-sync/sync.log`.

The menu bar app is the friendlier alternative: it schedules syncs internally
(interval set in Preferences), starts at login, and removes this launchd agent
the first time setup finishes. Use one or the other, not both.

## What the API can and can't do

This uses the Pulse OAuth scopes
`core:*:* content:topics:read content:file:read`:

- Works: content tree + file downloads (Valence + Pulse GraphQL), grades
  (`myGradeValues`), assignment folders/due dates (`dropbox/folders`),
  announcements (`news`), calendar.
- Does **not** work with these scopes (HTTP 403): submissions, quizzes,
  discussions, classlist, and instructor-only endpoints. Those would require a
  separately registered OAuth client.

## Notes

- Each course folder also gets readable `Grades.md`, `Assignments.md` and
  `Announcements.md` summaries, refreshed only when they change.
- Non-file topics (links, HTML pages, D2L videos) are saved as `.url`
  shortcuts rather than scraped.
- `sync` exits non-zero if any file failed, so the launchd log shows problems.
- State is stored at `~/.local/state/brightspace-sync/state.json`; delete it to
  force a full re-baseline (with `notify_initial: false`, no spam).
- The login helper app can be removed from `~/Applications/BSPulseLogin.app` if
  you ever stop using the tool.
