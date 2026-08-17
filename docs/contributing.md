# Contributing — Claude Nightcrawler

Thank you for your interest in contributing! This guide covers how to set up a development environment, coding standards, and the pull request process.

---

## Development Setup

### 1. Fork & Clone

```bash
git clone https://github.com/JayRathod07/claude-nightcrawler.git
cd claude-nightcrawler
```

### 2. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate    # Linux/macOS
.\venv\Scripts\Activate     # Windows
pip install -r requirements.txt
pip install -r requirements-dev.txt   # if present
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env: set ADMIN_USERNAME, ADMIN_PASSWORD (test values are fine locally)
```

### 4. Run Tests

```bash
pytest tests/ -v
```

All 166 tests must pass before submitting a PR.

---

## Code Standards

### Python Style
- Format with **Black**: `black src/ tests/ scripts/`
- Lint with **Ruff**: `ruff check src/`
- Type hints on all public functions
- Docstrings (Google style) on all modules and public functions

### Commit Messages
Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Telegram notification on task completion
fix: handle reset time in 12-hour format
docs: update API reference for /api/stats endpoint
test: add unit tests for _extract_reset_time
refactor: extract status label logic to utils
chore: bump playwright to 1.45.0
```

### Branch Naming

```
feat/telegram-notifications
fix/reset-time-parsing
docs/contributing-guide
```

---

## Testing Requirements

- All new features **must** include unit tests in `tests/`
- Aim for coverage of happy path + at least one error path
- Tests must not require network access (mock Claude/Playwright)
- Run `pytest tests/ -v --tb=short` before pushing

---

## Pull Request Process

1. **Create a branch** from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```

2. **Make your changes** with clear commit messages

3. **Run the full test suite**:
   ```bash
   pytest tests/ -v
   ```

4. **Push and open a PR** targeting `main`:
   ```bash
   git push origin feat/my-feature
   ```

5. **PR template** — fill in:
   - Summary of changes
   - How to test
   - Any breaking changes

6. **Review** — at least one approval required before merge

---

## Areas Open for Contribution

| Area | Description |
|---|---|
| 🔔 Notifications | Additional notification backends (Slack, Discord, email) |
| 🌐 Playwright | More robust response extraction selectors |
| 📊 Analytics | Task history charts and statistics |
| 🔒 Auth | Multi-user support / OAuth integration |
| 🐳 Docker | Dockerfile and docker-compose.yml |
| 📱 Mobile | PWA manifest and service worker for offline support |
| 🧪 Tests | Integration tests with mocked Playwright |
| 📖 Docs | Translations, tutorials, video walkthroughs |

---

## Reporting Issues

Please use [GitHub Issues](https://github.com/JayRathod07/claude-nightcrawler/issues) and include:

- OS and Python version
- Full error message and traceback
- Contents of relevant log files
- Steps to reproduce

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
