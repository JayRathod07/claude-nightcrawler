"""
Integration Tests — End-to-end workflow for Claude Nightcrawler

Tests the complete pipeline from task submission → worker processing →
result generation, using mocked Claude adapter so no real browser is needed.

Coverage:
    • Full task lifecycle: queued → running → completed
    • Rate-limit handling: queued → running → waiting_limit → reset → completed
    • Retry & failure: error → retry increment → queued → max retries → failed
    • Login-expired propagation
    • Dashboard API ↔ database consistency
    • Multi-task ordering (priority + FIFO)
    • Result file creation and content integrity
    • Concurrent read/write (worker writes, dashboard reads simultaneously)
    • Morning report generation against real DB state
    • Health check against a live database
"""

import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

# ── Project root on path ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def tmp_db(tmp_path):
    """Provide a fresh isolated SQLite database for each test."""
    db_file = str(tmp_path / "test_integration.db")
    with patch.dict(os.environ, {"DB_PATH": db_file}):
        import importlib
        import src.database as db_mod
        importlib.reload(db_mod)
        db_mod.init_db()
        yield db_mod
    # Cleanup handled by tmp_path fixture


@pytest.fixture()
def results_dir(tmp_path):
    """Provide a writable results directory."""
    d = tmp_path / "results"
    d.mkdir()
    return str(d)


def _make_mock_adapter(response: Dict[str, Any]):
    """Return a mock ClaudeAdapter that always returns `response`."""
    adapter = MagicMock()
    adapter.send_prompt.return_value = response
    adapter.login_if_needed.return_value = None
    adapter.start.return_value = None
    adapter.close.return_value = None
    return adapter


# ══════════════════════════════════════════════════════════════════════════════
# 7.2.1  Full Task Lifecycle — queued → running → completed
# ══════════════════════════════════════════════════════════════════════════════

class TestFullTaskLifecycle:

    def test_task_submitted_has_queued_status(self, tmp_db):
        task_id = tmp_db.add_task("Write a poem about the moon")
        tasks = tmp_db.get_all_tasks()
        assert len(tasks) == 1
        assert tasks[0]["status"] == "queued"
        assert tasks[0]["id"] == task_id

    def test_task_transitions_to_running(self, tmp_db):
        task_id = tmp_db.add_task("Hello Claude")
        task = tmp_db.get_next_task()
        assert task is not None
        tmp_db.update_task_status(task_id, "running")
        tasks = tmp_db.get_all_tasks()
        assert tasks[0]["status"] == "running"

    def test_task_transitions_to_completed(self, tmp_db, results_dir):
        """Full pipeline: add → pick → run → complete with result file."""
        task_id = tmp_db.add_task("Explain quantum entanglement simply")
        tmp_db.update_task_status(task_id, "running")

        # Simulate result file creation
        result_path = str(Path(results_dir) / f"task_{task_id}.md")
        Path(result_path).write_text(
            f"---\ntask_id: {task_id}\nstatus: completed\n---\n\n# Response\n\nQuantum entanglement is...",
            encoding="utf-8"
        )

        tmp_db.update_task_status(task_id, "completed", result_path=result_path)
        tasks = tmp_db.get_all_tasks()
        assert tasks[0]["status"] == "completed"
        assert tasks[0]["result_path"] == result_path
        assert Path(result_path).exists()

    def test_completed_task_not_picked_again(self, tmp_db):
        task_id = tmp_db.add_task("Already done")
        tmp_db.update_task_status(task_id, "running")
        tmp_db.update_task_status(task_id, "completed")
        # Queue should now be empty
        assert tmp_db.get_next_task() is None

    def test_multiple_tasks_processed_in_order(self, tmp_db):
        """Tasks with equal priority should be processed FIFO."""
        id1 = tmp_db.add_task("First task")
        time.sleep(0.01)  # ensure different created_at
        id2 = tmp_db.add_task("Second task")
        time.sleep(0.01)
        id3 = tmp_db.add_task("Third task")

        first = tmp_db.get_next_task()
        assert first["id"] == id1

        tmp_db.update_task_status(id1, "completed")
        second = tmp_db.get_next_task()
        assert second["id"] == id2

        tmp_db.update_task_status(id2, "completed")
        third = tmp_db.get_next_task()
        assert third["id"] == id3


# ══════════════════════════════════════════════════════════════════════════════
# 7.2.2  Priority ordering
# ══════════════════════════════════════════════════════════════════════════════

