"""
Tests for src/database.py — Phase 1 Database Layer

Covers:
    • Database initialisation (schema creation, idempotency)
    • Task CRUD operations
    • Status updates and constraint validation
    • Claude status management
    • Crash recovery (stale task reset)
    • Statistics aggregation
    • Audit log entries
    • Delete operations
    • Edge cases and error handling
"""
import os
import sys
import pytest
from datetime import datetime, timedelta
from pathlib import Path

# ── Make sure the src package is importable from the repo root ─────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.database as db


# ─── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """
    Each test gets its own fresh SQLite database in a temp directory.
    We monkeypatch DB_PATH so the module uses the temp file.
    """
    test_db_path = str(tmp_path / "test_agent.db")
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_db()
    yield test_db_path
    # tmp_path is cleaned up automatically by pytest


# ─── Initialisation Tests ──────────────────────────────────────────────────────

class TestInitDB:
    def test_creates_tables(self, isolated_db):
        """All three tables must exist after init_db."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row[0] for row in cursor.fetchall()}
        assert "tasks" in tables
        assert "claude_status" in tables
        assert "execution_log" in tables

    def test_idempotent(self, isolated_db):
        """Calling init_db() twice should not raise or corrupt state."""
        db.init_db()  # second call
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM claude_status")
            count = cursor.fetchone()[0]
        assert count == 1

    def test_claude_status_singleton_seeded(self, isolated_db):
        """claude_status row with id=1 must exist after init."""
        status = db.get_claude_status()
        assert status["id"] == 1
        assert bool(status["available"]) is True


# ─── add_task Tests ────────────────────────────────────────────────────────────

class TestAddTask:
    def test_returns_positive_id(self, isolated_db):
        task_id = db.add_task("Write a haiku about Python")
        assert isinstance(task_id, int)
        assert task_id > 0

    def test_task_persisted_with_correct_defaults(self, isolated_db):
        task_id = db.add_task("Explain async/await")
        task = db.get_task_by_id(task_id)
        assert task is not None
        assert task["prompt"] == "Explain async/await"
        assert task["status"] == "queued"
        assert task["priority"] == 0
        assert task["retry_count"] == 0
        assert task["max_retries"] == 3

    def test_priority_stored_correctly(self, isolated_db):
        task_id = db.add_task("Urgent task", priority=7)
        task = db.get_task_by_id(task_id)
        assert task["priority"] == 7

    def test_metadata_serialised_as_json(self, isolated_db):
        meta = {"source": "test", "tags": ["python", "db"]}
        task_id = db.add_task("Meta task", metadata=meta)
        task = db.get_task_by_id(task_id)
        import json
        assert json.loads(task["metadata"]) == meta

    def test_empty_prompt_raises_value_error(self, isolated_db):
        with pytest.raises(ValueError):
            db.add_task("")

    def test_blank_prompt_raises_value_error(self, isolated_db):
        with pytest.raises(ValueError):
            db.add_task("   ")

    def test_prompt_stripped_of_whitespace(self, isolated_db):
        task_id = db.add_task("  Hello World  ")
        task = db.get_task_by_id(task_id)
        assert task["prompt"] == "Hello World"


# ─── get_next_task Tests ───────────────────────────────────────────────────────

class TestGetNextTask:
    def test_returns_none_when_queue_empty(self, isolated_db):
        assert db.get_next_task() is None

    def test_returns_oldest_first_on_equal_priority(self, isolated_db):
        id1 = db.add_task("First")
        id2 = db.add_task("Second")
        next_task = db.get_next_task()
        assert next_task["id"] == id1

    def test_respects_priority_ordering(self, isolated_db):
        low_id = db.add_task("Low priority", priority=0)
        high_id = db.add_task("High priority", priority=5)
        next_task = db.get_next_task()
        assert next_task["id"] == high_id

    def test_waiting_limit_task_returned_after_reset(self, isolated_db):
        task_id = db.add_task("Delayed task")
        past_time = datetime.now() - timedelta(seconds=1)
        db.update_task_status(
            task_id,
            "waiting_limit",
            limit_reset_time=past_time,
        )
        next_task = db.get_next_task()
        assert next_task is not None
        assert next_task["id"] == task_id

    def test_waiting_limit_task_not_returned_before_reset(self, isolated_db):
        task_id = db.add_task("Future task")
        future_time = datetime.now() + timedelta(hours=5)
        db.update_task_status(
            task_id,
            "waiting_limit",
            limit_reset_time=future_time,
        )
        assert db.get_next_task() is None


