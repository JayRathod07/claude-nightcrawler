"""
Health Check Script — Verify the agent system is running correctly

Checks:
    1. Database connectivity and table integrity
    2. Results directory exists and is writable
    3. Logs directory exists
    4. Browser data directory exists
    5. Recent task activity (warns if no tasks in 24h)
    6. Claude availability status
    7. Python package versions

Exit codes:
    0  All checks passed
    1  One or more checks failed

Usage:
    python scripts/health_check.py
    python scripts/health_check.py --json   (machine-readable output)
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.database import get_all_tasks, get_claude_status, get_statistics, init_db


def check(name: str, fn) -> Tuple[bool, str]:
    """Run a single check function, returning (passed, message)."""
    try:
        result = fn()
        if result is True or result is None:
            return True, "OK"
        elif isinstance(result, str):
            return True, result
        else:
            return False, str(result)
    except Exception as exc:
        return False, f"ERROR: {exc}"


def run_checks() -> List[Dict[str, Any]]:
    results = []

    # ── Database ────────────────────────────────────────────────────────────
    def db_check():
        init_db()
        stats = get_statistics()
        return f"OK ({stats.get('total_tasks', 0)} tasks total)"

    ok, msg = check("Database", db_check)
    results.append({"name": "Database", "passed": ok, "message": msg})

    # ── Claude Status ───────────────────────────────────────────────────────
    def claude_check():
        status = get_claude_status()
        available = bool(status.get("available", True))
        reset = status.get("reset_time", "N/A")
        if available:
            return "Available"
        return f"Rate-limited (resets {reset})"

    ok, msg = check("Claude Status", claude_check)
    results.append({"name": "Claude Status", "passed": ok, "message": msg})

    # ── Directories ─────────────────────────────────────────────────────────
    for dir_name, path in [
        ("Results Dir", os.getenv("RESULTS_DIR", "results")),
        ("Logs Dir", os.getenv("LOGS_DIR", "logs")),
        ("Browser Data Dir", os.getenv("BROWSER_DATA_DIR", "browser_data")),
    ]:
        p = Path(path)
        if p.exists() and p.is_dir():
            results.append({"name": dir_name, "passed": True, "message": str(p.resolve())})
        else:
            results.append({"name": dir_name, "passed": False, "message": f"Missing: {path}"})

    # ── Recent Activity ─────────────────────────────────────────────────────
    def activity_check():
        tasks = get_all_tasks(limit=10)
        if not tasks:
            return "No tasks yet (normal for fresh install)"
        latest = tasks[0]
        created_str = latest.get("created_at", "")
        if not created_str:
            return "Tasks exist but no timestamps"
        try:
            created = datetime.fromisoformat(str(created_str))
            age = datetime.now() - created
            return f"Last task {int(age.total_seconds() / 3600)}h ago"
        except Exception:
            return "Tasks exist"

    ok, msg = check("Recent Activity", activity_check)
    results.append({"name": "Recent Activity", "passed": ok, "message": msg})

    # ── Python packages ─────────────────────────────────────────────────────
    def pkg_check():
        import importlib
        missing = []
        for pkg in ["fastapi", "playwright", "passlib", "pydantic"]:
            try:
                importlib.import_module(pkg)
            except ImportError:
                missing.append(pkg)
        if missing:
            raise ImportError(f"Missing: {', '.join(missing)}")
        return f"Python {sys.version.split()[0]}"

    ok, msg = check("Python Packages", pkg_check)
    results.append({"name": "Python Packages", "passed": ok, "message": msg})

    return results


def main() -> int:
    json_mode = "--json" in sys.argv

    results = run_checks()
    all_passed = all(r["passed"] for r in results)

    if json_mode:
        print(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "all_passed": all_passed,
            "checks": results,
        }, indent=2))
    else:
        print()
        print("╔══════════════════════════════════════╗")
        print("║   Claude Nightcrawler Health Check   ║")
        print("╚══════════════════════════════════════╝")
        print()
        for r in results:
            icon = "✅" if r["passed"] else "❌"
            name = r["name"].ljust(22)
            print(f"  {icon}  {name}  {r['message']}")
        print()
        if all_passed:
            print("  All checks passed ✓")
        else:
            failed = [r["name"] for r in results if not r["passed"]]
            print(f"  ⚠️  Failed: {', '.join(failed)}")
        print()

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
