"""
Tests for src/claude_adapter.py — Phase 2 Claude Adapter

All tests use unittest.mock to patch Playwright so no real browser is needed.
This makes the tests fast, deterministic, and safe to run in CI.

Test coverage:
    • ClaudeAdapter initialisation
    • start() / close() lifecycle
    • Context-manager protocol (__enter__ / __exit__)
    • check_login() — logged in, expired, unclear
    • send_prompt() — success, rate limit, login redirect
    • _is_limit_message() — all patterns
    • _extract_reset_time() — all four parsing patterns + fallback
    • _extract_response_text() — selector cascade
    • _find_input() — primary / fallback selectors
    • _save_screenshot() — success and failure
    • Custom exception hierarchy
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest

# ── Ensure src/ is importable ──────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.claude_adapter import (
    _LIMIT_PATTERNS,
    BrowserStartupError,
    ClaudeAdapter,
    ClaudeAdapterError,
    LimitDetectedException,
    LoginExpiredException,
    ResponseExtractionError,
    _Selectors,
)


# ─── Helpers / Fixtures ────────────────────────────────────────────────────────

def make_adapter(**kwargs) -> ClaudeAdapter:
    """Return a ClaudeAdapter with test defaults."""
    return ClaudeAdapter(user_data_dir="test_browser_data", headless=True, **kwargs)


def _mock_page(
    *,
    has_new_chat: bool = True,
    has_input: bool = True,
    has_email_input: bool = False,
    stop_btn_times_out: bool = True,
    copy_btn_times_out: bool = True,
    response_text: str = "The answer is 42.",
    locator_count: int = 0,
) -> MagicMock:
    """
    Build a MagicMock that mimics enough of the Playwright Page API
    for our adapter's selectors and interactions to work.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page = MagicMock()

    # goto / wait_for_load_state / wait_for_timeout — no-ops
    page.goto.return_value = None
    page.wait_for_load_state.return_value = None
    page.wait_for_timeout.return_value = None
    page.title.return_value = "Claude"

    # keyboard
    page.keyboard = MagicMock()
    page.keyboard.press.return_value = None

    # wait_for_selector — simulate different elements being present/absent
    def _wait_for_selector(selector, **kwargs):
        state = kwargs.get("state", "attached")
        timeout = kwargs.get("timeout", 30000)

        # Stop/Copy buttons always timeout in most tests
        if "Stop" in selector or "Copy" in selector:
            raise PlaywrightTimeout("timeout")

        if selector == _Selectors.LOGIN_EMAIL:
            if has_email_input:
                return MagicMock()
            raise PlaywrightTimeout("no email input")

        if selector == _Selectors.NEW_CHAT_BUTTON:
            if has_new_chat:
                return MagicMock()
            raise PlaywrightTimeout("no new chat button")

        if selector in (
            _Selectors.INPUT_PRIMARY,
            _Selectors.INPUT_FALLBACK,
        ):
            if has_input:
                m = MagicMock()
                m.click.return_value = None
                m.fill.return_value = None
                m.type.return_value = None
                return m
            raise PlaywrightTimeout("no input")

        return MagicMock()

    page.wait_for_selector.side_effect = _wait_for_selector

    # locator — returns a mock with count() and inner_text()
    def _locator(selector):
        loc = MagicMock()

        # email field
        if selector == _Selectors.LOGIN_EMAIL:
            loc.count.return_value = 1 if has_email_input else 0
        else:
            loc.count.return_value = locator_count

        # chained .last
        last = MagicMock()
        last.inner_text.return_value = response_text
        loc.last = last

        return loc

    page.locator.side_effect = _locator

    # screenshot
    page.screenshot.return_value = None

    return page


@pytest.fixture
def adapter():
    """Return a ClaudeAdapter with Playwright patched out."""
    with patch("src.claude_adapter.sync_playwright") as mock_pw_fn:
        mock_pw = MagicMock()
        mock_pw_fn.return_value.start.return_value = mock_pw

        mock_context = MagicMock()
        mock_context.pages = []
        mock_pw.chromium.launch_persistent_context.return_value = mock_context

        mock_page = _mock_page()
        mock_context.new_page.return_value = mock_page

        a = make_adapter()
        a.start()

        # Expose mocks for assertions
        a._mock_context = mock_context
        a._mock_page_obj = mock_page

        yield a


# ─── Exception Hierarchy ───────────────────────────────────────────────────────

