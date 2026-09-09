# Contributing to Claude Nightcrawler

Thank you for your interest in contributing! This guide covers how to set up a development environment, run tests, follow code style, and submit changes.

---

## Table of Contents

1. [Development Setup](#development-setup)
2. [Project Structure](#project-structure)
3. [Running Tests](#running-tests)
4. [Code Style](#code-style)
5. [Making Changes](#making-changes)
6. [Submitting a Pull Request](#submitting-a-pull-request)
7. [Adding New Features](#adding-new-features)
8. [Reporting Issues](#reporting-issues)
9. [Documentation](#documentation)

---

## Development Setup

### Prerequisites

- Python 3.11 or higher
- Git
- A Claude.ai account (for integration testing)

### Clone and Install

```bash
git clone https://github.com/JayRathod07/claude-nightcrawler.git
cd claude-nightcrawler

# Create virtual environment
python -m venv venv
source venv/bin/activate          # Linux / macOS
# venv\Scripts\activate           # Windows PowerShell

# Install dependencies (including dev dependencies)
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### Environment for Development

```bash
cp .env.example .env
# Edit .env with your test credentials
# For unit/integration tests, most values can be left as defaults
```

### Run the Dashboard Locally

```bash
source venv/bin/activate
uvicorn src.dashboard:app --reload --host 127.0.0.1 --port 8000
```

Open: http://localhost:8000

### Run the Worker Locally

In a separate terminal:

```bash
source venv/bin/activate
python src/agent_worker.py
```

---

## Project Structure

```
src/
  database.py        Data layer (SQLite)
  claude_adapter.py  Playwright browser automation
  agent_worker.py    Task processing loop
  dashboard.py       FastAPI web app
  auth.py            HTTP Basic Auth + bcrypt
  notifier.py        Telegram notifications
  utils.py           Shared utilities

tests/
  conftest.py        Shared fixtures (tmp_db, etc.)
  test_database.py   Database unit tests
  test_adapter.py    Claude adapter tests (mocked browser)
  test_worker.py     Worker unit tests
  test_dashboard.py  Dashboard API tests
  test_notifier.py   Notifier unit tests
  test_unit_extras.py  Auth and utils unit tests
  test_integration.py  End-to-end integration tests
  test_load.py         Performance / load tests

templates/
  dashboard.html     Jinja2 template

static/
  css/main.css       Liquid Glass UI styles
  js/dashboard.js    Live polling / interactions

scripts/
  setup.sh           Server setup script
  deploy.sh          Rolling deployment
  backup.sh          Database backup
  manual_login.py    One-time Claude login
  morning_report.py  Daily report
  health_check.py    Health verification

docs/               Documentation (Markdown)
config/             systemd / Caddy / DuckDNS config files
```

---

## Running Tests

### Full Test Suite

```bash
python -m pytest tests/ -v --tb=short
```

### Skip Load Tests (faster)

Load tests are slow (1–3 minutes). Skip them during normal development:

```bash
python -m pytest tests/ --ignore=tests/test_load.py -v
```

### Run a Specific Test File

```bash
python -m pytest tests/test_database.py -v
python -m pytest tests/test_integration.py -v
```

### Run a Single Test

```bash
python -m pytest tests/test_database.py::TestAddTask::test_add_task_returns_id -v
```

### Run Load Tests Explicitly

```bash
python -m pytest tests/test_load.py -v -s
```

### Test Coverage

```bash
python -m pytest tests/ --ignore=tests/test_load.py --cov=src --cov-report=term-missing
```

### Current Test Count

279 tests total (265 unit/integration, 14 load tests).

All tests must pass before a PR is merged:

```bash
python -m pytest tests/ --ignore=tests/test_load.py -q
# Expected: 265 passed
```

---

## Code Style

### Formatting and Linting

The project uses:

- **Ruff** for linting and formatting (preferred)
- **Black** formatting as a fallback
- **isort** for import ordering (built into Ruff)

```bash
# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Or with black
black src/ tests/
isort src/ tests/
```

### Type Hints

All public functions should have type hints:

```python
def add_task(prompt: str, priority: int = 0) -> int:
    ...
```

### Docstrings

Public functions and classes should have docstrings:

```python
def backoff_delay(retry_count: int, base: int = 5) -> float:
    """
    Calculate exponential backoff with jitter.

    Args:
        retry_count: Number of retries already attempted.
        base:        Base delay in seconds.

    Returns:
        Seconds to wait before next retry (capped at 120s + 10% jitter).
    """
```

### Error Handling

- Use specific exception types from `src/database.py` (`DatabaseError`, etc.)
- Never silence exceptions without logging them
- Add context to exceptions when re-raising:

```python
try:
    result = do_something()
except SpecificError as e:
    logger.error("Failed to do something for task %d: %s", task_id, e)
    raise
```

---

## Making Changes

### Create a Feature Branch

```bash
git checkout -b feat/my-new-feature
# or
git checkout -b fix/bug-description
```

### Branch Naming Conventions

| Prefix | Purpose | Example |
|---|---|---|
| `feat/` | New feature | `feat/batch-task-submission` |
| `fix/` | Bug fix | `fix/rate-limit-detection` |
| `docs/` | Documentation only | `docs/update-api-reference` |
| `refactor/` | Code refactor (no behaviour change) | `refactor/database-connection-pool` |
| `test/` | Add or fix tests | `test/cover-retry-logic` |
| `chore/` | Dependency updates, CI changes | `chore/update-playwright` |

### Commit Messages

Follow the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
type(scope): short description

Longer explanation if needed (wrap at 72 chars).
Explain WHY the change was made, not just what.

Fixes #123
```

Examples:

```
feat(notifier): add health alert for disk > 90%
fix(database): handle missing reset_time in is_claude_available
docs(api-reference): add Python client example
test(integration): cover concurrent read/write scenarios
refactor(auth): extract password verification to dedicated function
```

### Write Tests for Your Changes

Every new feature and bug fix should include tests:

- **New function** → unit test in the appropriate `test_*.py` file
- **New API endpoint** → test in `test_dashboard.py`
- **Bug fix** → regression test that would have caught the bug
- **New integration scenario** → test in `test_integration.py`

### Run Tests Before Committing

```bash
python -m pytest tests/ --ignore=tests/test_load.py -q
# All tests must pass
```

---

## Submitting a Pull Request

1. **Push your branch**:

   ```bash
   git push origin feat/my-new-feature
   ```

2. **Open a Pull Request** on GitHub against the `main` branch

3. **Fill in the PR template**:
   - What does this PR do?
   - Why is this change needed?
   - How was it tested?
   - Any breaking changes?

4. **PR checklist**:
   - [ ] All existing tests pass
   - [ ] New tests added for new functionality
   - [ ] Code is formatted (ruff/black)
   - [ ] Type hints are present
   - [ ] Docstrings are added/updated
   - [ ] `docs/` updated if user-facing behaviour changed
   - [ ] No secrets or credentials in the diff

5. A maintainer will review and merge, or request changes.

---

## Adding New Features

### Adding a New API Endpoint

1. Add the route in `src/dashboard.py`
2. Add auth dependency: `user: str = Depends(authenticate)`
3. Add tests in `tests/test_dashboard.py`
4. Document in `docs/api-reference.md`

### Adding a New Database Function

1. Add the function in `src/database.py`
2. Follow existing patterns (thread-local connection, log the operation)
3. Add unit tests in `tests/test_database.py`
4. Add integration test if the function is part of a workflow

### Adding a New CLI Script

1. Create `scripts/your_script.py`
2. Follow the pattern of `morning_report.py` or `health_check.py`:
   - Accept `--quiet` flag for cron usage
   - Print to stdout by default
   - Use `src/utils.py` helpers
3. Add tests if the script has testable logic

### Modifying the Database Schema

If you need to add or change a database table:

1. Update `init_db()` in `src/database.py`
2. Make schema changes additive where possible (`ADD COLUMN` rather than renames)
3. Test that `init_db()` is idempotent (safe to call on an existing database)
4. Update `docs/architecture.md` (Database Schema section)

---

## Reporting Issues

### Bug Reports

Open a GitHub Issue with:

- **Python version**: `python --version`
- **OS**: Ubuntu 22.04, Windows, macOS, etc.
- **Steps to reproduce**: numbered list
- **Expected behaviour**: what should happen
- **Actual behaviour**: what happens instead
- **Relevant logs**: `tail -50 /opt/claude-agent/logs/worker.log`
- **Error screenshots**: if a Playwright error, check `logs/screenshots/`

### Feature Requests

Open a GitHub Issue with:

- **Use case**: what problem does this solve?
- **Proposed solution**: how you imagine it working
- **Alternatives considered**: other approaches you thought about

---

## Documentation

All user-facing documentation lives in `docs/`. When you change behaviour, update the relevant doc file.

### Rebuilding the Docs Index

If you add a new doc file, add it to the table of contents in each related doc.

### Doc Writing Guidelines

- Use clear, imperative language: "Run `git pull`", not "You should run `git pull`"
- Code blocks for all commands, even one-liners
- Tables for structured data (options, fields, errors)
- Keep lines under 100 characters in Markdown source
- Link to related docs: `See [troubleshooting.md](troubleshooting.md)`

---

Thank you for contributing to Claude Nightcrawler!
