# API Reference — Claude Nightcrawler Dashboard

All endpoints require **HTTP Basic Authentication** unless noted.

Base URL (production): `https://your-agent.duckdns.org`  
Base URL (local dev): `http://localhost:8000`

---

## Authentication

All routes (except `/health`) use HTTP Basic Auth.

```bash
curl -u admin:your-password https://your-agent.duckdns.org/api/tasks
```

Incorrect credentials → `401 Unauthorized` with `WWW-Authenticate: Basic` header.

---

## HTML Routes

### `GET /`

Main dashboard page.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number (20 tasks per page) |
| `status_filter` | string | `all` | Filter: `all`, `queued`, `running`, `completed`, `waiting_limit`, `failed` |

**Response**: HTML page (Jinja2 rendered)

---

### `POST /tasks`

Submit a new task via HTML form.

**Form body**:

| Field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | Yes | The prompt to send to Claude (max 50,000 chars) |
| `priority` | int | No | Priority level: 0=Normal, 1=High, 5=Urgent, 10=Critical |

**Response**: Redirect to `/?submitted=1` on success.

**Errors**:
- `400 Bad Request` — empty prompt
- `422 Unprocessable Entity` — missing required field

---

## REST API Endpoints

### `GET /api/tasks`

Returns JSON list of tasks for live polling.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 20 | Max tasks to return |
| `offset` | int | 0 | Pagination offset |
| `status_filter` | string | `all` | Same filter options as `/` |

**Response** `200 OK`:

```json
{
  "tasks": [
    {
      "id": 42,
      "prompt": "Write a Python script to...",
      "status": "completed",
      "priority": 0,
      "retry_count": 0,
      "result_path": "results/task_42.md",
      "error_message": null,
      "created_at": "2024-01-15T02:30:00",
      "started_at": "2024-01-15T02:31:05",
      "completed_at": "2024-01-15T02:33:22"
    }
  ]
}
```

**Task status values**:
- `queued` — waiting to be processed
- `running` — currently being processed by Claude
- `completed` — done, result file available
- `waiting_limit` — paused until Claude rate limit resets
- `failed` — all retries exhausted

---

### `GET /api/stats`

System statistics.

**Response** `200 OK`:

```json
{
  "total_tasks": 127,
  "avg_completion_minutes": 2.4,
  "task_counts": {
    "queued": 3,
    "running": 1,
    "completed": 118,
    "waiting_limit": 0,
    "failed": 5
  }
}
```

---

### `GET /api/status`

Claude.ai availability status.

**Response** `200 OK`:

```json
{
  "available": true,
  "reset_time": null,
  "limit_message": null,
  "total_requests_today": 47
}
```

When rate-limited:

```json
{
  "available": false,
  "reset_time": "2024-01-15T06:00:00",
  "limit_message": "You've reached your usage limit. Resets in 3 hours.",
  "total_requests_today": 50
}
```

---

### `DELETE /tasks/{task_id}`

Delete a task by ID.

**Path parameters**:

| Parameter | Type | Description |
|---|---|---|
| `task_id` | int | Task ID to delete |

**Response** `200 OK`:

```json
{ "ok": true, "id": 42 }
```

**Errors**:
- `404 Not Found` — task ID doesn't exist

---

### `GET /results/{task_id}`

Download the Markdown result file for a completed task.

**Response**: `200 OK` with `Content-Type: text/markdown`, `Content-Disposition: attachment; filename="task_42.md"`

**Errors**:
- `404 Not Found` — task not found, no result_path, or file missing on disk

**Example result file format**:

```markdown
---
task_id: 42
status: completed
created_at: 2024-01-15T02:30:00
completed_at: 2024-01-15T02:33:22
priority: 0
---

# Prompt

Write a Python script to...

---

# Response

Here's the Python script:

```python
def hello():
    print("Hello, world!")
```
```

---

## `GET /health`

Public health check endpoint — **no authentication required**.

Used by Caddy for health probes and external uptime monitors.

**Response** `200 OK`:

```json
{
  "status": "ok",
  "timestamp": "2024-01-15T02:30:00.123456"
}
```

---

## Error Responses

All API errors follow a consistent JSON format:

```json
{
  "detail": "Human-readable error message"
}
```

| HTTP Code | Meaning |
|---|---|
| 401 | Invalid or missing credentials |
| 404 | Resource not found |
| 422 | Validation error (missing/invalid field) |
| 500 | Internal server error |
