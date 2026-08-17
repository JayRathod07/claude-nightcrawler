"""
Tests for src/dashboard.py — Phase 4 Web Dashboard

Uses FastAPI TestClient with app.dependency_overrides to bypass auth,
and unittest.mock.patch to isolate DB calls.

Coverage:
    • GET  /           → dashboard HTML
    • POST /tasks      → task submission, redirect, validation
    • DELETE /tasks/N  → task deletion
    • GET  /api/tasks  → JSON task list + count
    • GET  /api/stats  → JSON statistics
    • GET  /api/status → JSON Claude status
    • GET  /results/N  → Markdown file download
    • GET  /health     → public health check (no auth)
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Ensure static/template dirs exist before app mounts StaticFiles
(ROOT / "static" / "css").mkdir(parents=True, exist_ok=True)
(ROOT / "static" / "js").mkdir(parents=True, exist_ok=True)
(ROOT / "templates").mkdir(parents=True, exist_ok=True)

# ── Shared mock data ──────────────────────────────────────────────────────────
MOCK_STATS = {
    "total_tasks": 10,
    "task_counts": {"queued": 2, "running": 1, "completed": 7, "failed": 0, "waiting_limit": 0},
    "avg_completion_minutes": 3.5,
}
MOCK_STATUS = {
    "available": 1,
    "reset_time": None,
    "total_requests_today": 5,
    "last_limit_message": None,
}
SAMPLE_TASKS = [
    {
        "id": 1, "prompt": "Write a poem about Python",
        "status": "completed", "priority": 0,
        "created_at": "2026-08-10 10:00:00",
        "started_at": "2026-08-10 10:01:00",
        "completed_at": "2026-08-10 10:03:00",
        "result_path": "results/task_1.md",
        "error_message": None, "retry_count": 0, "max_retries": 3,
        "limit_reset_time": None, "metadata": None,
    },
    {
        "id": 2, "prompt": "Summarise the Rust book",
        "status": "queued", "priority": 1,
        "created_at": "2026-08-10 11:00:00",
        "started_at": None, "completed_at": None,
        "result_path": None, "error_message": None,
        "retry_count": 0, "max_retries": 3,
        "limit_reset_time": None, "metadata": None,
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client(tasks=None):
    """
    Return a TestClient with:
      • auth dependency overridden → always returns "admin"
      • DB functions patched via unittest.mock
    Caller is responsible for entering/exiting the patch context.
    """
    from starlette.testclient import TestClient
    from src.dashboard import app
    from src import auth as auth_module

    # Override the auth dependency
    app.dependency_overrides[auth_module.authenticate] = lambda: "admin"

    client = TestClient(app)
    return client


@contextmanager
def client_ctx(tasks=None):
    """
    Context manager that provides a fully mocked TestClient,
    then cleans up dependency overrides afterwards.
    """
    from src.dashboard import app
    from src import auth as auth_module

    with patch("src.dashboard.init_db"), \
         patch("src.dashboard.get_all_tasks", return_value=tasks or []), \
         patch("src.dashboard.get_statistics", return_value=MOCK_STATS), \
         patch("src.dashboard.get_claude_status", return_value=MOCK_STATUS), \
         patch("src.dashboard.is_claude_available", return_value=True):

        app.dependency_overrides[auth_module.authenticate] = lambda: "admin"
        try:
            from starlette.testclient import TestClient
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()


# ─── /health ─────────────────────────────────────────────────────────────────

class TestHealth:
    def test_returns_200(self):
        with client_ctx() as c:
            resp = c.get("/health")
        assert resp.status_code == 200

    def test_returns_ok_status(self):
        with client_ctx() as c:
            resp = c.get("/health")
        assert resp.json()["status"] == "ok"

    def test_has_service_key(self):
        with client_ctx() as c:
            resp = c.get("/health")
        assert "service" in resp.json()

    def test_no_auth_required(self):
        """Health endpoint must work without credentials."""
        # Use raw app without auth override
        from starlette.testclient import TestClient
        from src.dashboard import app
        with patch("src.dashboard.init_db"):
            c = TestClient(app)
            resp = c.get("/health")
        assert resp.status_code == 200


# ─── GET / ───────────────────────────────────────────────────────────────────

class TestDashboardPage:
    def test_returns_html(self):
        with client_ctx() as c:
            resp = c.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_contains_brand_name(self):
        with client_ctx() as c:
            resp = c.get("/")
        assert "Nightcrawler" in resp.text

    def test_contains_submit_form(self):
        with client_ctx() as c:
            resp = c.get("/")
        assert "<form" in resp.text
        assert 'name="prompt"' in resp.text

    def test_contains_task_table(self):
        with client_ctx() as c:
            resp = c.get("/")
        assert "task-table" in resp.text

    def test_renders_task_prompts(self):
        with client_ctx(SAMPLE_TASKS) as c:
            resp = c.get("/")
        assert "Write a poem about Python" in resp.text
        assert "Summarise the Rust book" in resp.text

    def test_status_filter_accepted(self):
        with client_ctx() as c:
            resp = c.get("/?status_filter=completed")
        assert resp.status_code == 200

    def test_pagination_page_param(self):
        with client_ctx() as c:
            resp = c.get("/?page=2")
        assert resp.status_code == 200


# ─── POST /tasks ─────────────────────────────────────────────────────────────

class TestSubmitTask:
    def test_redirect_on_success(self):
        with client_ctx() as c:
            with patch("src.dashboard.add_task", return_value=99):
                resp = c.post(
                    "/tasks",
                    data={"prompt": "Tell me about the universe", "priority": "0"},
                    follow_redirects=False,
                )
        assert resp.status_code == 303

    def test_redirect_location_is_root(self):
        with client_ctx() as c:
            with patch("src.dashboard.add_task", return_value=1):
                resp = c.post(
                    "/tasks",
                    data={"prompt": "A valid prompt", "priority": "0"},
                    follow_redirects=False,
                )
        assert resp.headers["location"].startswith("/")

    def test_passes_correct_priority(self):
        with client_ctx() as c:
            with patch("src.dashboard.add_task", return_value=5) as mock_add:
                c.post(
                    "/tasks",
                    data={"prompt": "High priority task", "priority": "10"},
                    follow_redirects=False,
                )
        mock_add.assert_called_once_with(prompt="High priority task", priority=10)

    def test_db_value_error_returns_400(self):
        with client_ctx() as c:
            with patch("src.dashboard.add_task", side_effect=ValueError("empty")):
                resp = c.post(
                    "/tasks",
                    data={"prompt": "x", "priority": "0"},
                    follow_redirects=False,
                )
        assert resp.status_code == 400


# ─── DELETE /tasks/{id} ───────────────────────────────────────────────────────

class TestDeleteTask:
    def test_delete_existing_task_ok(self):
        with client_ctx() as c:
            with patch("src.dashboard.delete_task", return_value=True):
                resp = c.delete("/tasks/1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_delete_returns_task_id(self):
        with client_ctx() as c:
            with patch("src.dashboard.delete_task", return_value=True):
                resp = c.delete("/tasks/42")
        assert resp.json()["task_id"] == 42

    def test_delete_missing_task_404(self):
        with client_ctx() as c:
            with patch("src.dashboard.delete_task", return_value=False):
                resp = c.delete("/tasks/9999")
        assert resp.status_code == 404


# ─── GET /api/tasks ──────────────────────────────────────────────────────────

class TestApiTasks:
    def test_returns_200(self):
        with client_ctx() as c:
            resp = c.get("/api/tasks")
        assert resp.status_code == 200

    def test_has_tasks_key(self):
        with client_ctx() as c:
            resp = c.get("/api/tasks")
        assert "tasks" in resp.json()

    def test_has_count_key(self):
        with client_ctx() as c:
            resp = c.get("/api/tasks")
        assert "count" in resp.json()

    def test_tasks_is_list(self):
        with client_ctx() as c:
            resp = c.get("/api/tasks")
        assert isinstance(resp.json()["tasks"], list)

    def test_with_populated_data(self):
        with client_ctx(SAMPLE_TASKS) as c:
            resp = c.get("/api/tasks")
        assert resp.json()["count"] == 2


# ─── GET /api/stats ───────────────────────────────────────────────────────────

class TestApiStats:
    def test_returns_200(self):
        with client_ctx() as c:
            resp = c.get("/api/stats")
        assert resp.status_code == 200

    def test_has_total_tasks(self):
        with client_ctx() as c:
            resp = c.get("/api/stats")
        assert "total_tasks" in resp.json()

    def test_has_task_counts(self):
        with client_ctx() as c:
            resp = c.get("/api/stats")
        assert "task_counts" in resp.json()

    def test_total_tasks_is_int(self):
        with client_ctx() as c:
            resp = c.get("/api/stats")
        assert isinstance(resp.json()["total_tasks"], int)


# ─── GET /api/status ─────────────────────────────────────────────────────────

class TestApiStatus:
    def test_returns_200(self):
        with client_ctx() as c:
            resp = c.get("/api/status")
        assert resp.status_code == 200

    def test_has_available_field(self):
        with client_ctx() as c:
            resp = c.get("/api/status")
        assert "available" in resp.json()

    def test_available_is_bool(self):
        with client_ctx() as c:
            resp = c.get("/api/status")
        assert isinstance(resp.json()["available"], bool)

    def test_has_all_required_fields(self):
        with client_ctx() as c:
            resp = c.get("/api/status")
        data = resp.json()
        for key in ("available", "reset_time", "total_requests_today"):
            assert key in data


# ─── GET /results/{id} ───────────────────────────────────────────────────────

class TestResultDownload:
    def test_task_not_found_returns_404(self):
        with client_ctx() as c:
            with patch("src.dashboard.get_task_by_id", return_value=None):
                resp = c.get("/results/999")
        assert resp.status_code == 404

    def test_no_result_path_returns_404(self):
        with client_ctx() as c:
            with patch("src.dashboard.get_task_by_id",
                       return_value={"id": 1, "result_path": None}):
                resp = c.get("/results/1")
        assert resp.status_code == 404

    def test_file_missing_on_disk_returns_404(self, tmp_path):
        fake_path = str(tmp_path / "nonexistent.md")
        with client_ctx() as c:
            with patch("src.dashboard.get_task_by_id",
                       return_value={"id": 1, "result_path": fake_path}):
                resp = c.get("/results/1")
        assert resp.status_code == 404

    def test_serves_existing_file(self, tmp_path):
        md_file = tmp_path / "task_1.md"
        md_file.write_text("# Result\nHello world!", encoding="utf-8")
        with client_ctx() as c:
            with patch("src.dashboard.get_task_by_id",
                       return_value={"id": 1, "result_path": str(md_file)}):
                resp = c.get("/results/1")
        assert resp.status_code == 200
        assert "Hello world!" in resp.text
