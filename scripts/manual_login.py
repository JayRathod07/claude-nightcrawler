"""
Manual Login Helper — One-time interactive script to authenticate Claude.ai

This opens a visible (non-headless) Chromium window so you can log in manually.
Once logged in, the session cookies are saved in browser_data/ and reused
by the agent worker on every subsequent run — no re-login needed.

Usage (run once on first setup, or after session expiry):
    python scripts/manual_login.py

Requirements:
    • A display (or X11 forwarding / VNC on headless servers)
    • Playwright + Chromium already installed
"""
import logging
import sys
from pathlib import Path

# ─── Make src/ importable ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.claude_adapter import ClaudeAdapter, LoginExpiredException

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)

_BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║          CLAUDE NIGHTCRAWLER — MANUAL LOGIN HELPER               ║
╚══════════════════════════════════════════════════════════════════╝
"""

_INSTRUCTIONS = """
A Chromium browser window will open and navigate to claude.ai.

  Steps to complete:
    1. Log in with your Claude.ai account credentials
    2. Complete any 2FA or email verification if prompted
    3. Confirm you can see the Claude chat interface
    4. Return to this terminal and press Enter

Your session will be saved to browser_data/ and the agent will
reuse it automatically — you should only need to do this once.
"""


def main() -> int:
    print(_BANNER)
    print(_INSTRUCTIONS)
    input("Press Enter to open the browser…  ")

    print()
    adapter = ClaudeAdapter(user_data_dir="browser_data", headless=False)

    try:
        adapter.start()
        logger.info("Browser opened — please log in at https://claude.ai")

        # Navigate directly to Claude
        adapter.page.goto("https://claude.ai")

        print()
        print("👆  Complete the login in the browser window, then come back here.")
        print()
        input("Press Enter AFTER you have successfully logged in…  ")

        # Verify the session is valid
        try:
            adapter.check_login()
            print()
            print("✅  Login verified successfully!")
            print("✅  Session saved to:  browser_data/")
            print("✅  The agent worker will now use this session automatically.")
            print()
            return 0

        except LoginExpiredException:
            print()
            print("❌  Login could not be verified.")
            print("    The browser may not be showing the Claude chat interface.")
            print("    Please try again and ensure you are fully logged in.")
            return 1

    except Exception as exc:
        logger.error("An error occurred: %s", exc)
        print(f"\n❌  Error: {exc}")
        print("    Check the logs or try running the script again.")
        return 1

    finally:
        print("Closing browser…")
        adapter.close()


if __name__ == "__main__":
    sys.exit(main())
