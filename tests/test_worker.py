"""
Tests for src/agent_worker.py — Phase 3 Agent Worker

All external dependencies (database, adapter, notifier) are mocked.
Tests verify the logic inside each function without touching disk or network.

Coverage:
    • save_result()     — Markdown file generation and frontmatter
    • backoff_delay()   — Exponential backoff formula
    • wait_for_limit_reset() — shutdown-aware sleep loop
    • process_task()    — success, limit, retry, exhausted, login expired
    • _interruptible_sleep() — shutdown flag awareness
    • Notifier          — enabled/disabled, all event methods
    • health_check.py   — check runner
"""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Patch notifier before importing worker so no Telegram calls slip through ──
with patch.dict(os.environ, {"TELEGRAM_ENABLED": "false"}):
    from src import agent_worker as aw
    from src.agent_worker import (
        _interruptible_sleep,
        backoff_delay,
        process_task,
        save_result,
        wait_for_limit_reset,
    )
    from src.notifier import Notifier
    from src.claude_adapter import LoginExpiredException, ResponseExtractionError


# ─── Fixtures ──────────────────────────────────────────────────────────────────

def make_task(
    task_id: int = 1,
    prompt: str = "What is 2+2?",
    status: str = "queued",
    retry_count: int = 0,
    max_retries: int = 3,
) -> dict:
    return {
        "id": task_id,
        "prompt": prompt,
        "status": status,
        "priority": 0,
        "retry_count": retry_count,
        "max_retries": max_retries,
        "created_at": datetime.now().isoformat(),
        "started_at": None,
        "completed_at": None,
        "result_path": None,
        "error_message": None,
        "limit_reset_time": None,
        "metadata": None,
        "created_by": "admin",
    }


@pytest.fixture
def notifier():
    """Return a Notifier with all send methods mocked out."""
    n = Notifier()
    n._send = MagicMock(return_value=False)  # Telegram disabled
    return n


@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    adapter.send_prompt.return_value = {"status": "ok", "text": "Four."}
    return adapter


# ─── save_result() ────────────────────────────────────────────────────────────

class TestSaveResult:
    def test_creates_markdown_file(self, tmp_path):
        with patch.object(aw, "RESULTS_DIR", str(tmp_path)):
            task = make_task(task_id=42, prompt="Tell me a joke")
            path = save_result(task, "Why did the chicken cross the road?")

        result_file = Path(path)
        assert result_file.exists()
        assert result_file.suffix == ".md"
        assert "task_42" in result_file.name

    def test_file_contains_prompt_and_response(self, tmp_path):
        with patch.object(aw, "RESULTS_DIR", str(tmp_path)):
            task = make_task(task_id=1, prompt="Hello Claude")
            save_result(task, "Hello! How can I help?")

        content = (tmp_path / "task_1.md").read_text(encoding="utf-8")
        assert "Hello Claude" in content
        assert "Hello! How can I help?" in content

    def test_file_contains_frontmatter(self, tmp_path):
        with patch.object(aw, "RESULTS_DIR", str(tmp_path)):
            task = make_task(task_id=7)
            save_result(task, "Response text here")

        content = (tmp_path / "task_7.md").read_text(encoding="utf-8")
        assert "task_id: 7" in content
        assert "completed_at:" in content
        assert "response_length:" in content

    def test_long_prompt_truncated_in_preview(self, tmp_path):
        with patch.object(aw, "RESULTS_DIR", str(tmp_path)):
            long_prompt = "A" * 500
            task = make_task(task_id=2, prompt=long_prompt)
            save_result(task, "Short response")

        content = (tmp_path / "task_2.md").read_text(encoding="utf-8")
        assert "…" in content  # truncation marker present

    def test_returns_path_string(self, tmp_path):
        with patch.object(aw, "RESULTS_DIR", str(tmp_path)):
            task = make_task()
            result = save_result(task, "answer")
        assert isinstance(result, str)
        assert "task_1" in result

    def test_creates_results_dir_if_missing(self, tmp_path):
        new_dir = tmp_path / "new_results"
        with patch.object(aw, "RESULTS_DIR", str(new_dir)):
            task = make_task(task_id=99)
            save_result(task, "text")
        assert new_dir.exists()


# ─── backoff_delay() ─────────────────────────────────────────────────────────