class TestPriorityOrdering:

    def test_high_priority_processed_before_normal(self, tmp_db):
        normal_id = tmp_db.add_task("Normal priority task", priority=0)
        high_id = tmp_db.add_task("High priority task", priority=10)

        first = tmp_db.get_next_task()
        assert first["id"] == high_id

    def test_equal_priority_is_fifo(self, tmp_db):
        id1 = tmp_db.add_task("Task A", priority=5)
        time.sleep(0.01)
        id2 = tmp_db.add_task("Task B", priority=5)

        first = tmp_db.get_next_task()
        assert first["id"] == id1

    def test_priority_zero_processed_last(self, tmp_db):
        low_id  = tmp_db.add_task("Low",    priority=0)
        high_id = tmp_db.add_task("High",   priority=5)
        mid_id  = tmp_db.add_task("Medium", priority=1)

        order = []
        for _ in range(3):
            t = tmp_db.get_next_task()
            order.append(t["id"])
            tmp_db.update_task_status(t["id"], "completed")

        assert order == [high_id, mid_id, low_id]


# ══════════════════════════════════════════════════════════════════════════════
# 7.2.3  Rate-limit handling
# ══════════════════════════════════════════════════════════════════════════════

class TestRateLimitHandling:

    def test_task_set_to_waiting_limit_on_detection(self, tmp_db):
        task_id = tmp_db.add_task("Rate limited task")
        tmp_db.update_task_status(task_id, "running")

        reset_time = datetime.now() + timedelta(hours=3)
        tmp_db.set_claude_available(False, reset_time=reset_time, limit_message="Usage limit reached")
        tmp_db.update_task_status(task_id, "waiting_limit")

        tasks = tmp_db.get_all_tasks()
        assert tasks[0]["status"] == "waiting_limit"

    def test_waiting_task_not_picked_before_reset(self, tmp_db):
        task_id = tmp_db.add_task("Waiting task")
        reset_time = datetime.now() + timedelta(hours=5)
        tmp_db.set_claude_available(False, reset_time=reset_time, limit_message="limit")
        tmp_db.update_task_status(task_id, "waiting_limit")

        # is_claude_available() will return False while reset_time is in the future;
        # get_next_task() returns None for waiting_limit tasks while limit is active
        assert tmp_db.is_claude_available() is False

    def test_waiting_task_picked_after_reset(self, tmp_db):
        task_id = tmp_db.add_task("Will resume task")
        # Mark Claude available again (simulates reset_time passing)
        tmp_db.set_claude_available(True)
        # Requeue the task (was waiting_limit → now queued again)
        tmp_db.update_task_status(task_id, "queued")
        task = tmp_db.get_next_task()
        assert task is not None
        assert task["id"] == task_id

    def test_claude_status_tracks_unavailable(self, tmp_db):
        reset_time = datetime.now() + timedelta(hours=2)
        tmp_db.set_claude_available(False, reset_time=reset_time, limit_message="You've hit the limit")
        # available flag is stored as integer 0/1
        assert tmp_db.is_claude_available() is False

    def test_claude_status_limit_message_stored(self, tmp_db):
        reset_time = datetime.now() + timedelta(hours=2)
        tmp_db.set_claude_available(False, reset_time=reset_time, limit_message="You've hit the limit")
        status = tmp_db.get_claude_status()
        # DB stores as 'last_limit_message'
        assert status.get("last_limit_message") == "You've hit the limit"

    def test_claude_available_cleared_on_resume(self, tmp_db):
        tmp_db.set_claude_available(False, reset_time=datetime.now() + timedelta(hours=1), limit_message="limit")
        tmp_db.set_claude_available(True)
        assert tmp_db.is_claude_available() is True


# ══════════════════════════════════════════════════════════════════════════════
# 7.2.4  Retry & failure flow
# ══════════════════════════════════════════════════════════════════════════════

