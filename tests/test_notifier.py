"""
Tests for src/notifier.py — Telegram notification module

All tests run with Telegram disabled (TELEGRAM_ENABLED=false) by default,
so no real API calls are made.  Network-path tests mock requests.post.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force Telegram disabled for most tests
os.environ.setdefault("TELEGRAM_ENABLED", "false")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_CHAT_ID", "")


# ─── Re-import notifier with fresh env each test class ───────────────────────

def _make_notifier(enabled: bool = True, token: str = "TEST_TOKEN", chat: str = "12345"):
    """Import a fresh Notifier with given env settings."""
    import importlib
    # Patch env before import
    with patch.dict(os.environ, {
        "TELEGRAM_ENABLED": "true" if enabled else "false",
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_CHAT_ID": chat,
        "TELEGRAM_NOTIFY_ON_COMPLETE": "true",
        "TELEGRAM_NOTIFY_ON_LIMIT": "true",
        "TELEGRAM_NOTIFY_ON_ERROR": "true",
        "TELEGRAM_NOTIFY_ON_HEALTH": "true",
    }):
        import src.notifier as mod
        importlib.reload(mod)
        return mod.Notifier(), mod


# ─────────────────────────────────────────────────────────────────────────────
class TestNotifierDisabled:
    """When TELEGRAM_ENABLED=false, all methods are no-ops."""

    def setup_method(self):
        from src.notifier import Notifier
        import importlib
        import src.notifier as mod
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "false"}):
            importlib.reload(mod)
        self.notifier = Notifier()

    def test_send_returns_false_when_disabled(self):
        assert self.notifier._send("hello") is False

    def test_notify_startup_does_not_raise(self):
        self.notifier.notify_startup()  # should silently return

    def test_notify_completed_does_not_raise(self):
        self.notifier.notify_completed(1, "results/task_1.md", "Great answer!")

    def test_notify_limit_hit_does_not_raise(self):
        self.notifier.notify_limit_hit(2, datetime.now() + timedelta(hours=2))

    def test_notify_error_does_not_raise(self):
        self.notifier.notify_error(3, "Something went wrong")

    def test_notify_shutdown_does_not_raise(self):
        self.notifier.notify_shutdown()

    def test_notify_daily_summary_returns_false(self):
        result = self.notifier.notify_daily_summary("report text")
        assert result is False

    def test_notify_health_alert_returns_false(self):
        result = self.notifier.notify_health_alert(["Database"], ["ERROR: connection refused"])
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
class TestNotifierMissingCredentials:
    """Enabled but missing token/chat_id → no-op."""

    def test_missing_token_returns_false(self):
        notifier, _ = _make_notifier(enabled=True, token="", chat="12345")
        with patch("requests.post") as mock_post:
            result = notifier._send("test")
        assert result is False
        mock_post.assert_not_called()

    def test_missing_chat_returns_false(self):
        notifier, _ = _make_notifier(enabled=True, token="TOKEN", chat="")
        with patch("requests.post") as mock_post:
            result = notifier._send("test")
        assert result is False
        mock_post.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
class TestNotifierSendSuccess:
    """Tests for the _send() method with mocked HTTP."""

    def _notifier_with_mock_post(self, status_code: int = 200):
        notifier, mod = _make_notifier()
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.raise_for_status = MagicMock()
        return notifier, mod, mock_resp

    def test_send_calls_telegram_api(self):
        notifier, mod, mock_resp = self._notifier_with_mock_post(200)
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "TOK", "TELEGRAM_CHAT_ID": "999"}):
            import importlib; importlib.reload(mod)
            notifier2 = mod.Notifier()
            with patch("requests.post", return_value=mock_resp) as mock_post:
                result = notifier2._send("hello world")
            mock_post.assert_called_once()
            assert result is True

    def test_send_returns_true_on_200(self):
        notifier, mod, mock_resp = self._notifier_with_mock_post(200)
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "TOK", "TELEGRAM_CHAT_ID": "999"}):
            import importlib; importlib.reload(mod)
            notifier2 = mod.Notifier()
            with patch("requests.post", return_value=mock_resp):
                assert notifier2._send("hello") is True

    def test_send_retries_on_network_error(self):
        import requests as req_mod
        notifier, mod, _ = self._notifier_with_mock_post()
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "TOK", "TELEGRAM_CHAT_ID": "999"}):
            import importlib; importlib.reload(mod)
            notifier2 = mod.Notifier()
            with patch("requests.post", side_effect=req_mod.exceptions.ConnectionError("fail")) as mp:
                with patch("time.sleep"):   # don't actually sleep
                    result = notifier2._send("test")
            # Should have tried _MAX_RETRIES times
            assert mp.call_count == mod._MAX_RETRIES
            assert result is False

    def test_send_no_retry_on_4xx(self):
        import requests as req_mod
        notifier, mod, _ = self._notifier_with_mock_post()
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "TOK", "TELEGRAM_CHAT_ID": "999"}):
            import importlib; importlib.reload(mod)
            notifier2 = mod.Notifier()
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            http_err = req_mod.exceptions.HTTPError(response=mock_resp)
            http_err.response = mock_resp
            with patch("requests.post", side_effect=http_err) as mp:
                with patch("time.sleep"):
                    result = notifier2._send("test")
            # 4xx → stop immediately, no retry
            assert mp.call_count == 1
            assert result is False


# ─────────────────────────────────────────────────────────────────────────────
class TestNotifierMessages:
    """Tests that each event method calls _send with expected content."""

    def setup_method(self):
        self.notifier, self.mod = _make_notifier()

    def _assert_sends_containing(self, method_call, *substrings):
        with patch.object(self.notifier, "_send", return_value=True) as mock_send:
            method_call()
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
            for s in substrings:
                assert s in text, f"Expected '{s}' in message: {text}"

    def test_notify_startup_contains_started(self):
        self._assert_sends_containing(
            self.notifier.notify_startup,
            "started"
        )

    def test_notify_completed_contains_task_id_and_file(self):
        # result_path is MarkdownV2-escaped: underscore → \_ and dot → \.
        # Assert on task id, keyword 'completed', and presence of 'results'.
        with patch.object(self.notifier, "_send", return_value=True) as mock_send:
            self.notifier.notify_completed(42, "results/task_42.md", "Great!")
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
        assert "42" in text
        assert "completed" in text.lower()
        assert "results" in text

    def test_notify_completed_truncates_preview(self):
        long_response = "x" * 500
        with patch.object(self.notifier, "_send", return_value=True) as mock_send:
            self.notifier.notify_completed(1, "r.md", long_response)
            text = mock_send.call_args[0][0]
            # Preview should be truncated
            assert len(text) < len(long_response)

    def test_notify_limit_hit_contains_reset_time(self):
        reset = datetime.now() + timedelta(hours=3)
        reset_str = reset.strftime("%H:%M:%S")
        with patch.object(self.notifier, "_send", return_value=True) as mock_send:
            self.notifier.notify_limit_hit(5, reset)
            text = mock_send.call_args[0][0]
        assert reset_str in text or "3" in text or "180" in text  # time or minutes

    def test_notify_error_contains_message(self):
        self._assert_sends_containing(
            lambda: self.notifier.notify_error(7, "Connection refused"),
            "Connection refused"
        )

    def test_notify_error_none_task_id(self):
        with patch.object(self.notifier, "_send", return_value=True) as mock_send:
            self.notifier.notify_error(None, "Worker crashed")
            mock_send.assert_called_once()

    def test_notify_shutdown_contains_stopped(self):
        self._assert_sends_containing(
            self.notifier.notify_shutdown,
            "stopped"
        )

    def test_notify_daily_summary_passes_report_text(self):
        report = "🌅 Morning Report\n✅ Completed: 5"
        with patch.object(self.notifier, "_send", return_value=True) as mock_send:
            self.notifier.notify_daily_summary(report)
            text = mock_send.call_args[0][0]
        assert report in text or "Morning" in text

    def test_notify_health_alert_contains_check_names(self):
        with patch.object(self.notifier, "_send", return_value=True) as mock_send:
            self.notifier.notify_health_alert(["Database", "Disk Space"], ["conn fail", "95% used"])
            text = mock_send.call_args[0][0]
        assert "Database" in text
        assert "Disk Space" in text


# ─────────────────────────────────────────────────────────────────────────────
class TestEscapeMd:
    """Tests for the _escape_md helper."""

    def test_escapes_asterisk(self):
        from src.notifier import _escape_md
        assert "\\*" in _escape_md("hello *world*")

    def test_escapes_underscore(self):
        from src.notifier import _escape_md
        assert "\\_" in _escape_md("hello_world")

    def test_does_not_alter_plain_text(self):
        from src.notifier import _escape_md
        plain = "Hello world 123"
        assert _escape_md(plain) == plain

    def test_handles_empty_string(self):
        from src.notifier import _escape_md
        assert _escape_md("") == ""

    def test_handles_non_string(self):
        from src.notifier import _escape_md
        assert _escape_md(42) == "42"


# ─────────────────────────────────────────────────────────────────────────────
class TestNotifyOnFlags:
    """Tests that NOTIFY_ON_* flags suppress individual event methods."""

    def _notifier_with_flags(self, complete="false", limit="false", error="false"):
        notifier, mod = _make_notifier()
        with patch.dict(os.environ, {
            "TELEGRAM_NOTIFY_ON_COMPLETE": complete,
            "TELEGRAM_NOTIFY_ON_LIMIT": limit,
            "TELEGRAM_NOTIFY_ON_ERROR": error,
        }):
            import importlib; importlib.reload(mod)
            return mod.Notifier(), mod

    def test_notify_on_complete_false_skips_send(self):
        notifier, _ = self._notifier_with_flags(complete="false")
        with patch.object(notifier, "_send") as mock_send:
            notifier.notify_completed(1, "r.md", "ok")
        mock_send.assert_not_called()

    def test_notify_on_limit_false_skips_send(self):
        notifier, _ = self._notifier_with_flags(limit="false")
        with patch.object(notifier, "_send") as mock_send:
            notifier.notify_limit_hit(1, datetime.now() + timedelta(hours=1))
        mock_send.assert_not_called()

    def test_notify_on_error_false_skips_send(self):
        notifier, _ = self._notifier_with_flags(error="false")
        with patch.object(notifier, "_send") as mock_send:
            notifier.notify_error(1, "boom")
        mock_send.assert_not_called()
