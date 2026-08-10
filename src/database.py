"""
Database Layer — SQLite operations with thread safety and connection pooling.

This module provides the complete data access layer for Claude Nightcrawler.
All database operations are ACID-compliant using SQLite WAL mode.

Tables:
    tasks          — User-submitted prompts and their execution state
    claude_status  — Singleton row tracking Claude.ai availability
    execution_log  — Full audit trail of task lifecycle events
"""

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

# ─── Logging ───────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ─── Database Configuration ────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "agent.db")
DB_TIMEOUT: float = 30.0  # seconds before a locked DB raises an error

# ─── Thread-local storage (one connection per thread) ─────────────────────────
_thread_local = threading.local()


# ─── Custom Exceptions ─────────────────────────────────────────────────────────

class DatabaseError(Exception):
    """Base exception for all database operations."""


class DatabaseConnectionError(DatabaseError):
    """Raised when the database cannot be opened or initialised."""


class TaskNotFoundError(DatabaseError):
    """Raised when a task ID does not exist in the database."""


# ─── Connection Manager ────────────────────────────────────────────────────────

@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager that yields a configured SQLite connection.

    Features:
        • Row factory — columns accessible by name (dict-style)
        • WAL journal mode — safe concurrent readers + one writer
        • Foreign key enforcement
        • Automatic commit on success / rollback on exception
        • Connection always closed in finally block

    Yields:
        sqlite3.Connection: Ready-to-use database connection

    Raises:
        DatabaseError: On any SQLite or OS-level error
    """
    conn = sqlite3.connect(
        DB_PATH,
        timeout=DB_TIMEOUT,
        check_same_thread=False,
        isolation_level="IMMEDIATE",
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    try:
        yield conn
        conn.commit()
    except (TaskNotFoundError, ValueError) as exc:
        conn.rollback()
        raise  # pass through without wrapping
    except Exception as exc:
        conn.rollback()
        logger.error("Database error — rolling back: %s", exc)
        raise DatabaseError(f"Database operation failed: {exc}") from exc
    finally:
        conn.close()


# ─── Schema Initialisation ─────────────────────────────────────────────────────

def init_db() -> None:
    """
    Create all tables and indexes if they do not already exist.

    This function is idempotent — safe to call on every application start.
    After creating the schema it also runs crash-recovery logic to reset
    any tasks that were left in 'running' state by a previous crash.

    Raises:
        DatabaseConnectionError: If the schema cannot be created.
    """
    logger.info("Initialising database at '%s'…", DB_PATH)

    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            # ── tasks table ────────────────────────────────────────────────────
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt           TEXT    NOT NULL,
                    status           TEXT    NOT NULL DEFAULT 'queued'
                                         CHECK(status IN (
                                             'queued', 'running', 'completed',
                                             'waiting_limit', 'failed'
                                         )),
                    priority         INTEGER DEFAULT 0,
                    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at       TIMESTAMP,
                    completed_at     TIMESTAMP,
                    result_path      TEXT,
                    error_message    TEXT,
                    retry_count      INTEGER DEFAULT 0,
                    max_retries      INTEGER DEFAULT 3,
                    limit_reset_time TIMESTAMP,
                    metadata         TEXT,
                    created_by       TEXT DEFAULT 'admin'
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_priority   ON tasks(priority DESC)"
            )

            # ── claude_status table (singleton — always id = 1) ────────────────
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS claude_status (
                    id                    INTEGER PRIMARY KEY CHECK (id = 1),
                    available             BOOLEAN NOT NULL DEFAULT 1,
                    reset_time            TIMESTAMP,
                    last_check            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_limit_message    TEXT,
                    total_requests_today  INTEGER DEFAULT 0,
                    last_reset_date       DATE    DEFAULT CURRENT_DATE
                )
                """
            )
            cursor.execute(
                "INSERT OR IGNORE INTO claude_status (id, available) VALUES (1, 1)"
            )

            # ── execution_log table (audit trail) ──────────────────────────────
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id     INTEGER NOT NULL,
                    event_type  TEXT    NOT NULL
                                    CHECK(event_type IN (
                                        'started', 'completed', 'failed',
                                        'retried', 'limit_hit'
                                    )),
                    event_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    details     TEXT,
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_log_task_id    ON execution_log(task_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_log_event_time ON execution_log(event_time)"
            )

            conn.commit()
            logger.info("Database schema ready ✓")

        # Recover tasks interrupted by a previous crash
        recover_stale_tasks()

    except Exception as exc:
        logger.critical("Failed to initialise database: %s", exc)
        raise DatabaseConnectionError(
            f"Database initialisation failed: {exc}"
        ) from exc