# ─── update_task_status Tests ──────────────────────────────────────────────────

class TestUpdateTaskStatus:
    def test_status_updated_to_running(self, isolated_db):
        task_id = db.add_task("Run me")
        db.update_task_status(task_id, "running", started_at=datetime.now())
        task = db.get_task_by_id(task_id)
        assert task["status"] == "running"
        assert task["started_at"] is not None

    def test_status_updated_to_completed(self, isolated_db):
        task_id = db.add_task("Complete me")
        db.update_task_status(
            task_id,
            "completed",
            completed_at=datetime.now(),
            result_path="results/task_1.md",
        )
        task = db.get_task_by_id(task_id)
        assert task["status"] == "completed"
        assert task["result_path"] == "results/task_1.md"

    def test_status_updated_to_failed_with_message(self, isolated_db):
        task_id = db.add_task("Fail me")
        db.update_task_status(task_id, "failed", error_message="Timeout")
        task = db.get_task_by_id(task_id)
        assert task["status"] == "failed"
        assert task["error_message"] == "Timeout"

    def test_invalid_status_raises_value_error(self, isolated_db):
        task_id = db.add_task("Bad status")
        with pytest.raises(ValueError):
            db.update_task_status(task_id, "invalid_status")

    def test_nonexistent_task_raises_not_found(self, isolated_db):
        with pytest.raises(db.TaskNotFoundError):
            db.update_task_status(9999, "completed")

    def test_all_valid_statuses_accepted(self, isolated_db):
        for status in ["queued", "running", "completed", "waiting_limit", "failed"]:
            task_id = db.add_task(f"Task for status={status}")
            db.update_task_status(task_id, status)
            task = db.get_task_by_id(task_id)
            assert task["status"] == status


# ─── increment_retry_count Tests ───────────────────────────────────────────────

class TestIncrementRetryCount:
    def test_increments_from_zero(self, isolated_db):
        task_id = db.add_task("Retry me")
        new_count = db.increment_retry_count(task_id)
        assert new_count == 1

    def test_increments_multiple_times(self, isolated_db):
        task_id = db.add_task("Retry again")
        db.increment_retry_count(task_id)
        db.increment_retry_count(task_id)
        count = db.increment_retry_count(task_id)
        assert count == 3

    def test_nonexistent_task_raises_not_found(self, isolated_db):
        with pytest.raises(db.TaskNotFoundError):
            db.increment_retry_count(9999)


# ─── Claude Status Tests ───────────────────────────────────────────────────────

class TestClaudeStatus:
    def test_initially_available(self, isolated_db):
        assert db.is_claude_available() is True

    def test_set_unavailable(self, isolated_db):
        future = datetime.now() + timedelta(hours=5)
        db.set_claude_available(False, reset_time=future)
        assert db.is_claude_available() is False

    def test_auto_resets_after_reset_time(self, isolated_db):
        past = datetime.now() - timedelta(seconds=1)
        db.set_claude_available(False, reset_time=past)
        # is_claude_available should detect expired reset and mark available
        assert db.is_claude_available() is True

    def test_limit_message_stored(self, isolated_db):
        db.set_claude_available(False, limit_message="You've reached the usage limit.")
        status = db.get_claude_status()
        assert status["last_limit_message"] == "You've reached the usage limit."

    def test_set_available_clears_reset_time(self, isolated_db):
        future = datetime.now() + timedelta(hours=1)
        db.set_claude_available(False, reset_time=future)
        db.set_claude_available(True)
        assert db.is_claude_available() is True


# ─── Crash Recovery Tests ──────────────────────────────────────────────────────

