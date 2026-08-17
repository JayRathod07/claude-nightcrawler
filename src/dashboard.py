"""
Dashboard — FastAPI web application for Claude Nightcrawler

Provides:
    GET  /              → Main dashboard page (HTML)
    POST /tasks         → Submit a new task
    DELETE /tasks/{id}  → Delete a task
    GET  /api/tasks     → Task list (JSON, for polling)
    GET  /api/stats     → System statistics (JSON)
    GET  /api/status    → Claude availability (JSON)
    GET  /results/{id}  → Download task result file
    GET  /health        → Public health endpoint

Authentication: HTTP Basic Auth on all routes except /health
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

# ── Project imports ────────────────────────────────────────────────────────────
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.auth import authenticate
from src.database import (
    add_task,
    delete_task,
    get_all_tasks,
    get_claude_status,
    get_statistics,
    get_task_by_id,
    init_db,
    is_claude_available,
)

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
RESULTS_DIR = os.getenv("RESULTS_DIR", "results")
TEMPLATES_DIR = str(ROOT / "templates")
STATIC_DIR = str(ROOT / "static")

# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Dashboard started — database initialised")
    yield

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Claude Nightcrawler",
    description="Overnight Claude.ai automation agent dashboard",
    version="1.0.0",
    docs_url=None,   # disable public docs
    redoc_url=None,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ── Jinja2 template filters ────────────────────────────────────────────────────
def _status_label(status: str) -> str:
    return {
        "queued": "Queued",
        "running": "Running",
        "completed": "Completed",
        "waiting_limit": "Waiting",
        "failed": "Failed",
    }.get(status, status.title())


def _status_class(status: str) -> str:
    return {
        "queued": "status-queued",
        "running": "status-running",
        "completed": "status-completed",
        "waiting_limit": "status-waiting",
        "failed": "status-failed",
    }.get(status, "")


templates.env.filters["status_label"] = _status_label
templates.env.filters["status_class"] = _status_class


# ── Helper ─────────────────────────────────────────────────────────────────────
def _task_to_dict(task: Any) -> Dict:
    """Convert a DB task row to a plain dict safe for JSON."""
    if hasattr(task, "keys"):
        d = dict(task)
    elif isinstance(task, dict):
        d = task
    else:
        d = {}
    # Convert any non-serialisable values to str
    for key in ("created_at", "started_at", "completed_at", "limit_reset_time"):
        if d.get(key) is not None:
            d[key] = str(d[key])
    return d


# ── HTML routes (authenticated) ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    username: str = Depends(authenticate),
    page: int = 1,
    status_filter: str = "all",
):
    """Main dashboard view."""
    limit = 20
    offset = (page - 1) * limit

    all_raw = get_all_tasks(limit=limit + 1, offset=offset)

    # Apply optional status filter
    if status_filter and status_filter != "all":
        all_raw = [t for t in all_raw if t.get("status") == status_filter]

    has_next = len(all_raw) > limit
    tasks = [_task_to_dict(t) for t in all_raw[:limit]]

    stats = get_statistics()
    claude_status = get_claude_status()
    available = bool(claude_status.get("available", True))

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "username": username,
            "tasks": tasks,
            "stats": stats,
            "claude_available": available,
            "claude_status": {k: str(v) if v is not None else None
                              for k, v in claude_status.items()},
            "page": page,
            "has_next": has_next,
            "status_filter": status_filter,
        },
    )



# ── Task actions ───────────────────────────────────────────────────────────────

@app.post("/tasks", response_class=RedirectResponse)
async def submit_task(
    request: Request,
    prompt: str = Form(...),
    priority: int = Form(default=0),
    username: str = Depends(authenticate),
):
    """Submit a new task from the web form."""
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    try:
        task_id = add_task(prompt=prompt, priority=priority)
        logger.info("Task %d submitted by %s (priority=%d)", task_id, username, priority)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return RedirectResponse(url="/?submitted=1", status_code=303)


@app.delete("/tasks/{task_id}", response_class=JSONResponse)
async def remove_task(
    task_id: int,
    username: str = Depends(authenticate),
):
    """Delete a task by ID."""
    deleted = delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    logger.info("Task %d deleted by %s", task_id, username)
    return {"success": True, "task_id": task_id}


# ── Result file download ───────────────────────────────────────────────────────

@app.get("/results/{task_id}")
async def download_result(
    task_id: int,
    username: str = Depends(authenticate),
):
    """Serve the Markdown result file for a completed task."""
    task = get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    result_path = task.get("result_path") if hasattr(task, "get") else None
    if not result_path:
        raise HTTPException(status_code=404, detail="Result not ready yet")

    file = Path(result_path)
    if not file.exists():
        raise HTTPException(status_code=404, detail="Result file not found on disk")

    return FileResponse(
        path=str(file),
        media_type="text/markdown",
        filename=f"task_{task_id}.md",
    )


# ── JSON API endpoints (for auto-refresh polling) ─────────────────────────────

@app.get("/api/tasks", response_class=JSONResponse)
async def api_tasks(
    username: str = Depends(authenticate),
    limit: int = 50,
    offset: int = 0,
    status_filter: Optional[str] = None,
):
    """Return current task list as JSON (used by the auto-refresh JS)."""
    tasks = get_all_tasks(limit=limit, offset=offset)
    result = [_task_to_dict(t) for t in tasks]

    if status_filter and status_filter != "all":
        result = [t for t in result if t.get("status") == status_filter]

    return {"tasks": result, "count": len(result)}


@app.get("/api/stats", response_class=JSONResponse)
async def api_stats(username: str = Depends(authenticate)):
    """Return system statistics as JSON."""
    stats = get_statistics()
    return stats


@app.get("/api/status", response_class=JSONResponse)
async def api_claude_status(username: str = Depends(authenticate)):
    """Return Claude.ai availability status as JSON."""
    s = get_claude_status()
    return {
        "available": bool(s.get("available", True)),
        "reset_time": str(s.get("reset_time")) if s.get("reset_time") else None,
        "total_requests_today": s.get("total_requests_today", 0),
        "last_limit_message": s.get("last_limit_message"),
    }


# ── Public health endpoint ─────────────────────────────────────────────────────

@app.get("/health", response_class=JSONResponse)
async def health():
    """Public health check — no auth required. Used by load balancers / uptime monitors."""
    return {"status": "ok", "service": "claude-nightcrawler"}


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "src.dashboard:app",
        host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.getenv("DASHBOARD_PORT", "8000")),
        reload=os.getenv("ENVIRONMENT", "production") == "development",
        log_level="info",
    )
