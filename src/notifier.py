"""
Telegram Notifier — Optional push notifications for key agent events

Sends messages via the Telegram Bot API when:
    • The worker starts up
    • A task is completed
    • A usage limit is hit (with reset time)
    • A task fails permanently
    • A general error occurs

All methods are no-ops when TELEGRAM_ENABLED=false, so the worker
code is clean and doesn't need to check the flag itself.

Setup:
    1. Create a bot via @BotFather on Telegram
    2. Get the token and your chat ID (message @userinfobot)
    3. Set TELEGRAM_ENABLED=true in .env
    4. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

import logging
import os
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─── Configuration ─────────────────────────────────────────────────────────────
TELEGRAM_ENABLED: bool = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
API_URL: str = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

NOTIFY_ON_COMPLETE: bool = os.getenv("TELEGRAM_NOTIFY_ON_COMPLETE", "true").lower() == "true"
NOTIFY_ON_LIMIT: bool = os.getenv("TELEGRAM_NOTIFY_ON_LIMIT", "true").lower() == "true"
NOTIFY_ON_ERROR: bool = os.getenv("TELEGRAM_NOTIFY_ON_ERROR", "true").lower() == "true"

_REQUEST_TIMEOUT: int = 10  # seconds


class Notifier:
    """
    Sends Telegram messages for significant worker events.

    All public methods silently no-op if Telegram is disabled or
    if the API call fails (notifications are best-effort, never fatal).
    """

    def _send(self, text: str) -> bool:
        """
        Send a Telegram message.

        Args:
            text: Message text (supports Markdown).

        Returns:
            True if the message was sent, False on failure.
        """
        if not TELEGRAM_ENABLED:
            return False

        if not BOT_TOKEN or not CHAT_ID:
            logger.warning("Telegram enabled but BOT_TOKEN or CHAT_ID not set — skipping")
            return False

        try:
            resp = requests.post(
                API_URL,
                json={
                    "chat_id": CHAT_ID,
                    "text": text,
                    "parse_mode": "Markdown",
                },
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            logger.debug("Telegram notification sent ✓")
            return True

        except requests.exceptions.Timeout:
            logger.warning("Telegram API timed out")
        except requests.exceptions.HTTPError as exc:
            logger.warning("Telegram API HTTP error: %s", exc)
        except requests.exceptions.RequestException as exc:
            logger.warning("Telegram API request failed: %s", exc)

        return False

    # ── Event Methods ──────────────────────────────────────────────────────────

    def notify_startup(self) -> None:
        """Notify that the agent worker has started."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._send(
            f"🤖 *Claude Nightcrawler started*\n"
            f"Time: `{ts}`\n"
            f"Worker is online and ready to process tasks."
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
        preview = response_preview[:200].replace("*", "\\*")
        if len(response_preview) > 200:
            preview += "…"
        self._send(
            f"✅ *Task {task_id} completed*\n"
            f"File: `{result_path}`\n\n"
            f"_{preview}_"
        )

    def notify_limit_hit(self, task_id: int, reset_time: datetime) -> None:
        """Notify that a usage limit was detected."""
        if not NOTIFY_ON_LIMIT:
            return
        reset_str = reset_time.strftime("%H:%M:%S")
        minutes = int((reset_time - datetime.now()).total_seconds() / 60)
        self._send(
            f"⏸️ *Usage limit detected*\n"
            f"Task {task_id} is waiting.\n"
            f"Reset at: `{reset_str}` (~{minutes} min)"
        )

    def notify_error(self, task_id: int, error_message: str) -> None:
        """Notify that an error occurred."""
        if not NOTIFY_ON_ERROR:
            return
        msg = error_message[:300].replace("*", "\\*")
        label = f"Task {task_id}" if task_id else "Worker"
        self._send(
            f"❌ *{label} error*\n"
            f"`{msg}`"
        )

    def notify_shutdown(self) -> None:
        """Notify that the agent worker is shutting down."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._send(
            f"🛑 *Claude Nightcrawler stopped*\n"
            f"Time: `{ts}`"
        )