# ─── Task CRUD ─────────────────────────────────────────────────────────────────

def add_task(
    prompt: str,
    priority: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Insert a new task into the queue.

    Args:
        prompt:   User prompt text (must be non-empty).
        priority: Scheduling priority — higher value = processed sooner.
        metadata: Arbitrary JSON-serialisable dict stored alongside the task.

    Returns:
        Integer ID of the newly created task row.

    Raises:
        ValueError:     If prompt is empty or blank.
        DatabaseError:  On any DB-level failure.
    """
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty")

    meta_json: Optional[str] = json.dumps(metadata) if metadata else None

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tasks (prompt, priority, metadata) VALUES (?, ?, ?)",
                (prompt.strip(), priority, meta_json),
            )
            task_id: int = cursor.lastrowid  # type: ignore[assignment]
            logger.info("Task %d created (priority=%d)", task_id, priority)
            return task_id

    except DatabaseError:
        raise
    except Exception as exc:
        logger.error("Failed to add task: %s", exc)
        raise DatabaseError(f"Failed to add task: {exc}") from exc


def get_next_task() -> Optional[Dict[str, Any]]:
    """
    Fetch the next task to be processed.

    Selection logic (in order):
        1. Tasks in 'waiting_limit' whose reset time has passed.
        2. Tasks in 'queued' state.

    Both groups are sorted: highest priority first, then oldest first (FIFO).

    Returns:
        Task dict, or None if the queue is empty.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            # Check for waiting_limit tasks whose timer expired
            # Compare as text — SQLite stores ISO 8601 strings and lexicographic
            # comparison works correctly for datetime strings in this format.
            now_iso = datetime.now().isoformat()
            cursor.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'waiting_limit'
                  AND limit_reset_time IS NOT NULL
                  AND limit_reset_time <= ?
                ORDER BY priority DESC, id ASC
                LIMIT 1
                """,
                (now_iso,),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

            # Normal queued tasks
            cursor.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'queued'
                ORDER BY priority DESC, id ASC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    except Exception as exc:
        logger.error("Failed to get next task: %s", exc)
        raise DatabaseError(f"Failed to get next task: {exc}") from exc


def update_task_status(
    task_id: int,
    status: str,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    result_path: Optional[str] = None,
    error_message: Optional[str] = None,
    limit_reset_time: Optional[datetime] = None,
) -> None:
    """
    Update a task's status and any accompanying timestamp/path fields.

    Builds a dynamic UPDATE statement so only provided fields are modified.
    Also appends an entry to the execution_log audit table.

    Args:
        task_id:          Row to update.
        status:           New status string (must be a valid status value).
        started_at:       Set when the worker begins processing.
        completed_at:     Set when the task finishes (success or failure).
        result_path:      Relative path to the saved Markdown response file.
        error_message:    Human-readable error if status='failed'.
        limit_reset_time: Datetime after which the task can be retried.

    Raises:
        ValueError:       If status is not a recognised value.
        TaskNotFoundError: If no row with task_id exists.
        DatabaseError:    On other DB failures.
    """
    valid_statuses = ["queued", "running", "completed", "waiting_limit", "failed"]
    if status not in valid_statuses:
        raise ValueError(f"Invalid status '{status}'. Must be one of {valid_statuses}")

    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            fields: List[str] = ["status = ?"]
            values: List[Any] = [status]

            if started_at is not None:
                fields.append("started_at = ?")
                values.append(started_at.isoformat())

            if completed_at is not None:
                fields.append("completed_at = ?")
                values.append(completed_at.isoformat())

            if result_path is not None:
                fields.append("result_path = ?")
                values.append(result_path)

            if error_message is not None:
                fields.append("error_message = ?")
                values.append(error_message)

            if limit_reset_time is not None:
                fields.append("limit_reset_time = ?")
                values.append(limit_reset_time.isoformat())

            values.append(task_id)

            cursor.execute(
                f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?",
                values,
            )

            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"Task {task_id} not found")

            logger.info("Task %d → '%s'", task_id, status)

        # Log event (separate connection to avoid nested transaction)
        _log_event_safe(task_id, status, error_message)

    except (TaskNotFoundError, ValueError):
        raise
    except DatabaseError:
        raise
    except Exception as exc:
        logger.error("Failed to update task %d: %s", task_id, exc)
        raise DatabaseError(f"Failed to update task: {exc}") from exc


def increment_retry_count(task_id: int) -> int:
    """
    Atomically increment the retry_count for a task.

    Returns:
        The new retry count after incrementing.

    Raises:
        TaskNotFoundError: If task_id does not exist.
        DatabaseError:     On other DB failures.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tasks SET retry_count = retry_count + 1 WHERE id = ?",
                (task_id,),
            )
            cursor.execute("SELECT retry_count FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

            if not row:
                raise TaskNotFoundError(f"Task {task_id} not found")

            new_count: int = row[0]
            logger.info("Task %d retry count → %d", task_id, new_count)
            return new_count

    except TaskNotFoundError:
        raise
    except Exception as exc:
        logger.error("Failed to increment retry count for task %d: %s", task_id, exc)
        raise DatabaseError(f"Failed to increment retry count: {exc}") from exc


def get_task_by_id(task_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetch a single task row by primary key.

    Returns:
        Task dict, or None if not found.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("Failed to get task %d: %s", task_id, exc)
        return None


def get_all_tasks(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Return tasks ordered by newest first, with pagination.

    Args:
        limit:  Maximum rows to return.
        offset: Rows to skip (for pagination).

    Returns:
        List of task dicts.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]
    except Exception as exc:
        logger.error("Failed to get all tasks: %s", exc)
        raise DatabaseError(f"Failed to get all tasks: {exc}") from exc


def delete_task(task_id: int) -> bool:
    """
    Delete a task and its execution log entries.

    Returns:
        True if a row was deleted, False if the task was not found.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            # Cascade handled manually (no ON DELETE CASCADE in all SQLite builds)
            cursor.execute("DELETE FROM execution_log WHERE task_id = ?", (task_id,))
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info("Task %d deleted", task_id)
            return deleted
    except Exception as exc:
        logger.error("Failed to delete task %d: %s", task_id, exc)
        raise DatabaseError(f"Failed to delete task: {exc}") from exc


# ─── Claude Status ─────────────────────────────────────────────────────────────

def set_claude_available(
    available: bool,
    reset_time: Optional[datetime] = None,
    limit_message: Optional[str] = None,
) -> None:
    """
    Update the singleton claude_status row.

    Args:
        available:      True means Claude can accept new prompts.
        reset_time:     When the usage limit will lift (if available=False).
        limit_message:  Raw limit message text from the Claude UI.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE claude_status
                SET available          = ?,
                    reset_time         = ?,
                    last_check         = CURRENT_TIMESTAMP,
                    last_limit_message = ?
                WHERE id = 1
                """,
                (
                    int(available),
                    reset_time.isoformat() if reset_time else None,
                    limit_message,
                ),
            )
            status_str = "available" if available else "rate-limited"
            logger.info("Claude status → %s", status_str)
            if reset_time:
                logger.info("Limit resets at %s", reset_time)

    except Exception as exc:
        logger.error("Failed to update Claude status: %s", exc)
        raise DatabaseError(f"Failed to update Claude status: {exc}") from exc


def is_claude_available() -> bool:
    """
    Check if Claude is currently able to accept requests.

    If the stored reset_time has passed, this function automatically
    marks Claude as available again (self-healing).

    Returns:
        True if Claude is available.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT available, reset_time FROM claude_status WHERE id = 1"
            )
            row = cursor.fetchone()
            if not row:
                return True  # default to available if no row exists

            available: bool = bool(row["available"])
            reset_time_str: Optional[str] = row["reset_time"]

        if not available and reset_time_str:
            reset_dt = datetime.fromisoformat(reset_time_str)
            if datetime.now() >= reset_dt:
                set_claude_available(True)
                logger.info("Usage limit expired — Claude is now available")
                return True
            remaining = reset_dt - datetime.now()
            logger.debug("Claude rate-limited — %s remaining", remaining)
            return False

        return available

    except Exception as exc:
        logger.error("Failed to check Claude availability: %s", exc)
        return False  # fail-safe: treat as unavailable


def get_claude_status() -> Dict[str, Any]:
    """
    Return the full claude_status row as a dict.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM claude_status WHERE id = 1")
            row = cursor.fetchone()
            return dict(row) if row else {}
    except Exception as exc:
        logger.error("Failed to get Claude status: %s", exc)
        return {}


def increment_requests_today() -> None:
    """Increment the daily request counter in claude_status."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            # Reset counter if date changed
            cursor.execute(
                """
                UPDATE claude_status
                SET total_requests_today = CASE
                    WHEN last_reset_date < CURRENT_DATE
                    THEN 1
                    ELSE total_requests_today + 1
                END,
                last_reset_date = CURRENT_DATE
                WHERE id = 1
                """
            )
    except Exception as exc:
        logger.error("Failed to increment requests: %s", exc)


# ─── Crash Recovery ────────────────────────────────────────────────────────────

def recover_stale_tasks() -> int:
    """
    Reset 'running' tasks back to 'queued' so they are retried.

    Called on every worker startup. If the previous worker crashed mid-task,
    those tasks would be stuck in 'running' forever without this.

    Returns:
        Number of tasks recovered.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tasks
                SET status      = 'queued',
                    started_at  = NULL,
                    retry_count = retry_count + 1
                WHERE status = 'running'
                """
            )
            count = cursor.rowcount
            if count > 0:
                logger.warning("Recovered %d stale task(s) from previous crash", count)
            return count
    except Exception as exc:
        logger.error("Failed to recover stale tasks: %s", exc)
        return 0