class TestBackoffDelay:
    def test_first_retry_uses_base_delay(self):
        delay = backoff_delay(0, base=5)
        assert 5 <= delay <= 6.5  # base + ≤10% jitter

    def test_delay_doubles_each_retry(self):
        d0 = backoff_delay(0, base=5)
        d1 = backoff_delay(1, base=5)
        d2 = backoff_delay(2, base=5)
        # rough ordering: d0 < d1 < d2
        assert d0 < d1 < d2 + 5  # allow for jitter

    def test_capped_at_120_seconds(self):
        # After many retries, should be ≤ 120 + jitter
        delay = backoff_delay(100, base=5)
        assert delay <= 134  # 120 + 10% jitter

    def test_returns_float(self):
        assert isinstance(backoff_delay(0), float)


# ─── _interruptible_sleep() ───────────────────────────────────────────────────

class TestInterruptibleSleep:
    def test_sleeps_approximately_given_duration(self):
        start = time.monotonic()
        _interruptible_sleep(0.1)
        elapsed = time.monotonic() - start
        assert 0.05 <= elapsed <= 0.5

    def test_stops_immediately_on_shutdown_flag(self):
        with patch.object(aw._shutdown, "_stop", True):
            start = time.monotonic()
            _interruptible_sleep(10)  # should not actually sleep 10s
            elapsed = time.monotonic() - start
        assert elapsed < 1.0  # exits within 1s


# ─── wait_for_limit_reset() ──────────────────────────────────────────────────

class TestWaitForLimitReset:
    def test_returns_when_reset_time_passed(self, notifier):
        past = datetime.now() - timedelta(seconds=1)
        with patch("src.agent_worker.set_claude_available") as mock_set:
            wait_for_limit_reset(1, past, notifier)
            mock_set.assert_called_once_with(True)

    def test_stops_early_on_shutdown(self, notifier):
        future = datetime.now() + timedelta(hours=5)
        with patch.object(aw._shutdown, "_stop", True):
            with patch("src.agent_worker.set_claude_available") as mock_set:
                wait_for_limit_reset(1, future, notifier)
        # Should not call set_claude_available since shutdown was requested
        mock_set.assert_not_called()


# ─── process_task() ──────────────────────────────────────────────────────────

class TestProcessTask:
    def test_successful_task_marked_completed(self, mock_adapter, notifier, tmp_path):
        task = make_task()
        mock_adapter.send_prompt.return_value = {"status": "ok", "text": "The answer is 42."}

        with patch.object(aw, "RESULTS_DIR", str(tmp_path)), \
             patch("src.agent_worker.update_task_status") as mock_update, \
             patch("src.agent_worker.log_task_event"), \
             patch("src.agent_worker.increment_requests_today"), \
             patch("src.agent_worker.increment_retry_count"):

            result = process_task(task, mock_adapter, notifier)

        assert result is True
        # Final status should be 'completed'
        calls = [c.args[1] for c in mock_update.call_args_list]
        assert "completed" in calls

    def test_limit_response_marks_waiting_limit(self, mock_adapter, notifier):
        task = make_task()
        reset = datetime.now() + timedelta(hours=3)
        mock_adapter.send_prompt.return_value = {
            "status": "limit",
            "reset_time": reset,
            "message": "You've reached your usage limit. Resets in 3 hours.",
        }

        with patch("src.agent_worker.update_task_status") as mock_update, \
             patch("src.agent_worker.log_task_event"), \
             patch("src.agent_worker.set_claude_available"), \
             patch("src.agent_worker.increment_requests_today"), \
             patch("src.agent_worker.wait_for_limit_reset"):

            result = process_task(task, mock_adapter, notifier)

        assert result is True
        statuses = [c.args[1] for c in mock_update.call_args_list]
        assert "waiting_limit" in statuses
        assert "queued" in statuses  # re-queued after wait

    def test_login_expired_propagates(self, mock_adapter, notifier):
        task = make_task()
        mock_adapter.send_prompt.side_effect = LoginExpiredException("Session expired")

        with patch("src.agent_worker.update_task_status"), \
             patch("src.agent_worker.log_task_event"):

            with pytest.raises(LoginExpiredException):
                process_task(task, mock_adapter, notifier)

    def test_error_increments_retry_and_requeues(self, mock_adapter, notifier):
        task = make_task(retry_count=0, max_retries=3)
        mock_adapter.send_prompt.side_effect = RuntimeError("Browser timeout")

        with patch("src.agent_worker.update_task_status") as mock_update, \
             patch("src.agent_worker.log_task_event"), \
             patch("src.agent_worker.increment_retry_count", return_value=1), \
             patch("src.agent_worker.backoff_delay", return_value=0.01):

            result = process_task(task, mock_adapter, notifier)

        assert result is False
        statuses = [c.args[1] for c in mock_update.call_args_list]
        assert "queued" in statuses

    def test_exhausted_retries_marks_failed(self, mock_adapter, notifier):
        task = make_task(retry_count=3, max_retries=3)
        mock_adapter.send_prompt.side_effect = RuntimeError("Persistent failure")

        with patch("src.agent_worker.update_task_status") as mock_update, \
             patch("src.agent_worker.log_task_event"), \
             patch("src.agent_worker.increment_retry_count", return_value=4):

            result = process_task(task, mock_adapter, notifier)

        assert result is False
        statuses = [c.args[1] for c in mock_update.call_args_list]
        assert "failed" in statuses

    def test_unexpected_exception_marks_failed(self, mock_adapter, notifier):
        task = make_task()
        mock_adapter.send_prompt.side_effect = MemoryError("OOM")

        with patch("src.agent_worker.update_task_status") as mock_update, \
             patch("src.agent_worker.log_task_event"):

            result = process_task(task, mock_adapter, notifier)

        assert result is False
        statuses = [c.args[1] for c in mock_update.call_args_list]
        assert "failed" in statuses

    def test_result_file_created_on_success(self, mock_adapter, notifier, tmp_path):
        task = make_task(task_id=55)
        mock_adapter.send_prompt.return_value = {"status": "ok", "text": "Result content"}

        with patch.object(aw, "RESULTS_DIR", str(tmp_path)), \
             patch("src.agent_worker.update_task_status"), \
             patch("src.agent_worker.log_task_event"), \
             patch("src.agent_worker.increment_requests_today"):

            process_task(task, mock_adapter, notifier)

        assert (tmp_path / "task_55.md").exists()


