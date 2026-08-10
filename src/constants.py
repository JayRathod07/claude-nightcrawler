"""
Application Constants - Centralized configuration values
All environment-agnostic constants live here.
"""

# ─── Task Status Values ────────────────────────────────────────────────────────
class TaskStatus:
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    WAITING_LIMIT = "waiting_limit"
    FAILED = "failed"

    ALL = [QUEUED, RUNNING, COMPLETED, WAITING_LIMIT, FAILED]


# ─── Execution Log Event Types ─────────────────────────────────────────────────
class EventType:
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRIED = "retried"
    LIMIT_HIT = "limit_hit"

    ALL = [STARTED, COMPLETED, FAILED, RETRIED, LIMIT_HIT]


# ─── Limit Detection Patterns ──────────────────────────────────────────────────
LIMIT_PATTERNS = [
    r"usage limit",
    r"limit reached",
    r"resets? in",
    r"you've used up",
    r"messages remaining.*0",
    r"try again",
    r"rate limit",
]

# ─── Default Values ────────────────────────────────────────────────────────────
DEFAULT_PRIORITY = 0
DEFAULT_MAX_RETRIES = 3
DEFAULT_POLL_INTERVAL_SECONDS = 10
DEFAULT_LIMIT_WAIT_HOURS = 5
DEFAULT_BROWSER_TIMEOUT_MS = 30_000
DEFAULT_TASK_HISTORY_LIMIT = 100

# ─── File Paths ────────────────────────────────────────────────────────────────
RESULTS_DIR = "results"
LOGS_DIR = "logs"
BROWSER_DATA_DIR = "browser_data"
SCREENSHOTS_DIR = "logs/screenshots"

# ─── Log Format ────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
