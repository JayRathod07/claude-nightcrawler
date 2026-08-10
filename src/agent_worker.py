"""
Agent Worker — Infinite task processing loop for Claude Nightcrawler

This is the heart of the system. It runs as a long-lived process (managed by
systemd) and:

    1. Polls SQLite for queued tasks
    2. Sends each prompt to Claude.ai via the ClaudeAdapter
    3. Saves responses as structured Markdown files
    4. Handles usage limits: detects reset time, waits, then retries
    5. Recovers from crashes via stale-task detection on startup
    6. Retries failed tasks with exponential backoff
    7. Sends optional Telegram notifications on key events
    8. Gracefully shuts down on SIGTERM / SIGINT

Process lifecycle:
    systemd starts → init_db() → recover stale tasks → ClaudeAdapter.start()
    → check_login() → infinite loop → SIGTERM → graceful shutdown

Run directly for development:
    python src/agent_worker.py
"""

import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# ── Make project root importable when run directly ─────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.claude_adapter import (
    ClaudeAdapter,
    ClaudeAdapterError,
    LoginExpiredException,
    ResponseExtractionError,
)
from src.database import (
    DatabaseError,
    add_task,
    get_claude_status,
    get_next_task,
    get_statistics,
    increment_requests_today,
    increment_retry_count,
    init_db,
    is_claude_available,
    log_task_event,
    recover_stale_tasks,
    set_claude_available,
    update_task_status,
)
from src.notifier import Notifier

# ─── Environment Configuration ─────────────────────────────────────────────────
POLL_INTERVAL: int = int(os.getenv("TASK_POLL_INTERVAL", "10"))          # seconds
BETWEEN_TASK_WAIT: int = int(os.getenv("TASK_COMPLETION_WAIT", "2"))     # seconds
MAX_RETRIES: int = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))
RETRY_BASE_DELAY: int = int(os.getenv("RETRY_DELAY_SECONDS", "5"))       # backoff base
LIMIT_CHECK_INTERVAL: int = int(os.getenv("LIMIT_CHECK_INTERVAL", "30")) # seconds
BROWSER_DATA_DIR: str = os.getenv("BROWSER_DATA_DIR", "browser_data")
RESULTS_DIR: str = os.getenv("RESULTS_DIR", "results")
LOGS_DIR: str = os.getenv("LOGS_DIR", "logs")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES: int = int(os.getenv("LOG_MAX_SIZE_MB", "5")) * 1024 * 1024
LOG_BACKUP_COUNT: int = int(os.getenv("LOG_BACKUP_COUNT", "3"))

# ─── Logging Setup ─────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    """
    Configure the root logger with:
      • Console handler (stderr) — for journalctl / systemd
      • Rotating file handler — logs/worker.log (5 MB × 3 backups)
    """
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s  %(name)-20s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Console
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    # Rotating file
    log_file = Path(LOGS_DIR) / "worker.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    return logging.getLogger(__name__)


logger = setup_logging()


# ─── Graceful Shutdown ─────────────────────────────────────────────────────────

class _ShutdownFlag:
    """Thread-safe flag set by signal handlers to request a clean stop."""
    def __init__(self) -> None:
        self._stop = False

    def request_stop(self, signum: int, frame: Any) -> None:
        logger.info("Signal %d received — shutting down gracefully…", signum)
        self._stop = True

    @property
    def should_stop(self) -> bool:
        return self._stop


_shutdown = _ShutdownFlag()


# ─── Markdown Result Writer ────────────────────────────────────────────────────

def save_result(task: Dict[str, Any], response_text: str) -> str:
    """
    Write the Claude response to a structured Markdown file.

    File naming: results/task_<id>.md
    The file includes YAML-style frontmatter for machine readability.

    Args:
        task:          Task dict from the database.
        response_text: Raw response text from Claude.

    Returns:
        Relative path to the saved file (stored in the DB).
    """
    Path(RESULTS_DIR).mkdir(parents=True, exist_ok=True)

    task_id = task["id"]
    now = datetime.now()
    filename = f"task_{task_id}.md"
    filepath = Path(RESULTS_DIR) / filename

    prompt_preview = task["prompt"][:200] + ("…" if len(task["prompt"]) > 200 else "")

    content = f"""---
