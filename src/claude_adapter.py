"""
Claude Adapter — Playwright-based browser automation for Claude.ai

Responsibilities:
    • Manage a persistent Chromium browser profile (cookies survive restarts)
    • Detect whether the session is still logged in
    • Submit prompts to Claude.ai and wait for full response
    • Detect usage-limit messages and extract the reset time
    • Save error screenshots for post-mortem debugging
    • Provide a clean context-manager interface for the agent worker

Usage (in agent_worker.py):
    with ClaudeAdapter('browser_data') as adapter:
        result = adapter.send_prompt("Explain the CAP theorem")
        if result['status'] == 'ok':
            print(result['text'])
        else:
            print("Rate limited until", result['reset_time'])
"""

import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

# ─── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ─── Environment Configuration ─────────────────────────────────────────────────
CLAUDE_URL: str = os.getenv("CLAUDE_URL", "https://claude.ai")
BROWSER_TIMEOUT: int = int(os.getenv("BROWSER_TIMEOUT", "30000"))  # ms
HEADLESS: bool = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
SCREENSHOTS_DIR: str = os.getenv("SCREENSHOTS_DIR", "logs/screenshots")
DEFAULT_LIMIT_WAIT_HOURS: int = int(os.getenv("DEFAULT_LIMIT_WAIT_HOURS", "5"))

# ─── Limit Detection Patterns (ordered most-specific → least-specific) ─────────
_LIMIT_PATTERNS: List[str] = [
    r"usage limit",
    r"limit reached",
    r"resets?\s+in",
    r"you(?:'ve| have) used up",
    r"\d+\s*messages?\s+remaining",
    r"messages?\s+remaining.*?0",
    r"rate.?limit",
    r"try again in",
    r"temporarily unavailable",
    r"too many requests",
]


# ─── Custom Exceptions ─────────────────────────────────────────────────────────

class ClaudeAdapterError(Exception):
    """Base exception for all adapter errors."""


class LoginExpiredException(ClaudeAdapterError):
    """Raised when the Claude.ai session cookie has expired."""


class BrowserStartupError(ClaudeAdapterError):
    """Raised when Playwright / Chromium fails to start."""


class ResponseExtractionError(ClaudeAdapterError):
    """Raised when no response text can be found after submission."""


class LimitDetectedException(ClaudeAdapterError):
    """
    Raised when a usage-limit message is detected.

    Attributes:
        reset_time: Datetime when the limit will lift.
        message:    Raw text of the limit message from Claude.
    """

    def __init__(self, reset_time: datetime, message: str) -> None:
        self.reset_time = reset_time
        self.message = message
        super().__init__(f"Usage limit detected. Resets at {reset_time.isoformat()}")


# ─── Selector Registry ─────────────────────────────────────────────────────────
# Centralised so they're easy to update if Claude.ai changes its DOM.

class _Selectors:
    # Chat input (the main editable area)
    INPUT_PRIMARY = 'div[contenteditable="true"]'
    INPUT_FALLBACK = "[contenteditable]"

    # Login indicators
    LOGIN_EMAIL = 'input[type="email"]'
    NEW_CHAT_BUTTON = 'button:has-text("New chat")'

    # Response completion signals
    STOP_BUTTON = 'button[aria-label*="Stop"]'
    COPY_BUTTON = 'button[aria-label*="Copy"]'

    # Response text containers (tried in order)
    RESPONSE_CONTAINERS = [
        '[data-testid="conversation-turn"]:last-child',
        'div[class*="message"]:last-child',
        'div[class*="response"]:last-child',
    ]

    # Limit / overlay dialogs
    LIMIT_DIALOG = 'div[class*="limit"], div[class*="usage"], dialog'


_SEL = _Selectors()


# ─── Main Adapter Class ────────────────────────────────────────────────────────