# ─── Audit Logging ─────────────────────────────────────────────────────────────

def log_task_event(
    task_id: int,
    event_type: str,
    details: Optional[str] = None,
) -> None:
    """
    Append an event to the execution_log audit trail.

    Args:
        task_id:    ID of the task being logged.
        event_type: One of: started, completed, failed, retried, limit_hit.
        details:    Optional human-readable description.
    """
    valid_events = ["started", "completed", "failed", "retried", "limit_hit"]
    if event_type not in valid_events:
        logger.warning("Unknown event type '%s' — skipping log", event_type)
        return

    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO execution_log (task_id, event_type, details) VALUES (?, ?, ?)",
                (task_id, event_type, details),
            )
    except Exception as exc:
        logger.error("Failed to log event '%s' for task %d: %s", event_type, task_id, exc)


def _log_event_safe(task_id: int, status: str, details: Optional[str]) -> None:
    """Internal helper — maps status strings to event_type strings."""
    status_to_event = {
        "running": "started",
        "completed": "completed",
        "failed": "failed",
        "waiting_limit": "limit_hit",
        "queued": "retried",
    }
    event = status_to_event.get(status)
    if event:
        log_task_event(task_id, event, details)


# ─── Statistics ────────────────────────────────────────────────────────────────

def get_statistics() -> Dict[str, Any]:
    """
    Aggregate system statistics for the monitoring dashboard.

    Returns:
        Dict containing task counts, request totals, and performance metrics.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            stats: Dict[str, Any] = {}

            # Task counts per status
            cursor.execute(
                "SELECT status, COUNT(*) as count FROM tasks GROUP BY status"
            )
            stats["task_counts"] = {row["status"]: row["count"] for row in cursor.fetchall()}

            # Grand total
            cursor.execute("SELECT COUNT(*) FROM tasks")
            stats["total_tasks"] = cursor.fetchone()[0]

            # Today's request count
            cursor.execute(
                "SELECT total_requests_today FROM claude_status WHERE id = 1"
            )
            row = cursor.fetchone()
            stats["requests_today"] = row[0] if row else 0

            # Average completion time in minutes
            cursor.execute(
                """
                SELECT AVG(
                    (JULIANDAY(completed_at) - JULIANDAY(started_at)) * 24 * 60
                ) AS avg_minutes
                FROM tasks
                WHERE status = 'completed'
                  AND started_at  IS NOT NULL
                  AND completed_at IS NOT NULL
                """
            )
            row = cursor.fetchone()
            stats["avg_completion_minutes"] = round(row[0], 2) if row and row[0] else 0.0

            return stats

    except Exception as exc:
        logger.error("Failed to get statistics: %s", exc)
        return {}


# ─── Module Self-test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("Running database self-test…")
    init_db()

    task_id = add_task("Hello from self-test!", priority=1)
    print(f"  Created task: id={task_id}")

    task = get_next_task()
    assert task is not None, "Expected a queued task"
    print(f"  Fetched next task: {task['id']} — {task['status']}")

    update_task_status(task_id, "running", started_at=datetime.now())
    update_task_status(task_id, "completed", completed_at=datetime.now())

    stats = get_statistics()
    print(f"  Statistics: {stats}")

    print("Self-test passed ✓")