class TestExceptions:
    def test_login_expired_is_adapter_error(self):
        assert issubclass(LoginExpiredException, ClaudeAdapterError)

    def test_browser_startup_is_adapter_error(self):
        assert issubclass(BrowserStartupError, ClaudeAdapterError)

    def test_response_extraction_is_adapter_error(self):
        assert issubclass(ResponseExtractionError, ClaudeAdapterError)

    def test_limit_detected_stores_reset_time_and_message(self):
        reset = datetime(2026, 1, 1, 10, 0, 0)
        exc = LimitDetectedException(reset_time=reset, message="Rate limit hit")
        assert exc.reset_time == reset
        assert exc.message == "Rate limit hit"
        assert "2026" in str(exc)


# ─── Initialisation ────────────────────────────────────────────────────────────

class TestInit:
    def test_default_values_applied(self):
        a = ClaudeAdapter()
        assert a.user_data_dir == Path("browser_data")
        assert isinstance(a.headless, bool)

    def test_custom_values_stored(self):
        a = ClaudeAdapter(user_data_dir="custom_dir", headless=False, timeout_ms=60000)
        assert a.user_data_dir == Path("custom_dir")
        assert a.headless is False
        assert a.timeout_ms == 60000

    def test_page_raises_before_start(self):
        a = ClaudeAdapter()
        with pytest.raises(RuntimeError, match="not started"):
            _ = a.page


# ─── start() / close() ─────────────────────────────────────────────────────────

class TestStartClose:
    def test_start_creates_page(self, adapter):
        assert adapter._page is not None

    def test_start_uses_existing_page_if_available(self):
        """If the persistent context already has a page, reuse it."""
        with patch("src.claude_adapter.sync_playwright") as mock_fn:
            mock_pw = MagicMock()
            mock_fn.return_value.start.return_value = mock_pw

            existing_page = MagicMock()
            mock_context = MagicMock()
            mock_context.pages = [existing_page]
            mock_pw.chromium.launch_persistent_context.return_value = mock_context

            a = make_adapter()
            a.start()

            assert a._page is existing_page
            mock_context.new_page.assert_not_called()

    def test_close_resets_internal_state(self, adapter):
        adapter.close()
        assert adapter._page is None
        assert adapter._context is None
        assert adapter._playwright is None

    def test_close_is_safe_before_start(self):
        a = ClaudeAdapter()
        a.close()  # should not raise

    def test_start_raises_browser_startup_error_on_failure(self):
        with patch("src.claude_adapter.sync_playwright") as mock_fn:
            mock_fn.return_value.start.side_effect = OSError("chromium not found")
            a = make_adapter()
            with pytest.raises(BrowserStartupError):
                a.start()

    def test_context_manager_calls_start_and_close(self):
        with patch("src.claude_adapter.sync_playwright") as mock_fn:
            mock_pw = MagicMock()
            mock_fn.return_value.start.return_value = mock_pw
            mock_context = MagicMock()
            mock_context.pages = []
            mock_pw.chromium.launch_persistent_context.return_value = mock_context
            mock_context.new_page.return_value = MagicMock()

            a = make_adapter()
            with a:
                assert a._page is not None
            assert a._page is None  # closed on exit

    def test_context_manager_closes_on_exception(self):
        with patch("src.claude_adapter.sync_playwright") as mock_fn:
            mock_pw = MagicMock()
            mock_fn.return_value.start.return_value = mock_pw
            mock_context = MagicMock()
            mock_context.pages = []
            mock_pw.chromium.launch_persistent_context.return_value = mock_context
            mock_context.new_page.return_value = MagicMock()

            a = make_adapter()
            with pytest.raises(ValueError):
                with a:
                    raise ValueError("test error")
            assert a._page is None  # still cleaned up


# ─── check_login() ─────────────────────────────────────────────────────────────

class TestCheckLogin:
    def test_returns_true_when_new_chat_button_found(self, adapter):
        adapter._page = _mock_page(has_new_chat=True)
        assert adapter.check_login() is True

    def test_returns_true_when_input_found_but_no_new_chat(self, adapter):
        adapter._page = _mock_page(has_new_chat=False, has_input=True)
        assert adapter.check_login() is True

    def test_raises_login_expired_when_email_input_visible(self, adapter):
        adapter._page = _mock_page(
            has_new_chat=False, has_input=False, has_email_input=True
        )
        with pytest.raises(LoginExpiredException):
            adapter.check_login()

    def test_returns_true_when_status_unclear(self, adapter):
        """Unclear status (no new-chat, no input, no email form) should not raise."""
        adapter._page = _mock_page(
            has_new_chat=False, has_input=False, has_email_input=False
        )
        result = adapter.check_login()
        assert result is True