class ClaudeAdapter:
    """
    High-level interface for automating Claude.ai via Playwright.

    Lifecycle (choose one):
        Option A — explicit:
            adapter = ClaudeAdapter('browser_data')
            adapter.start()
            result = adapter.send_prompt("Hello")
            adapter.close()

        Option B — context manager (recommended):
            with ClaudeAdapter('browser_data') as adapter:
                result = adapter.send_prompt("Hello")
    """

    def __init__(
        self,
        user_data_dir: str = "browser_data",
        headless: bool = HEADLESS,
        timeout_ms: int = BROWSER_TIMEOUT,
    ) -> None:
        """
        Args:
            user_data_dir: Path where Chromium stores cookies/profile data.
            headless:      Run without a visible window (True for server use).
            timeout_ms:    Default Playwright action timeout in milliseconds.
        """
        self.user_data_dir = Path(user_data_dir)
        self.headless = headless
        self.timeout_ms = timeout_ms

        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        logger.info(
            "ClaudeAdapter created (user_data_dir=%s, headless=%s, timeout=%dms)",
            self.user_data_dir,
            headless,
            timeout_ms,
        )

    # ── Context-manager support ────────────────────────────────────────────────

    def __enter__(self) -> "ClaudeAdapter":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ── Public interface ───────────────────────────────────────────────────────

    @property
    def page(self) -> Page:
        """Active Playwright page — raises if browser is not started."""
        if self._page is None:
            raise RuntimeError("Browser is not started. Call start() first.")
        return self._page

    def start(self) -> None:
        """
        Launch Playwright and open the persistent Chromium profile.

        The profile directory (`user_data_dir`) persists cookies between
        restarts — this is how the session is maintained without re-logging in.

        Raises:
            BrowserStartupError: If Playwright or Chromium fails to launch.
        """
        try:
            logger.info("Starting browser…")
            self.user_data_dir.mkdir(parents=True, exist_ok=True)

            self._playwright = sync_playwright().start()

            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-extensions",
                    "--disable-gpu",
                ],
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=False,
            )

            # Reuse the first existing page or open a new one
            if self._context.pages:
                self._page = self._context.pages[0]
                logger.debug("Reusing existing browser tab")
            else:
                self._page = self._context.new_page()
                logger.debug("Opened new browser tab")

            self._page.set_default_timeout(self.timeout_ms)
            logger.info("Browser ready ✓")

        except BrowserStartupError:
            raise
        except Exception as exc:
            logger.error("Browser startup failed: %s", exc)
            raise BrowserStartupError(f"Browser startup failed: {exc}") from exc

    def close(self) -> None:
        """
        Gracefully shut down the browser and Playwright runtime.
        Safe to call even if the browser was never started.
        """
        try:
            if self._context:
                self._context.close()
                self._context = None
            if self._playwright:
                self._playwright.stop()
                self._playwright = None
            self._page = None
            logger.info("Browser closed ✓")
        except Exception as exc:
            logger.warning("Error while closing browser: %s", exc)

    def check_login(self) -> bool:
        """
        Navigate to Claude and return True if a valid session exists.

        Raises:
            LoginExpiredException: If the email login form is visible.
            RuntimeError:          On unexpected page errors.
        """
        try:
            logger.info("Checking login status…")
            self.page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=self.timeout_ms)
            self.page.wait_for_timeout(2000)

            # Check #1 — "New chat" button (sidebar)
            try:
                self.page.wait_for_selector(_SEL.NEW_CHAT_BUTTON, timeout=5000)
                logger.info("Logged in ✓ (New chat button found)")
                return True
            except PlaywrightTimeout:
                pass

            # Check #2 — editable chat input (direct chat view)
            try:
                self.page.wait_for_selector(_SEL.INPUT_PRIMARY, timeout=5000)
                logger.info("Logged in ✓ (chat input found)")
                return True
            except PlaywrightTimeout:
                pass

            # Check #3 — login form = definitely not logged in
            if self.page.locator(_SEL.LOGIN_EMAIL).count() > 0:
                logger.error("Not logged in — email input visible")
                raise LoginExpiredException(
                    "Session expired. Run scripts/manual_login.py to log in again."
                )

            logger.warning("Login status unclear — proceeding cautiously")
            return True

        except LoginExpiredException:
            raise
        except Exception as exc:
            logger.error("Login check failed: %s", exc)
            raise RuntimeError(f"Login check failed: {exc}") from exc

    def send_prompt(self, prompt_text: str) -> Dict[str, Any]:
        """
        Submit a prompt to Claude.ai and wait for the complete response.

        Args:
            prompt_text: The user's prompt (any length).

        Returns:
            On success::

                {"status": "ok", "text": "<response text>"}

            On rate-limit::

                {
                    "status":     "limit",
                    "reset_time": datetime,   # when the limit lifts
                    "message":    str,        # raw limit message text
                }

        Raises:
            LoginExpiredException:    If the session cookie has expired.
            ResponseExtractionError:  If no response text can be found.
            RuntimeError:             On timeout or other browser errors.
        """
        if not prompt_text or not prompt_text.strip():
            raise ValueError("prompt_text cannot be empty")

        logger.info("Sending prompt (%d chars)…", len(prompt_text))

        try:
            # ── Step 1: open a fresh chat ──────────────────────────────────
            self.page.goto(f"{CLAUDE_URL}/new", wait_until="domcontentloaded",
                           timeout=self.timeout_ms)
            self.page.wait_for_timeout(1500)

            # ── Step 2: check if we got redirected to login ────────────────
            if self.page.locator(_SEL.LOGIN_EMAIL).count() > 0:
                raise LoginExpiredException("Redirected to login page mid-session")

            # ── Step 3: find the prompt input ─────────────────────────────
            input_field = self._find_input()

            # ── Step 4: type the prompt ────────────────────────────────────
            input_field.click()
            # fill() is faster than type(); use type() for human-like pacing
            input_field.fill(prompt_text)
            self.page.wait_for_timeout(300)  # short settle

            # ── Step 5: submit ─────────────────────────────────────────────
            self.page.keyboard.press("Enter")
            logger.info("Prompt submitted — waiting for response…")

            # ── Step 6: wait for completion and extract text ───────────────
            response_text = self._wait_for_response()

            # ── Step 7: classify the response ─────────────────────────────
            if self._is_limit_message(response_text):
                logger.warning("Usage limit detected in response")
                reset_time = self._extract_reset_time(response_text)
                return {
                    "status": "limit",
                    "reset_time": reset_time,
                    "message": response_text,
                }

            logger.info("Response received (%d chars) ✓", len(response_text))
            return {"status": "ok", "text": response_text}

        except (LoginExpiredException, ValueError):
            raise
        except PlaywrightTimeout as exc:
            logger.error("Playwright timeout: %s", exc)
            self._save_screenshot("timeout")
            raise RuntimeError(f"Timeout waiting for response: {exc}") from exc
        except ResponseExtractionError:
            self._save_screenshot("extraction_error")
            raise
        except Exception as exc:
            logger.error("Unexpected error in send_prompt: %s", exc)
            self._save_screenshot("unexpected_error")
            raise RuntimeError(f"Prompt submission failed: {exc}") from exc

    # ── Private helpers ────────────────────────────────────────────────────────

    def _find_input(self) -> Any:
        """
        Locate the chat input field using primary then fallback selectors.

        Returns:
            Playwright ElementHandle for the contenteditable input.

        Raises:
            RuntimeError: If no input field can be found.
        """
        try:
            field = self.page.wait_for_selector(_SEL.INPUT_PRIMARY, timeout=12000)
            if field:
                logger.debug("Input found via primary selector")
                return field
        except PlaywrightTimeout:
            logger.warning("Primary input selector timed out, trying fallback…")

        try:
            field = self.page.wait_for_selector(_SEL.INPUT_FALLBACK, timeout=6000)
            if field:
                logger.debug("Input found via fallback selector")
                return field
        except PlaywrightTimeout:
            pass

        raise RuntimeError(
            "Could not locate the prompt input field. "
            "Claude.ai may have updated its DOM structure."
        )

    def _wait_for_response(self) -> str:
        """
        Wait until Claude finishes generating its response, then extract the text.

        Strategy (in order of preference):
            1. Wait for the Stop-generation button to appear, then disappear.
            2. Wait for a Copy button to appear (response complete signal).
            3. Fall back to network idle + a fixed wait.

        Returns:
            The full response text as a plain string.

        Raises:
            ResponseExtractionError: If no text can be extracted.
        """
        # Strategy 1 — Stop button lifecycle
        try:
            self.page.wait_for_selector(_SEL.STOP_BUTTON, timeout=8000)
            logger.debug("Stop button appeared — generation in progress")
            # Wait up to 5 minutes for the response to finish
            self.page.wait_for_selector(_SEL.STOP_BUTTON, state="hidden", timeout=300_000)
            logger.debug("Stop button gone — generation complete")
        except PlaywrightTimeout:
            logger.debug("Stop button strategy timed out — trying strategy 2")

        # Strategy 2 — Copy button appearance
        try:
            self.page.wait_for_selector(_SEL.COPY_BUTTON, timeout=12000)
            logger.debug("Copy button appeared — response ready")
        except PlaywrightTimeout:
            logger.debug("Copy button not found — falling back to network idle")

        # Strategy 3 — Network idle + safety buffer
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            pass  # not fatal

        self.page.wait_for_timeout(2500)  # final safety buffer

        # ── Extract text ───────────────────────────────────────────────────
        return self._extract_response_text()

    def _extract_response_text(self) -> str:
        """
        Try multiple DOM strategies to pull the assistant's response text.

        Returns:
            Response text (stripped).

        Raises:
            ResponseExtractionError: If all strategies return empty text.
        """
        for selector in _SEL.RESPONSE_CONTAINERS:
            try:
                locator = self.page.locator(selector)
                if locator.count() > 0:
                    text = locator.last.inner_text(timeout=5000).strip()
                    if text and len(text) >= 5:
                        logger.debug("Extracted %d chars via selector '%s'", len(text), selector)
                        return text
            except Exception as exc:
                logger.debug("Selector '%s' failed: %s", selector, exc)

        # Last resort — grab all visible text from the page body
        try:
            body_text = self.page.locator("body").inner_text(timeout=5000).strip()
            if body_text and len(body_text) >= 5:
                logger.warning("Falling back to full body text extraction")
                return body_text
        except Exception:
            pass

        raise ResponseExtractionError(
            "Could not extract response text from Claude.ai. "
            "Check logs/screenshots/ for a screenshot of the current page state."
        )

    # ── Limit detection ────────────────────────────────────────────────────────

    @staticmethod
    def _is_limit_message(text: str) -> bool:
        """
        Return True if the text contains a usage-limit indicator.

        Checks against ``_LIMIT_PATTERNS`` (case-insensitive).
        """
        text_lower = text.lower()
        for pattern in _LIMIT_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                logger.debug("Limit pattern matched: '%s'", pattern)
                return True
        return False

    @staticmethod
    def _extract_reset_time(limit_text: str) -> datetime:
        """
        Parse the reset time from a usage-limit message.

        Tries four patterns in order:
            1. "X hours and Y minutes"
            2. "X hours"
            3. "X minutes"
            4. "until H:MM AM/PM" (absolute clock time)
            5. Fallback: DEFAULT_LIMIT_WAIT_HOURS from environment

        Args:
            limit_text: Raw text of the limit message.

        Returns:
            Datetime when the limit is expected to lift.
        """
        now = datetime.now()

        # Pattern 1 — "X hours and Y minutes" / "X hours Y minutes"
        m = re.search(
            r"(\d+)\s*hours?\s+(?:and\s+)?(\d+)\s*minutes?",
            limit_text,
            re.IGNORECASE,
        )
        if m:
            h, mins = int(m.group(1)), int(m.group(2))
            reset = now + timedelta(hours=h, minutes=mins)
            logger.info("Reset time: +%dh %dm → %s", h, mins, reset)
            return reset

        # Pattern 2 — "X hours"
        m = re.search(r"(\d+)\s*hours?", limit_text, re.IGNORECASE)
        if m:
            h = int(m.group(1))
            reset = now + timedelta(hours=h)
            logger.info("Reset time: +%dh → %s", h, reset)
            return reset

        # Pattern 3 — "X minutes"
        m = re.search(r"(\d+)\s*minutes?", limit_text, re.IGNORECASE)
        if m:
            mins = int(m.group(1))
            reset = now + timedelta(minutes=mins)
            logger.info("Reset time: +%dm → %s", mins, reset)
            return reset

        # Pattern 4 — absolute time "until 3:00 PM" / "at 15:30"
        m = re.search(
            r"(?:until|at)\s+(\d{1,2}):(\d{2})\s*(AM|PM)?",
            limit_text,
            re.IGNORECASE,
        )
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
            period = (m.group(3) or "").upper()
            if period == "PM" and hour != 12:
                hour += 12
            elif period == "AM" and hour == 12:
                hour = 0
            reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if reset <= now:
                reset += timedelta(days=1)
            logger.info("Reset time (absolute clock): %s", reset)
            return reset

        # Fallback
        default_h = DEFAULT_LIMIT_WAIT_HOURS
        reset = now + timedelta(hours=default_h)
        logger.warning(
            "Could not parse reset time from limit message — using default %dh → %s",
            default_h,
            reset,
        )
        return reset

    # ── Utility ────────────────────────────────────────────────────────────────

    def _save_screenshot(self, label: str) -> Optional[str]:
        """
        Capture and save a full-page screenshot for debugging.

        Args:
            label: Short descriptor used in the filename.

        Returns:
            Path to saved screenshot, or None on failure.
        """
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            shots_dir = Path(SCREENSHOTS_DIR)
            shots_dir.mkdir(parents=True, exist_ok=True)
            path = shots_dir / f"error_{label}_{ts}.png"
            self.page.screenshot(path=str(path), full_page=True)
            logger.info("Screenshot saved: %s", path)
            return str(path)
        except Exception as exc:
            logger.warning("Failed to save screenshot: %s", exc)
            return None

    def get_page_title(self) -> str:
        """Return the current page title (useful for health checks)."""
        try:
            return self.page.title()
        except Exception:
            return ""

    def navigate_to_new_chat(self) -> None:
        """Navigate to a fresh Claude chat session."""
        self.page.goto(f"{CLAUDE_URL}/new", wait_until="domcontentloaded",
                       timeout=self.timeout_ms)
        self.page.wait_for_timeout(1000)


# ─── Module self-test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    print("Claude Adapter — browser connectivity test")
    print("This opens a visible browser window.")
    print()

    adapter = ClaudeAdapter("browser_data", headless=False)
    try:
        adapter.start()
        logged_in = adapter.check_login()
        print(f"Login status: {'✓ logged in' if logged_in else '✗ not logged in'}")
        input("\nPress Enter to close…")
    except LoginExpiredException:
        print("✗ Not logged in — run scripts/manual_login.py first")
        sys.exit(1)
    finally:
        adapter.close()