class TestRetryAndFailure:

    def test_failed_task_retry_count_increments(self, tmp_db):
        task_id = tmp_db.add_task("Will fail once")
        tmp_db.update_task_status(task_id, "running")
        tmp_db.increment_retry_count(task_id)
        tmp_db.update_task_status(task_id, "queued")

        tasks = tmp_db.get_all_tasks()
        assert tasks[0]["retry_count"] == 1

    def test_task_marked_failed_after_max_retries(self, tmp_db):
        task_id = tmp_db.add_task("Will exhaust retries")
        # Simulate 3 retries
        for _ in range(3):
            tmp_db.update_task_status(task_id, "running")
            tmp_db.increment_retry_count(task_id)
            tmp_db.update_task_status(task_id, "queued")

        # On 4th attempt, mark as failed
        tmp_db.update_task_status(task_id, "running")
        tmp_db.update_task_status(task_id, "failed", error_message="Max retries exceeded")

        tasks = tmp_db.get_all_tasks()
        assert tasks[0]["status"] == "failed"
        assert tasks[0]["retry_count"] == 3
        assert "retries" in tasks[0]["error_message"].lower()

    def test_failed_task_not_picked_again(self, tmp_db):
        task_id = tmp_db.add_task("Already failed")
        tmp_db.update_task_status(task_id, "running")
        tmp_db.update_task_status(task_id, "failed", error_message="boom")
        assert tmp_db.get_next_task() is None

    def test_error_message_stored_correctly(self, tmp_db):
        task_id = tmp_db.add_task("Will fail with message")
        err = "Playwright timeout after 30 seconds"
        tmp_db.update_task_status(task_id, "running")
        tmp_db.update_task_status(task_id, "failed", error_message=err)

        tasks = tmp_db.get_all_tasks()
        assert tasks[0]["error_message"] == err


# ══════════════════════════════════════════════════════════════════════════════
# 7.2.5  Crash recovery (stale task detection)
# ══════════════════════════════════════════════════════════════════════════════

class TestCrashRecovery:

    def test_stale_running_tasks_recovered_on_startup(self, tmp_db):
        """Simulate a crash: tasks left in 'running' state should be reset."""
        id1 = tmp_db.add_task("Task that was running during crash")
        id2 = tmp_db.add_task("Another running task")

        # Force both to 'running' without going through normal flow
        for tid in (id1, id2):
            tmp_db.update_task_status(tid, "running")

        # Simulate startup recovery
        recovered = tmp_db.recover_stale_tasks()
        assert recovered == 2

        tasks = tmp_db.get_all_tasks()
        for t in tasks:
            assert t["status"] == "queued"

    def test_stale_recovery_increments_retry_count(self, tmp_db):
        task_id = tmp_db.add_task("Crashed mid-run")
        tmp_db.update_task_status(task_id, "running")
        tmp_db.recover_stale_tasks()

        tasks = tmp_db.get_all_tasks()
        assert tasks[0]["retry_count"] == 1

    def test_completed_tasks_not_affected_by_recovery(self, tmp_db):
        done_id = tmp_db.add_task("Already done")
        tmp_db.update_task_status(done_id, "running")
        tmp_db.update_task_status(done_id, "completed")

        stale_id = tmp_db.add_task("Crashed")
        tmp_db.update_task_status(stale_id, "running")

        recovered = tmp_db.recover_stale_tasks()
        assert recovered == 1  # only stale_id recovered

        tasks = tmp_db.get_all_tasks(limit=10)
        by_id = {t["id"]: t for t in tasks}
        assert by_id[done_id]["status"] == "completed"
        assert by_id[stale_id]["status"] == "queued"


# ══════════════════════════════════════════════════════════════════════════════
# 7.2.6  Worker process_task with mocked Claude adapter
# ══════════════════════════════════════════════════════════════════════════════