# ─── Notifier ─────────────────────────────────────────────────────────────────

class TestNotifier:
    def test_disabled_send_returns_false(self):
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "false"}):
            import importlib
            import src.notifier as nm
            importlib.reload(nm)
            n = nm.Notifier()
            result = n._send("test")
        assert result is False

    def test_notify_completed_does_not_raise(self, notifier):
        notifier.notify_completed(1, "results/task_1.md", "Great answer!")
        # Should not raise even if _send returns False

    def test_notify_limit_hit_does_not_raise(self, notifier):
        notifier.notify_limit_hit(1, datetime.now() + timedelta(hours=3))

    def test_notify_error_does_not_raise(self, notifier):
        notifier.notify_error(0, "Some error occurred")

    def test_notify_startup_does_not_raise(self, notifier):
        notifier.notify_startup()

    def test_notify_shutdown_does_not_raise(self, notifier):
        notifier.notify_shutdown()

    def test_missing_token_returns_false(self):
        with patch.dict(os.environ, {
            "TELEGRAM_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        }):
            import importlib
            import src.notifier as nm
            importlib.reload(nm)
            n = nm.Notifier()
            result = n._send("test")
        assert result is False


# ─── Health Check ─────────────────────────────────────────────────────────────

class TestHealthCheck:
    def test_check_runner_returns_list(self):
        from scripts.health_check import run_checks
        with patch("scripts.health_check.init_db"), \
             patch("scripts.health_check.get_statistics", return_value={"total_tasks": 0}), \
             patch("scripts.health_check.get_claude_status", return_value={"available": 1}), \
             patch("scripts.health_check.get_all_tasks", return_value=[]):
            results = run_checks()
        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert "name" in r
            assert "passed" in r
            assert "message" in r

    def test_all_passed_when_everything_ok(self):
        from scripts.health_check import run_checks
        with patch("scripts.health_check.init_db"), \
             patch("scripts.health_check.get_statistics", return_value={"total_tasks": 5}), \
             patch("scripts.health_check.get_claude_status", return_value={"available": 1}), \
             patch("scripts.health_check.get_all_tasks", return_value=[]):
            results = run_checks()
        db_result = next(r for r in results if r["name"] == "Database")
        assert db_result["passed"] is True


# ─── Integration-style: save_result + DB read-back ───────────────────────────

class TestSaveResultIntegration:
    def test_saved_file_is_valid_markdown(self, tmp_path):
        with patch.object(aw, "RESULTS_DIR", str(tmp_path)):
            task = make_task(
                task_id=100,
                prompt="Explain quantum computing",
                retry_count=1,
            )
            response = "Quantum computing uses qubits instead of classical bits..."
            path = save_result(task, response)

        content = Path(path).read_text(encoding="utf-8")

        # Check YAML frontmatter delimiters
        assert content.startswith("---")
        assert content.count("---") >= 2

        # Check Markdown sections
        assert "# Task 100" in content
        assert "## Prompt" in content
        assert "## Response" in content

        # Check actual content
        assert "Explain quantum computing" in content
        assert "qubits" in content
