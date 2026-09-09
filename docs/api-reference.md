# API Reference — Claude Nightcrawler Dashboard

Complete reference for all HTTP endpoints, request/response formats, authentication, and error handling.

**Base URL (production)**: `https://your-agent.duckdns.org`
**Base URL (local dev)**: `http://localhost:8000`

---

## Table of Contents

1. [Authentication](#authentication)
2. [HTML Routes](#html-routes)
3. [REST API Endpoints](#rest-api-endpoints)
4. [Health Check](#health-check)
5. [Error Responses](#error-responses)
6. [Task Status Values](#task-status-values)
7. [Priority Levels](#priority-levels)
8. [Result File Format](#result-file-format)
9. [Client Examples](#client-examples)

---

## Authentication

All routes except `/health` require **HTTP Basic Authentication**.

| Header | Value |
|---|---|
| `Authorization` | `Basic base64(username:password)` |

**Example with curl**:

```bash
curl -u admin:your-password https://your-agent.duckdns.org/api/tasks
```

**Example with Python**:

```python
import requests
resp = requests.get(
    "https://your-agent.duckdns.org/api/tasks",
    auth=("admin", "your-password")
)
```

**Incorrect credentials** → `401 Unauthorized` with `WWW-Authenticate: Basic` header.

> The password comparison uses `secrets.compare_digest` — immune to timing attacks.

---

## HTML Routes

### `GET /`

Main dashboard page.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | `1` | Page number (20 tasks per page) |
| `status_filter` | string | `all` | Filter by status: `all`, `queued`, `running`, `completed`, `waiting_limit`, `failed` |
| `submitted` | int | — | When `1`, shows a success flash message (set by redirect after form submit) |

**Response**: `200 OK` — HTML page (Jinja2 rendered, Liquid Glass UI)

---

### `POST /tasks`

Submit a new task via HTML form.

**Content-Type**: `application/x-www-form-urlencoded`

**Form fields**:

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `prompt` | string | Yes | 1–50,000 chars | The prompt text to send to Claude |
| `priority` | int | No | 0, 1, 5, or 10 | Priority level; defaults to 0 (Normal) |

**Success response**: `302 Redirect` → `/?submitted=1`

**Error responses**:

| Status | Condition |
|---|---|
| `400 Bad Request` | Empty or whitespace-only prompt |
| `422 Unprocessable Entity` | Missing required field or wrong type |

---

## REST API Endpoints

### `GET /api/tasks`

Returns a paginated JSON list of tasks for live dashboard polling.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `20` | Maximum tasks to return (1–100) |
| `offset` | int | `0` | Pagination offset |
| `status_filter` | string | `all` | Same filter options as the HTML route |

**Response** `200 OK`:

```json
{
  "tasks": [
    {
      "id": 42,
      "prompt": "Write a Python script to sort a list of dicts by a key",
      "status": "completed",
      "priority": 0,
      "retry_count": 0,
      "result_path": "results/task_42.md",
      "error_message": null,
      "created_at": "2024-01-15T02:30:00",
      "started_at": "2024-01-15T02:31:05",
      "completed_at": "2024-01-15T02:33:22"
    },
    {
      "id": 43,
      "prompt": "Explain quantum entanglement simply",
      "status": "queued",
      "priority": 1,
      "retry_count": 0,
      "result_path": null,
      "error_message": null,
      "created_at": "2024-01-15T02:35:00",
      "started_at": null,
      "completed_at": null
    }
  ]
}
```

**Field descriptions**:

| Field | Type | Description |
|---|---|---|
| `id` | int | Unique task ID |
| `prompt` | string | The prompt text submitted |
| `status` | string | Current task status (see [Task Status Values](#task-status-values)) |
| `priority` | int | Priority level (higher = picked sooner) |
| `retry_count` | int | Number of retry attempts so far |
| `result_path` | string or null | Relative path to result file, null if not completed |
| `error_message` | string or null | Last error message, null if no error |
| `created_at` | string or null | ISO 8601 timestamp when task was submitted |
| `started_at` | string or null | ISO 8601 timestamp when worker started processing |
| `completed_at` | string or null | ISO 8601 timestamp when task completed or failed |

---

### `GET /api/stats`

System-wide statistics for the dashboard summary panel.

**No query parameters.**

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

**Field descriptions**:

| Field | Type | Description |
|---|---|---|
| `total_tasks` | int | Total tasks ever submitted |
| `avg_completion_minutes` | float or null | Average time from `started_at` to `completed_at` for completed tasks, null if no completed tasks |
| `task_counts` | object | Count of tasks in each status |

---

### `GET /api/status`

Claude.ai availability status — used to display the status indicator in the dashboard.

**No query parameters.**

**Response when Claude is available** `200 OK`:

```json
{
  "available": true,
  "reset_time": null,
  "limit_message": null,
  "total_requests_today": 47
}
```

**Response when rate-limited** `200 OK`:

```json
{
  "available": false,
  "reset_time": "2024-01-15T06:00:00",
  "limit_message": "You've reached your usage limit. Resets in 3 hours.",
  "total_requests_today": 50
}
```

**Field descriptions**:

| Field | Type | Description |
|---|---|---|
| `available` | bool | Whether Claude is currently accepting requests |
| `reset_time` | string or null | ISO 8601 when the rate limit resets, null if not limited |
| `limit_message` | string or null | The rate-limit message from Claude.ai, null if not limited |
| `total_requests_today` | int | Number of requests sent today (resets daily) |

> The `available` field auto-heals: if `reset_time` is in the past, it returns `true` and clears the limit record in the database.

---

### `DELETE /tasks/{task_id}`

Delete a task and its database record. The result file on disk is **not** deleted.

**Path parameters**:

| Parameter | Type | Description |
|---|---|---|
| `task_id` | int | The ID of the task to delete |

**Response** `200 OK`:

```json
{ "ok": true, "id": 42 }
```

**Error responses**:

| Status | Condition |
|---|---|
| `404 Not Found` | Task ID does not exist |

---

### `GET /results/{task_id}`

Download the Markdown result file for a completed task.

**Path parameters**:

| Parameter | Type | Description |
|---|---|---|
| `task_id` | int | Task ID whose result file to download |

**Response** `200 OK`:

- `Content-Type: text/markdown`
- `Content-Disposition: attachment; filename="task_42.md"`
- Body: the raw Markdown file content

**Error responses**:

| Status | Condition |
|---|---|
| `404 Not Found` | Task not found in DB, has no `result_path`, or file is missing on disk |

---

## Health Check

### `GET /health`

Public endpoint — **no authentication required**. Used by Caddy reverse proxy health probes and external uptime monitors (UptimeRobot, etc.).

**Response** `200 OK`:

```json
{
  "status": "ok",
  "timestamp": "2024-01-15T02:30:00.123456"
}
```

> This endpoint always returns `200` if the FastAPI process is running. It does NOT check the worker process or Claude availability — use `/api/status` for that.

---

## Error Responses

All API errors return a JSON body:

```json
{
  "detail": "Human-readable error message"
}
```

**Standard HTTP codes**:

| Code | Meaning | Common Causes |
|---|---|---|
| `400` | Bad Request | Empty prompt submitted |
| `401` | Unauthorized | Missing or incorrect Basic Auth credentials |
| `404` | Not Found | Task ID does not exist, result file missing |
| `422` | Unprocessable Entity | Missing required form field, wrong data type |
| `500` | Internal Server Error | Unexpected server-side exception |

---

## Task Status Values

| Status | Description | Terminal? |
|---|---|---|
| `queued` | Waiting to be picked up by the worker | No |
| `running` | Currently being sent to Claude.ai | No |
| `completed` | Successfully processed; result file available | Yes |
| `waiting_limit` | Paused because Claude.ai hit usage limit; auto-resumes | No |
| `failed` | All retry attempts exhausted; error_message populated | Yes |

**Terminal statuses** (`completed`, `failed`) will not be re-processed unless manually re-submitted.

---

## Priority Levels

| Value | Label | Description |
|---|---|---|
| `0` | Normal | Default; FIFO ordering among normal tasks |
| `1` | High | Processed before all Normal tasks |
| `5` | Urgent | Processed before High and Normal tasks |
| `10` | Critical | Highest priority; processed as soon as possible |

Within the same priority level, tasks are ordered by `created_at` (FIFO).

---

## Result File Format

Completed tasks have their Claude response saved as a Markdown file at `results/task_NNN.md`.

**File format**:

```markdown
---
task_id: 42
status: completed
created_at: 2024-01-15T02:30:00
completed_at: 2024-01-15T02:33:22
priority: 0
---

# Prompt

Write a Python script to sort a list of dicts by a key

---

# Response

Here is a Python script that sorts a list of dictionaries by a specified key:

```python
def sort_by_key(items, key):
    return sorted(items, key=lambda x: x[key])

# Example usage
people = [
    {"name": "Alice", "age": 30},
    {"name": "Bob", "age": 25},
]
print(sort_by_key(people, "age"))
```
```

The YAML frontmatter provides structured metadata; the body contains the raw prompt and Claude's full response.

---

## Client Examples

### Submit a task via curl

```bash
curl -X POST \
  -u admin:your-password \
  -d "prompt=Explain recursion with a simple example&priority=0" \
  https://your-agent.duckdns.org/tasks
```

### Poll task status

```bash
curl -u admin:your-password \
  "https://your-agent.duckdns.org/api/tasks?status_filter=running"
```

### Download result file

```bash
curl -u admin:your-password \
  -O -J \
  https://your-agent.duckdns.org/results/42
# Saves as task_42.md
```

### Python client example

```python
import requests
import time

BASE = "https://your-agent.duckdns.org"
AUTH = ("admin", "your-password")

# Submit a task
resp = requests.post(f"{BASE}/tasks",
    auth=AUTH,
    data={"prompt": "List 10 Python best practices", "priority": 0},
    allow_redirects=False)

# Get the task list and find the new task
tasks = requests.get(f"{BASE}/api/tasks", auth=AUTH).json()["tasks"]
new_task = tasks[0]
task_id = new_task["id"]

# Poll until completed
while True:
    task = requests.get(f"{BASE}/api/tasks", auth=AUTH).json()["tasks"]
    match = next((t for t in task if t["id"] == task_id), None)
    if match and match["status"] == "completed":
        result = requests.get(f"{BASE}/results/{task_id}", auth=AUTH)
        print(result.text)
        break
    time.sleep(15)
```