class TestWorkerIntegrationMockedClaude:
    """
    Tests the process_task() worker function end-to-end with a mocked
    Claude adapter — verifies DB state transitions and result file creation.
    """

    def test_successful_response_marks_completed(self, tmp_db, tmp_path):
        results_dir = str(tmp_path / "results")
        Path(results_dir).mkdir()
        task_id = tmp_db.add_task("Write a haiku about Python")

        with patch.dict(os.environ, {"RESULTS_DIR": results_dir}):
            import src.agent_worker as wm
            task = {"id": task_id, "prompt": "Write a haiku about Python", "retry_count": 0}
            mock_adapter = _make_mock_adapter({"status": "ok", "text": "Snakes of logic run"})
            wm.process_task(task, mock_adapter, MagicMock())

        tasks = tmp_db.get_all_tasks()
        assert tasks[0]["status"] == "completed"
        assert tasks[0]["result_path"] is not None

    def test_limit_response_marks_waiting_limit(self, tmp_db, tmp_path):
        results_dir = str(tmp_path / "results")
        Path(results_dir).mkdir()
        task_id = tmp_db.add_task("Limited task")

        # Track statuses by patching at the agent_worker level (where update_task_status is imported)
        seen_statuses = []
        import src.agent_worker as wm
        import src.database as real_db

        original_update = real_db.update_task_status
        def tracking_update(tid, status, **kwargs):
            seen_statuses.append(status)
            return original_update(tid, status, **kwargs)

        task = {"id": task_id, "prompt": "Limited task", "retry_count": 0}
        reset = datetime.now() + timedelta(hours=3)
        mock_adapter = _make_mock_adapter({"status": "limit", "reset_time": reset, "message": "Limit hit"})
        with patch.dict(os.environ, {"RESULTS_DIR": results_dir}):
            with patch("src.agent_worker.update_task_status", side_effect=tracking_update):
                with patch.object(wm, "wait_for_limit_reset"):
                    wm.process_task(task, mock_adapter, MagicMock())

        # Worker must have called update_task_status with 'waiting_limit'
        assert "waiting_limit" in seen_statuses

    def test_result_file_contains_prompt_and_response(self, tmp_db, tmp_path):
        results_dir = str(tmp_path / "results")
        Path(results_dir).mkdir()
        prompt = "Explain binary search to a five-year-old"
        task_id = tmp_db.add_task(prompt)

        with patch.dict(os.environ, {"RESULTS_DIR": results_dir}):
            import src.agent_worker as wm
            task = {"id": task_id, "prompt": prompt, "retry_count": 0}
            mock_adapter = _make_mock_adapter({"status": "ok", "text": "Imagine a sorted list..."})
            wm.process_task(task, mock_adapter, MagicMock())

        tasks = tmp_db.get_all_tasks()
        result_path = tasks[0]["result_path"]
        assert result_path is not None
        content = Path(result_path).read_text(encoding="utf-8")
        assert "binary search" in content.lower() or "five-year-old" in content