task_id: {task_id}
created_at: {task.get('created_at', 'unknown')}
completed_at: {now.isoformat()}
priority: {task.get('priority', 0)}
retry_count: {task.get('retry_count', 0)}
prompt_length: {len(task['prompt'])}
response_length: {len(response_text)}
---

# Task {task_id} — Claude Response

## Prompt

> {prompt_preview}

---

## Response

{response_text}

---
*Generated by Claude Nightcrawler on {now.strftime("%Y-%m-%d at %H:%M:%S")}*
"""

    filepath.write_text(content, encoding="utf-8")
    logger.info("Result saved → %s (%d chars)", filepath, len(response_text))
    return str(filepath)


# ─── Retry / Backoff ──────────────────────────────────────────────────────────

def backoff_delay(retry_count: int, base: int = RETRY_BASE_DELAY) -> float:
    """
    Calculate exponential backoff with jitter.

    Formula: base * 2^retry_count (capped at 120s)

    Args:
        retry_count: Number of retries already attempted.
        base:        Base delay in seconds.

    Returns:
        Seconds to wait before next retry.
    """
    import random
    delay = min(base * (2 ** retry_count), 120)
    jitter = random.uniform(0, delay * 0.1)  # ±10% jitter
    return delay + jitter


# ─── Limit-Wait Loop ──────────────────────────────────────────────────────────

def wait_for_limit_reset(task_id: int, reset_time: datetime, notifier: Notifier) -> None:
    """
    Sleep in short intervals until the rate-limit reset time passes.

    Logs progress every LIMIT_CHECK_INTERVAL seconds and respects the
    global shutdown flag so the process can exit cleanly mid-wait.

    Args:
        task_id:    ID of the task being held.
        reset_time: When the limit will lift.
        notifier:   Notifier for Telegram alerts.
    """
    minutes_left = (reset_time - datetime.now()).total_seconds() / 60
    logger.info(
        "Task %d waiting for limit reset at %s (%.1f min)",
        task_id,
        reset_time.strftime("%H:%M:%S"),
        minutes_left,
    )
    notifier.notify_limit_hit(task_id, reset_time)

    while datetime.now() < reset_time and not _shutdown.should_stop:
        remaining = (reset_time - datetime.now()).total_seconds()
        if remaining <= 0:
            break
        sleep_secs = min(LIMIT_CHECK_INTERVAL, remaining)
        logger.debug(
            "Limit wait: %.0f seconds remaining (sleeping %ds)",
            remaining,
            sleep_secs,
        )
        time.sleep(sleep_secs)

    if not _shutdown.should_stop:
        logger.info("Limit reset — resuming task %d", task_id)
        set_claude_available(True)


# ─── Single Task Processor ────────────────────────────────────────────────────

def process_task(task: Dict[str, Any], adapter: ClaudeAdapter, notifier: Notifier) -> bool:
    """
    Process one task end-to-end: submit → wait → save → update DB.

    Handles:
        • Successful response → saves MD file, marks 'completed'
        • Rate limit response → marks 'waiting_limit', waits, re-queues
        • Retry-able error  → increments retry count, marks 'queued'
        • Exhausted retries → marks 'failed'
        • Login expired     → re-raises (handled by main loop)

    Args:
        task:    Task dict from the database.
        adapter: Running ClaudeAdapter instance.
        notifier: Notifier for Telegram alerts.

    Returns:
        True if the task was processed successfully (even if it hit a limit).
        False if it failed and should be counted as an error.

    Raises:
        LoginExpiredException: Propagated to the main loop.
    """
    task_id: int = task["id"]
    prompt: str = task["prompt"]
    retry_count: int = task.get("retry_count", 0)
    max_retries: int = task.get("max_retries", MAX_RETRIES)

    logger.info("━━━ Processing task %d (retry %d/%d) ━━━", task_id, retry_count, max_retries)
    logger.debug("Prompt: %s…", prompt[:100])

    # Mark as running
    update_task_status(task_id, "running", started_at=datetime.now())
    log_task_event(task_id, "started", f"Worker started processing (retry {retry_count})")

    try:
        result = adapter.send_prompt(prompt)
        increment_requests_today()

        # ── Success ──────────────────────────────────────────────────────
        if result["status"] == "ok":
            response_text: str = result["text"]
            result_path = save_result(task, response_text)

            update_task_status(
                task_id,
                "completed",
                completed_at=datetime.now(),
                result_path=result_path,
            )
            log_task_event(task_id, "completed", f"Saved to {result_path}")

            elapsed = (datetime.now() - datetime.fromisoformat(
                str(task.get("started_at") or datetime.now().isoformat())
            )).total_seconds()
            logger.info("Task %d completed in %.1fs → %s", task_id, elapsed, result_path)
            notifier.notify_completed(task_id, result_path, response_text[:300])
            return True

        # ── Rate limit ───────────────────────────────────────────────────
        elif result["status"] == "limit":
            reset_time: datetime = result["reset_time"]
            limit_message: str = result["message"]

            set_claude_available(False, reset_time=reset_time, limit_message=limit_message)
            update_task_status(
                task_id,
                "waiting_limit",
                limit_reset_time=reset_time,
            )
            log_task_event(task_id, "limit_hit", f"Reset at {reset_time.isoformat()}")

            wait_for_limit_reset(task_id, reset_time, notifier)

            # Re-queue so it gets picked up in the normal flow
            update_task_status(task_id, "queued")
            return True

        else:
            raise RuntimeError(f"Unknown adapter status: {result['status']}")

    except LoginExpiredException:
        logger.error("Session expired — cannot process task %d", task_id)
        update_task_status(task_id, "queued")  # will retry after re-login
        raise  # propagate to main loop

    except (ResponseExtractionError, RuntimeError, ClaudeAdapterError) as exc:
        logger.warning("Task %d failed: %s", task_id, exc)

        new_retry = increment_retry_count(task_id)

        if new_retry > max_retries:
            err_msg = f"Exhausted {max_retries} retries: {exc}"
            update_task_status(task_id, "failed", error_message=err_msg,
                               completed_at=datetime.now())
            log_task_event(task_id, "failed", err_msg)
            logger.error("Task %d permanently failed after %d retries", task_id, max_retries)
            notifier.notify_error(task_id, err_msg)
            return False

        # Exponential backoff before re-queue
        delay = backoff_delay(new_retry - 1)
        logger.info("Retrying task %d in %.1fs (attempt %d/%d)…",
                    task_id, delay, new_retry, max_retries)
        log_task_event(task_id, "retried",
                       f"Attempt {new_retry}/{max_retries}, delay={delay:.1f}s")
        update_task_status(task_id, "queued", error_message=str(exc))

        # Wait with shutdown awareness
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline and not _shutdown.should_stop:
            time.sleep(min(1.0, deadline - time.monotonic()))

        return False

    except Exception as exc:
        logger.exception("Unexpected error processing task %d", task_id)
        update_task_status(task_id, "failed", error_message=str(exc),
                           completed_at=datetime.now())
        log_task_event(task_id, "failed", f"Unexpected: {exc}")
        notifier.notify_error(task_id, str(exc))
        return False


# ─── Main Loop ────────────────────────────────────────────────────────────────

def run_worker() -> None:
    """
    Main worker entry point. Runs until SIGTERM/SIGINT or a fatal error.

    Startup sequence:
        1. Register signal handlers
        2. Initialise database (creates tables, recovers stale tasks)
        3. Start ClaudeAdapter (Chromium browser)
        4. Verify Claude.ai login
        5. Enter polling loop

    Polling loop:
        • Fetch next task from DB
        • If no task: sleep POLL_INTERVAL seconds
        • If Claude unavailable: sleep LIMIT_CHECK_INTERVAL seconds
        • Otherwise: call process_task()
        • Sleep BETWEEN_TASK_WAIT seconds after each task
    """
    # ── Signal handlers ────────────────────────────────────────────────────
    signal.signal(signal.SIGTERM, _shutdown.request_stop)
    signal.signal(signal.SIGINT, _shutdown.request_stop)

    logger.info("=" * 60)
    logger.info("Claude Nightcrawler Worker — starting up")
    logger.info("=" * 60)

    # ── Database ───────────────────────────────────────────────────────────
    try:
        init_db()
        stale = recover_stale_tasks()
        if stale:
            logger.warning("Recovered %d stale tasks from previous crash", stale)
    except Exception as exc:
        logger.critical("Database initialisation failed: %s", exc)
        sys.exit(1)

    notifier = Notifier()

    # ── Browser ────────────────────────────────────────────────────────────
    adapter = ClaudeAdapter(user_data_dir=BROWSER_DATA_DIR)
    try:
        adapter.start()
        logger.info("Browser started")
    except Exception as exc:
        logger.critical("Failed to start browser: %s", exc)
        sys.exit(1)

    # ── Login check ────────────────────────────────────────────────────────
    try:
        adapter.check_login()
        logger.info("Claude.ai session verified ✓")
    except LoginExpiredException:
        logger.critical(
            "Not logged in to Claude.ai. "
            "Run: python scripts/manual_login.py"
        )
        adapter.close()
        sys.exit(1)
    except Exception as exc:
        logger.warning("Login check inconclusive: %s — proceeding anyway", exc)

    logger.info("Worker ready — polling every %ds", POLL_INTERVAL)
    notifier.notify_startup()

    # ── Main polling loop ──────────────────────────────────────────────────
    consecutive_errors: int = 0
    MAX_CONSECUTIVE_ERRORS: int = 5

    try:
        while not _shutdown.should_stop:
            try:
                # ── Check Claude availability ──────────────────────────
                if not is_claude_available():
                    status = get_claude_status()
                    reset_str = status.get("reset_time", "unknown")
                    logger.debug("Claude unavailable — waiting for reset (%s)", reset_str)
                    _interruptible_sleep(LIMIT_CHECK_INTERVAL)
                    continue

                # ── Fetch next task ────────────────────────────────────
                task = get_next_task()

                if task is None:
                    logger.debug("Queue empty — sleeping %ds", POLL_INTERVAL)
                    _interruptible_sleep(POLL_INTERVAL)
                    continue

                # ── Process task ───────────────────────────────────────
                success = process_task(task, adapter, notifier)
                consecutive_errors = 0 if success else consecutive_errors + 1

                # ── Circuit breaker ────────────────────────────────────
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        "%d consecutive failures — pausing for 60s",
                        consecutive_errors,
                    )
                    notifier.notify_error(
                        0,
                        f"{consecutive_errors} consecutive task failures — pausing",
                    )
                    _interruptible_sleep(60)
                    consecutive_errors = 0

                # ── Cool-down between tasks ────────────────────────────
                if not _shutdown.should_stop:
                    _interruptible_sleep(BETWEEN_TASK_WAIT)

            except LoginExpiredException:
                logger.critical(
                    "Session expired during task processing. "
                    "Stopping worker — run manual_login.py to re-authenticate."
                )
                notifier.notify_error(0, "Session expired — manual re-login required")
                break

            except DatabaseError as exc:
                logger.error("Database error in main loop: %s", exc)
                consecutive_errors += 1
                _interruptible_sleep(POLL_INTERVAL)

            except Exception as exc:
                logger.exception("Unhandled error in main loop: %s", exc)
                consecutive_errors += 1
                _interruptible_sleep(POLL_INTERVAL)

    finally:
        logger.info("Shutting down — closing browser…")
        adapter.close()
        logger.info("Worker stopped.")


def _interruptible_sleep(seconds: float) -> None:
    """
    Sleep for `seconds` but wake up immediately if shutdown is requested.
    Checks the flag every 1 second.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not _shutdown.should_stop:
        time.sleep(min(1.0, deadline - time.monotonic()))


# ─── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_worker()