class TestRecoverStaleTasks:
    def test_running_tasks_reset_to_queued(self, isolated_db):
        task_id = db.add_task("Stale task")
        db.update_task_status(task_id, "running")
        recovered = db.recover_stale_tasks()
        assert recovered == 1
        task = db.get_task_by_id(task_id)
        assert task["status"] == "queued"

    def test_completed_tasks_not_affected(self, isolated_db):
        task_id = db.add_task("Done task")
        db.update_task_status(task_id, "completed")
        recovered = db.recover_stale_tasks()
        assert recovered == 0
        task = db.get_task_by_id(task_id)
        assert task["status"] == "completed"

    def test_retry_count_incremented_on_recovery(self, isolated_db):
        task_id = db.add_task("Recover me")
        db.update_task_status(task_id, "running")
        db.recover_stale_tasks()
        task = db.get_task_by_id(task_id)
        assert task["retry_count"] == 1


# ─── get_all_tasks Tests ───────────────────────────────────────────────────────

class TestGetAllTasks:
    def test_returns_all_tasks(self, isolated_db):
        db.add_task("Task A")
        db.add_task("Task B")
        db.add_task("Task C")
        tasks = db.get_all_tasks()
        assert len(tasks) == 3

    def test_ordered_newest_first(self, isolated_db):
        id1 = db.add_task("First")
        id2 = db.add_task("Second")
        tasks = db.get_all_tasks()
        assert tasks[0]["id"] == id2  # newest first

    def test_limit_applied(self, isolated_db):
        for i in range(5):
            db.add_task(f"Task {i}")
        tasks = db.get_all_tasks(limit=3)
        assert len(tasks) == 3

    def test_offset_applied(self, isolated_db):
        for i in range(5):
            db.add_task(f"Task {i}")
        tasks_page1 = db.get_all_tasks(limit=2, offset=0)
        tasks_page2 = db.get_all_tasks(limit=2, offset=2)
        ids_page1 = {t["id"] for t in tasks_page1}
        ids_page2 = {t["id"] for t in tasks_page2}
        assert ids_page1.isdisjoint(ids_page2)


# ─── delete_task Tests ─────────────────────────────────────────────────────────

class TestDeleteTask:
    def test_delete_existing_task(self, isolated_db):
        task_id = db.add_task("Delete me")
        result = db.delete_task(task_id)
        assert result is True
        assert db.get_task_by_id(task_id) is None

    def test_delete_nonexistent_task_returns_false(self, isolated_db):
        result = db.delete_task(9999)
        assert result is False


# ─── get_statistics Tests ──────────────────────────────────────────────────────

class TestGetStatistics:
    def test_returns_dict(self, isolated_db):
        stats = db.get_statistics()
        assert isinstance(stats, dict)

    def test_total_tasks_count(self, isolated_db):
        db.add_task("One")
        db.add_task("Two")
        stats = db.get_statistics()
        assert stats["total_tasks"] == 2

    def test_task_counts_by_status(self, isolated_db):
        id1 = db.add_task("Q1")
        id2 = db.add_task("Q2")
        db.update_task_status(id2, "completed")
        stats = db.get_statistics()
        assert stats["task_counts"].get("queued", 0) == 1
        assert stats["task_counts"].get("completed", 0) == 1

    def test_avg_completion_zero_with_no_completed_tasks(self, isolated_db):
        db.add_task("Incomplete")
        stats = db.get_statistics()
        assert stats["avg_completion_minutes"] == 0.0


# ─── log_task_event Tests ──────────────────────────────────────────────────────

class TestLogTaskEvent:
    def test_event_logged_for_valid_type(self, isolated_db):
        task_id = db.add_task("Audit me")
        db.log_task_event(task_id, "started", "Worker picked up task")
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM execution_log WHERE task_id = ?", (task_id,)
            )
            rows = cursor.fetchall()
        assert len(rows) >= 1

    def test_invalid_event_type_silently_skipped(self, isolated_db):
        task_id = db.add_task("No-log task")
        # Should not raise
        db.log_task_event(task_id, "nonexistent_event", "details")


# ─── increment_requests_today Tests ───────────────────────────────────────────

class TestIncrementRequestsToday:
    def test_increments_counter(self, isolated_db):
        db.increment_requests_today()
        db.increment_requests_today()
        status = db.get_claude_status()
        assert status["total_requests_today"] >= 2