# ══════════════════════════════════════════════════════════════════════════════
# 7.2.7  Dashboard API ↔ database consistency
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardDatabaseConsistency:
    """Verify dashboard REST endpoints reflect correct DB state."""

    @staticmethod
    def _auth():
        import base64
        return {"Authorization": "Basic " + base64.b64encode(b"admin:testpass").decode()}

    def _patched_client(self):
        """Create a TestClient with auth module attributes patched (module-level, not env)."""
        from fastapi.testclient import TestClient
        import src.auth as auth_mod
        import src.dashboard as dash_mod
        # Must patch the module-level attributes since they're read at module import time
        with patch.object(auth_mod, "ADMIN_USERNAME", "admin"), \
             patch.object(auth_mod, "ADMIN_PASSWORD", "testpass"):
            return TestClient(dash_mod.app, raise_server_exceptions=False)

    def test_api_tasks_reflects_submitted_task(self, tmp_db):
        tmp_db.add_task("Dashboard consistency test")
        import src.auth as auth_mod
        import src.dashboard as dash_mod
        from fastapi.testclient import TestClient
        with patch.object(auth_mod, "ADMIN_USERNAME", "admin"), \
             patch.object(auth_mod, "ADMIN_PASSWORD", "testpass"):
            client = TestClient(dash_mod.app, raise_server_exceptions=False)
            resp = client.get("/api/tasks", headers=self._auth())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) >= 1

    def test_api_stats_counts_match_db(self, tmp_db):
        tmp_db.add_task("Task 1")
        id2 = tmp_db.add_task("Task 2")
        tmp_db.update_task_status(id2, "running")
        tmp_db.update_task_status(id2, "completed")

        import src.auth as auth_mod
        import src.dashboard as dash_mod
        from fastapi.testclient import TestClient
        with patch.object(auth_mod, "ADMIN_USERNAME", "admin"), \
             patch.object(auth_mod, "ADMIN_PASSWORD", "testpass"):
            client = TestClient(dash_mod.app, raise_server_exceptions=False)
            resp = client.get("/api/stats", headers=self._auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tasks"] >= 2

    def test_delete_via_api_removes_from_db(self, tmp_db):
        task_id = tmp_db.add_task("To be deleted")
        import src.auth as auth_mod
        import src.dashboard as dash_mod
        from fastapi.testclient import TestClient
        with patch.object(auth_mod, "ADMIN_USERNAME", "admin"), \
             patch.object(auth_mod, "ADMIN_PASSWORD", "testpass"):
            client = TestClient(dash_mod.app, raise_server_exceptions=False)
            resp = client.delete(f"/tasks/{task_id}", headers=self._auth())
        assert resp.status_code == 200
        tasks = tmp_db.get_all_tasks()
        assert not any(t["id"] == task_id for t in tasks)


# ══════════════════════════════════════════════════════════════════════════════
# 7.2.8  Morning report integration
# ══════════════════════════════════════════════════════════════════════════════

class TestMorningReportIntegration:

    def test_report_runs_against_populated_db(self, tmp_db):
        """Morning report should not raise against any real DB state."""
        for i in range(3):
            tid = tmp_db.add_task(f"Task {i}")
            tmp_db.update_task_status(tid, "running")
            tmp_db.update_task_status(tid, "completed")
        tmp_db.add_task("Pending task")

        import scripts.morning_report as mr
        report = mr.build_report()
        assert isinstance(report, str)
        assert len(report) > 50

    def test_report_contains_queue_section(self, tmp_db):
        tmp_db.add_task("Queued task A")
        tmp_db.add_task("Queued task B")

        import scripts.morning_report as mr
        report = mr.build_report()
        assert "Queue" in report or "Queued" in report or "queued" in report


# ══════════════════════════════════════════════════════════════════════════════
# 7.2.9  Health check integration
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthCheckIntegration:

    def test_health_check_db_passes_with_valid_setup(self, tmp_db, tmp_path):
        """Database check should pass in test environment."""
        results_dir = str(tmp_path / "results")
        logs_dir = str(tmp_path / "logs")
        browser_dir = str(tmp_path / "browser_data")
        for d in (results_dir, logs_dir, browser_dir):
            Path(d).mkdir(parents=True, exist_ok=True)

        import scripts.health_check as hc
        with patch.dict(os.environ, {
            "RESULTS_DIR": results_dir,
            "LOGS_DIR": logs_dir,
            "BROWSER_DATA_DIR": browser_dir,
        }):
            results = hc.run_checks()

        assert isinstance(results, list)
        db_result = next((r for r in results if r["name"] == "Database"), None)
        assert db_result is not None
        assert db_result["passed"] is True

    def test_health_check_fails_on_missing_results_dir(self, tmp_db, tmp_path):
        import scripts.health_check as hc
        logs_dir = str(tmp_path / "logs")
        browser_dir = str(tmp_path / "browser_data")
        Path(logs_dir).mkdir(); Path(browser_dir).mkdir()

        with patch.dict(os.environ, {
            "RESULTS_DIR": str(tmp_path / "nonexistent"),
            "LOGS_DIR": logs_dir,
            "BROWSER_DATA_DIR": browser_dir,
        }):
            results = hc.run_checks()

        results_check = next((r for r in results if r["name"] == "Results Dir"), None)
        assert results_check is not None
        assert results_check["passed"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 7.2.10  Concurrent read/write safety
# ══════════════════════════════════════════════════════════════════════════════

class TestConcurrentReadWrite:
    """
    Verify SQLite WAL mode allows concurrent readers while worker writes.
    Simulates dashboard polling while worker updates task status.
    """

    def test_concurrent_reads_while_writing(self, tmp_db):
        """Multiple threads can read while one thread writes."""
        # Add some tasks
        for i in range(5):
            tmp_db.add_task(f"Concurrent task {i}")

        errors = []
        reads_completed = []

        def reader():
            try:
                for _ in range(20):
                    tasks = tmp_db.get_all_tasks(limit=10)
                    reads_completed.append(len(tasks))
                    time.sleep(0.001)
            except Exception as e:
                errors.append(str(e))

        def writer():
            try:
                for i in range(5):
                    task_id = tmp_db.add_task(f"New task {i}")
                    time.sleep(0.002)
                    tmp_db.update_task_status(task_id, "running")
                    tmp_db.update_task_status(task_id, "completed")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=reader) for _ in range(3)]
        threads.append(threading.Thread(target=writer))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Concurrent errors: {errors}"
        assert len(reads_completed) > 0

    def test_statistics_consistent_under_concurrent_writes(self, tmp_db):
        """Statistics should reflect all writes after concurrent inserts."""
        def insert_tasks(n: int):
            for i in range(n):
                tmp_db.add_task(f"Thread task {threading.get_ident()}_{i}")

        threads = [threading.Thread(target=insert_tasks, args=(10,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        stats = tmp_db.get_statistics()
        assert stats["total_tasks"] == 50