# ─── send_prompt() ─────────────────────────────────────────────────────────────

class TestSendPrompt:
    def test_returns_ok_with_response_text(self, adapter):
        adapter._page = _mock_page(
            has_new_chat=True,
            has_email_input=False,
            has_input=True,
            response_text="Paris is the capital of France.",
            locator_count=1,
        )
        result = adapter.send_prompt("What is the capital of France?")
        assert result["status"] == "ok"
        assert "Paris" in result["text"]

    def test_returns_limit_when_rate_limited(self, adapter):
        limit_text = "You've reached your usage limit. Resets in 3 hours."
        adapter._page = _mock_page(
            has_input=True,
            has_email_input=False,
            response_text=limit_text,
            locator_count=1,
        )
        result = adapter.send_prompt("Tell me something")
        assert result["status"] == "limit"
        assert isinstance(result["reset_time"], datetime)
        assert result["message"] == limit_text

    def test_raises_login_expired_when_redirected(self, adapter):
        page = _mock_page(has_email_input=True, has_input=False)
        adapter._page = page
        with pytest.raises(LoginExpiredException):
            adapter.send_prompt("Hello")

    def test_raises_value_error_on_empty_prompt(self, adapter):
        with pytest.raises(ValueError):
            adapter.send_prompt("")

    def test_raises_value_error_on_blank_prompt(self, adapter):
        with pytest.raises(ValueError):
            adapter.send_prompt("   ")

    def test_fill_called_with_prompt_text(self, adapter):
        mock_input = MagicMock()
        adapter._page = _mock_page(
            has_input=True, response_text="Answer", locator_count=1
        )
        with patch.object(adapter, "_find_input", return_value=mock_input):
            with patch.object(adapter, "_wait_for_response", return_value="Answer"):
                adapter.send_prompt("My question")
        mock_input.fill.assert_called_once_with("My question")

    def test_screenshot_saved_on_timeout(self, adapter):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        with patch.object(adapter, "_find_input") as mock_find:
            mock_find.side_effect = PlaywrightTimeout("timed out")
            with patch.object(adapter, "_save_screenshot") as mock_ss:
                with pytest.raises(RuntimeError):
                    adapter.send_prompt("Hello")
                mock_ss.assert_called_once_with("timeout")


# ─── _is_limit_message() ───────────────────────────────────────────────────────

class TestIsLimitMessage:
    @pytest.mark.parametrize(
        "text",
        [
            "You've reached your usage limit for today.",
            "Usage limit reached. Please try again later.",
            "Your message limit resets in 4 hours.",
            "You have used up your free tier messages.",
            "0 messages remaining until reset.",
            "You have 0 messages remaining.",
            "Rate limit exceeded.",
            "Too many requests, try again in 30 minutes.",
            "The service is temporarily unavailable.",
        ],
    )
    def test_detects_limit_messages(self, text):
        assert ClaudeAdapter._is_limit_message(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Here is the answer to your question.",
            "Python is a high-level programming language.",
            "The capital of France is Paris.",
            "I cannot help with that request.",  # refusal, not limit
        ],
    )
    def test_does_not_flag_normal_responses(self, text):
        assert ClaudeAdapter._is_limit_message(text) is False

    def test_case_insensitive(self):
        assert ClaudeAdapter._is_limit_message("USAGE LIMIT REACHED") is True
        assert ClaudeAdapter._is_limit_message("Rate Limit") is True


# ─── _extract_reset_time() ────────────────────────────────────────────────────

