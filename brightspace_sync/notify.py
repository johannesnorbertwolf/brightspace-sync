"""Notification channels: macOS Notification Center, email, and webhooks."""

from __future__ import annotations

import json
import os
import shutil
import smtplib
import ssl
import subprocess
from email.message import EmailMessage

import requests


class Notifier:
    """Send notifications to every enabled channel.

    Config shape::

        "notify": {
          "macos": true,
          "email": {"enabled": false, "to": "", "smtp_host": "",
                    "smtp_port": 587, "username": "",
                    "password_env": "BRIGHTSPACE_SMTP_PASSWORD",
                    "from": "", "use_tls": true},
          "webhook": {"enabled": false, "url_env": "BRIGHTSPACE_WEBHOOK_URL"}
        }
    """

    def __init__(self, config: dict | None = None, *, verbose: bool = False):
        self.config = config or {}
        self.verbose = verbose

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"  [notify] {message}")

    def send(self, title: str, message: str, *, subtitle: str | None = None) -> None:
        if self.config.get("macos", True):
            self._macos(title, message, subtitle)
        email = self.config.get("email") or {}
        if email.get("enabled"):
            self._email(title, message, email)
        webhook = self.config.get("webhook") or {}
        if webhook.get("enabled"):
            self._webhook(title, message, webhook)
        telegram = self.config.get("telegram") or {}
        if telegram.get("enabled"):
            self._telegram(title, message, telegram)
        imessage = self.config.get("imessage") or {}
        if imessage.get("enabled"):
            self._imessage(title, message, imessage)

    # -- macOS -------------------------------------------------------------

    def _macos(self, title: str, message: str, subtitle: str | None) -> None:
        try:
            if shutil.which("terminal-notifier"):
                args = ["terminal-notifier", "-title", title, "-message", message]
                if subtitle:
                    args += ["-subtitle", subtitle]
                subprocess.run(args, check=False, capture_output=True)
                return

            script = (
                "on run argv\n"
                "  if (count of argv) is 3 then\n"
                "    display notification (item 2 of argv) with title "
                "(item 1 of argv) subtitle (item 3 of argv)\n"
                "  else\n"
                "    display notification (item 2 of argv) with title "
                "(item 1 of argv)\n"
                "  end if\n"
                "end run"
            )
            args = ["osascript", "-e", script, title, message]
            if subtitle:
                args.append(subtitle)
            subprocess.run(args, check=False, capture_output=True)
            self._log(f"macOS notification: {title} - {message}")
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            self._log(f"macOS notification failed: {exc}")

    # -- email -------------------------------------------------------------

    def _email(self, title: str, message: str, cfg: dict) -> None:
        password_env = cfg.get("password_env", "BRIGHTSPACE_SMTP_PASSWORD")
        password = os.environ.get(password_env, "")
        host = cfg.get("smtp_host", "")
        recipient = cfg.get("to", "")
        sender = cfg.get("from") or cfg.get("username", "")
        if not (host and recipient and sender):
            self._log("email enabled but smtp_host/to/from not fully configured")
            return

        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = sender
        msg["To"] = recipient
        msg.set_content(message)

        port = int(cfg.get("smtp_port", 587))
        try:
            if cfg.get("use_tls", True):
                context = ssl.create_default_context()
                with smtplib.SMTP(host, port, timeout=30) as smtp:
                    smtp.starttls(context=context)
                    if cfg.get("username"):
                        smtp.login(cfg["username"], password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                    if cfg.get("username"):
                        smtp.login(cfg["username"], password)
                    smtp.send_message(msg)
            self._log(f"email sent to {recipient}")
        except (OSError, smtplib.SMTPException) as exc:
            self._log(f"email failed: {exc}")

    # -- webhook -----------------------------------------------------------

    def _webhook(self, title: str, message: str, cfg: dict) -> None:
        url_env = cfg.get("url_env", "BRIGHTSPACE_WEBHOOK_URL")
        url = os.environ.get(url_env, "")
        if not url:
            self._log(f"webhook enabled but ${url_env} is not set")
            return
        try:
            requests.post(
                url,
                data=json.dumps({"title": title, "message": message}),
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            self._log("webhook posted")
        except requests.RequestException as exc:
            self._log(f"webhook failed: {exc}")

    # -- telegram ----------------------------------------------------------
    def _telegram(self, title: str, message: str, cfg: dict) -> None:
        token = os.environ.get(
            cfg.get("bot_token_env", "BRIGHTSPACE_TELEGRAM_BOT_TOKEN"), ""
        )
        chat_id = os.environ.get(
            cfg.get("chat_id_env", "BRIGHTSPACE_TELEGRAM_CHAT_ID"), ""
        )
        if not (token and chat_id):
            self._log("telegram enabled but bot token / chat id is not set")
            return
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": f"{title}\n{message}",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if response.status_code == 200:
                self._log("telegram message sent")
            else:
                self._log(
                    f"telegram failed: HTTP {response.status_code} "
                    f"{response.text[:120]}"
                )
        except requests.RequestException as exc:
            self._log(f"telegram failed: {exc}")

    # -- iMessage (macOS) --------------------------------------------------

    def _imessage(self, title: str, message: str, cfg: dict) -> None:
        recipient = (cfg.get("recipient") or "").strip()
        if not recipient:
            self._log("imessage enabled but recipient is not set")
            return
        script = (
            "on run {msg, toAddr}\n"
            '  tell application "Messages"\n'
            "    set svc to 1st account whose service type = iMessage\n"
            "    set theBuddy to buddy (toAddr as text) of svc\n"
            "    send msg to theBuddy\n"
            "  end tell\n"
            "end run"
        )
        text = f"{title}\n{message}"
        try:
            result = subprocess.run(
                ["osascript", "-e", script, text, recipient],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                self._log(f"iMessage sent to {recipient}")
            else:
                self._log(f"iMessage failed: {result.stderr.strip()[:160]}")
        except (OSError, subprocess.SubprocessError) as exc:
            self._log(f"iMessage failed: {exc}")
