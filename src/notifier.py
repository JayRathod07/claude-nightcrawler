"""
Telegram Notifier — Push notifications for key agent events

Sends messages via the Telegram Bot API when:
    • The worker starts up / shuts down
    • A task is completed (with response preview)
    • A usage limit is hit (with reset time countdown)
    • A task fails permanently
    • A general error occurs
    • A daily summary is ready (from morning_report.py)
    • A health alert fires (from health_check.py --alert)

All methods are no-ops when TELEGRAM_ENABLED=false, so the worker
code is clean and doesn't need to check the flag itself.

Setup:
    1. Create a bot via @BotFather on Telegram
    2. Start a chat with the bot, then visit:
         https://api.telegram.org/bot<TOKEN>/getUpdates
       to find your chat_id
    3. Add to .env:
         TELEGRAM_ENABLED=true
         TELEGRAM_BOT_TOKEN=<your-token>
         TELEGRAM_CHAT_ID=<your-chat-id>
"""

import logging
import os
import time
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─── Configuration ─────────────────────────────────────────────────────────────
TELEGRAM_ENABLED: bool = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

NOTIFY_ON_COMPLETE: bool = os.getenv("TELEGRAM_NOTIFY_ON_COMPLETE", "true").lower() == "true"
NOTIFY_ON_LIMIT: bool = os.getenv("TELEGRAM_NOTIFY_ON_LIMIT", "true").lower() == "true"
NOTIFY_ON_ERROR: bool = os.getenv("TELEGRAM_NOTIFY_ON_ERROR", "true").lower() == "true"
NOTIFY_ON_HEALTH: bool = os.getenv("TELEGRAM_NOTIFY_ON_HEALTH", "true").lower() == "true"

_REQUEST_TIMEOUT: int = 10   # seconds per attempt
_MAX_RETRIES: int = 3        # retry transient failures up to this many times
_RETRY_DELAY: float = 2.0    # seconds between retries


def _escape_md(text: str) -> str:
    """
    Escape special characters for Telegram MarkdownV2.
    Telegram's MarkdownV2 requires escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    specials = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in specials else c for c in str(text))


class Notifier:
    """
    Sends Telegram messages for significant worker events.

    All public methods silently no-op if Telegram is disabled or
    if the API call fails (notifications are best-effort, never fatal).
    Uses MarkdownV2 format for richer message formatting.
    Retries up to _MAX_RETRIES times on network/5xx errors.
    """

    def _send(self, text: str, parse_mode: str = "MarkdownV2") -> bool:
        """
        Send a Telegram message with automatic retry on transient failures.

        Args:
            text: Message text. Use MarkdownV2 syntax; escape special chars.
            parse_mode: 'MarkdownV2' (default) or 'Markdown' or 'HTML'.

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        if not TELEGRAM_ENABLED:
            return False

        if not BOT_TOKEN or not CHAT_ID:
            logger.warning("Telegram enabled but BOT_TOKEN or CHAT_ID not set — skipping")
            return False

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
        }

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(url, json=payload, timeout=_REQUEST_TIMEOUT)

                # 429 = Too Many Requests — respect retry-after header
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 5))
                    logger.warning("Telegram rate limited — waiting %ds", wait)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                logger.debug("Telegram notification sent ✓ (attempt %d)", attempt)
                return True

            except requests.exceptions.Timeout:
                logger.warning("Telegram API timed out (attempt %d/%d)", attempt, _MAX_RETRIES)
            except requests.exceptions.HTTPError as exc:
                logger.warning("Telegram API HTTP error: %s (attempt %d/%d)", exc, attempt, _MAX_RETRIES)
                # 4xx errors won't be fixed by retrying
                if exc.response is not None and exc.response.status_code < 500:
                    break
            except requests.exceptions.RequestException as exc:
                logger.warning("Telegram API request failed: %s (attempt %d/%d)", exc, attempt, _MAX_RETRIES)

            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY * attempt)

        return False

    # ── Event Methods ─────────────────────────────────────────────────────────

    def notify_startup(self) -> None:
        """Notify that the agent worker has started."""
        ts = _escape_md(datetime.now().strftime("%H:%M:%S"))
        self._send(
            f"🤖 *Claude Nightcrawler started*\n"
            f"Time: `{ts}`\n"
            f"Worker is online and ready to process tasks\\."
        )

    def notify_completed(
        self,
        task_id: int,
        result_path: str,
        response_preview: str = "",
    ) -> None:
        """Notify that a task completed successfully."""
        if not NOTIFY_ON_COMPLETE:
            return
        preview = _escape_md(response_preview[:200])
        if len(response_preview) > 200:
            preview += "…"
        task_id_s = _escape_md(str(task_id))
        path_s = _escape_md(result_path)
        self._send(
            f"✅ *Task {task_id_s} completed*\n"
            f"File: `{path_s}`\n\n"
            f"_{preview}_"
        )

    def notify_limit_hit(self, task_id: int, reset_time: datetime) -> None:
        """Notify that a usage limit was detected."""
        if not NOTIFY_ON_LIMIT:
            return
        reset_str = _escape_md(reset_time.strftime("%H:%M:%S"))
        minutes = max(0, int((reset_time - datetime.now()).total_seconds() / 60))
        tid = _escape_md(str(task_id))
        self._send(
            f"⏸ *Usage limit detected*\n"
            f"Task {tid} is paused\\.\n"
            f"Reset at: `{reset_str}` \\(\\~{minutes} min\\)"
        )

    def notify_error(self, task_id: Optional[int], error_message: str) -> None:
        """Notify that an error occurred."""
        if not NOTIFY_ON_ERROR:
            return
        msg = _escape_md(error_message[:300])
        label = _escape_md(f"Task {task_id}") if task_id else "Worker"
        self._send(
            f"❌ *{label} error*\n"
            f"`{msg}`"
        )

    def notify_shutdown(self) -> None:
        """Notify that the agent worker is shutting down."""
        ts = _escape_md(datetime.now().strftime("%H:%M:%S"))
        self._send(
            f"🛑 *Claude Nightcrawler stopped*\n"
            f"Time: `{ts}`"
        )

    def notify_daily_summary(self, report_text: str) -> bool:
        """
        Send the daily morning report via Telegram.

        Args:
            report_text: Plain-text report built by morning_report.py.

        Returns:
            True if the message was sent successfully.
        """
        # Morning report uses its own Markdown (not MarkdownV2) for simplicity
        return self._send(report_text, parse_mode="Markdown")

    def notify_health_alert(self, failed_checks: list[str], details: list[str]) -> bool:
        """
        Send a health alert when one or more health checks fail.

        Args:
            failed_checks: List of check names that failed.
            details: Corresponding detail messages for each failed check.

        Returns:
            True if the message was sent successfully.
        """
        if not NOTIFY_ON_HEALTH:
            return False
        ts = _escape_md(datetime.now().strftime("%Y\\-%m\\-%d %H:%M:%S"))
        lines = [f"🚨 *Health Alert \\— {_escape_md(str(len(failed_checks)))} check\\(s\\) failed*"]
        lines.append(f"Time: `{ts}`\n")
        for name, detail in zip(failed_checks, details):
            lines.append(f"❌ `{_escape_md(name)}`: {_escape_md(detail)}")
        return self._send("\n".join(lines))