class TestExtractResetTime:
    def _approx_equal(self, dt1: datetime, dt2: datetime, delta_seconds: int = 5) -> bool:
        return abs((dt1 - dt2).total_seconds()) <= delta_seconds

    def test_hours_and_minutes_pattern(self):
        text = "Your limit resets in 3 hours and 45 minutes."
        reset = ClaudeAdapter._extract_reset_time(text)
        expected = datetime.now() + timedelta(hours=3, minutes=45)
        assert self._approx_equal(reset, expected)

    def test_hours_only_pattern(self):
        text = "Resets in 5 hours."
        reset = ClaudeAdapter._extract_reset_time(text)
        expected = datetime.now() + timedelta(hours=5)
        assert self._approx_equal(reset, expected)

    def test_minutes_only_pattern(self):
        text = "Please wait 30 minutes."
        reset = ClaudeAdapter._extract_reset_time(text)
        expected = datetime.now() + timedelta(minutes=30)
        assert self._approx_equal(reset, expected)

    def test_absolute_time_pm(self):
        text = "Your limit resets until 3:00 PM today."
        reset = ClaudeAdapter._extract_reset_time(text)
        assert reset.hour == 15
        assert reset.minute == 0

    def test_absolute_time_am(self):
        text = "Available again at 8:30 AM."
        reset = ClaudeAdapter._extract_reset_time(text)
        assert reset.hour == 8
        assert reset.minute == 30

    def test_absolute_time_past_rolls_to_tomorrow(self):
        # Force a time in the past by choosing midnight
        text = "Resets until 00:00 AM."
        reset = ClaudeAdapter._extract_reset_time(text)
        # Either today or tomorrow — must be in the future
        assert reset > datetime.now()

    def test_fallback_uses_default_hours(self):
        with patch("src.claude_adapter.DEFAULT_LIMIT_WAIT_HOURS", 7):
            reset = ClaudeAdapter._extract_reset_time("No time info here at all.")
            expected = datetime.now() + timedelta(hours=7)
            assert self._approx_equal(reset, expected, delta_seconds=10)

    def test_hours_and_minutes_singular_forms(self):
        text = "Wait 1 hour and 1 minute."
        reset = ClaudeAdapter._extract_reset_time(text)
        expected = datetime.now() + timedelta(hours=1, minutes=1)
        assert self._approx_equal(reset, expected)


# ─── _extract_response_text() ─────────────────────────────────────────────────

class TestExtractResponseText:
    def test_extracts_from_first_successful_selector(self, adapter):
        page = MagicMock()
        good_loc = MagicMock()
        good_loc.count.return_value = 1
        good_loc.last.inner_text.return_value = "  Great answer!  "
        page.locator.return_value = good_loc

        adapter._page = page
        result = adapter._extract_response_text()
        assert result == "Great answer!"

    def test_falls_back_to_body_when_all_containers_empty(self, adapter):
        page = MagicMock()
        empty_loc = MagicMock()
        empty_loc.count.return_value = 0
        empty_loc.last.inner_text.return_value = ""

        body_loc = MagicMock()
        body_loc.inner_text.return_value = "Fallback body text."

        def _loc(sel):
            if sel == "body":
                return body_loc
            return empty_loc

        page.locator.side_effect = _loc
        adapter._page = page
        result = adapter._extract_response_text()
        assert result == "Fallback body text."

    def test_raises_extraction_error_when_nothing_found(self, adapter):
        page = MagicMock()
        empty_loc = MagicMock()
        empty_loc.count.return_value = 0
        empty_loc.last.inner_text.return_value = ""
        empty_loc.inner_text.return_value = ""
        page.locator.return_value = empty_loc

        adapter._page = page
        with pytest.raises(ResponseExtractionError):
            adapter._extract_response_text()


# ─── _find_input() ─────────────────────────────────────────────────────────────

class TestFindInput:
    def test_uses_primary_selector_first(self, adapter):
        mock_input = MagicMock()
        adapter._page = _mock_page(has_input=True)
        # The mock page's wait_for_selector returns a MagicMock for INPUT_PRIMARY
        result = adapter._find_input()
        assert result is not None

    def test_raises_if_no_input_found(self, adapter):
        adapter._page = _mock_page(has_input=False, has_new_chat=False)
        with pytest.raises(RuntimeError, match="Could not locate the prompt input"):
            adapter._find_input()


# ─── _save_screenshot() ────────────────────────────────────────────────────────

class TestSaveScreenshot:
    def test_saves_screenshot_with_label_and_timestamp(self, adapter, tmp_path):
        with patch("src.claude_adapter.SCREENSHOTS_DIR", str(tmp_path)):
            adapter._page = MagicMock()
            result = adapter._save_screenshot("test_label")
            assert result is not None
            assert "test_label" in result
            adapter._page.screenshot.assert_called_once()

    def test_returns_none_on_failure(self, adapter):
        adapter._page = MagicMock()
        adapter._page.screenshot.side_effect = OSError("disk full")
        result = adapter._save_screenshot("fail")
        assert result is None


# ─── Selectors sanity check ────────────────────────────────────────────────────

class TestSelectors:
    def test_all_selectors_are_non_empty_strings(self):
        sel = _Selectors()
        for attr in dir(sel):
            if attr.startswith("_"):
                continue
            value = getattr(sel, attr)
            if isinstance(value, str):
                assert value.strip(), f"Selector {attr!r} should not be empty"
            elif isinstance(value, list):
                for item in value:
                    assert isinstance(item, str) and item.strip(), \
                        f"Selector list {attr!r} contains an empty string"
