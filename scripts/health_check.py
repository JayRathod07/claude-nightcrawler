"""
Health Check Script — Verify the Claude Nightcrawler system is healthy

Checks:
    1.  Database connectivity and integrity
    2.  Claude availability status
    3.  Results directory — exists and writable
    4.  Logs directory — exists
    5.  Browser data directory — exists
    6.  Disk space — warns if > 85% full, fails if > 95%
    7.  Required Python packages — all importable
    8.  Dashboard HTTP endpoint — responds with 200
    9.  systemd service status — claude-agent and claude-dashboard running
    10. Recent task activity — warns if no activity in 24 hours

Exit codes:
    0  All checks passed
    1  One or more checks FAILED

Flags:
    --json    Machine-readable JSON output
    --alert   Also send a Telegram alert if any check fails
    --quiet   Suppress stdout (useful with --alert in cron)

Usage:
    python scripts/health_check.py
    python scripts/health_check.py --json
    python scripts/health_check.py --alert          # alert on failure
    python scripts/health_check.py --alert --quiet  # silent, alert only

Schedule via cron every 30 minutes:
    */30 * * * * /opt/claude-agent/venv/bin/python /opt/claude-agent/scripts/health_check.py --alert --quiet >> /opt/claude-agent/logs/health.log 2>&1
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.database import get_all_tasks, get_claude_status, get_statistics, init_db

# ── Config ────────────────────────────────────────────────────────────────────
DASHBOARD_URL = os.getenv("HEALTH_CHECK_URL", "http://127.0.0.1:8000/health")
DISK_WARN_PCT = 85.0
DISK_FAIL_PCT = 95.0
INACTIVITY_WARN_HOURS = 24


# ── Check runner ──────────────────────────────────────────────────────────────

def check(name: str, fn) -> Tuple[bool, str, str]:
    """
    Run a single check function, returning (passed, level, message).
    level is 'ok', 'warn', or 'fail'.
    """
    try:
        result = fn()
        if result is True or result is None:
            return True, "ok", "OK"
        elif isinstance(result, str):
            return True, "ok", result
        elif isinstance(result, tuple) and len(result) == 2:
            # (passed, message) or (level, message)
            passed, msg = result
            if isinstance(passed, bool):
                return passed, ("ok" if passed else "fail"), str(msg)
            else:
                # (level, message) form e.g. ('warn', 'Disk at 87%')
                return passed != "fail", passed, str(msg)
        else:
            return False, "fail", str(result)
    except Exception as exc:
        return False, "fail", f"ERROR: {exc}"


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_database() -> str:
    init_db()
    stats = get_statistics()
    total = stats.get("total_tasks", 0)
    return f"OK — {total} tasks total"


def _check_claude_status() -> str:
    status = get_claude_status()
    available = bool(status.get("available", True))
    if available:
        return "Available"
    reset = str(status.get("reset_time", "unknown"))[:19]
    return f"Rate-limited — resets {reset}"


def _check_directory(path: str, must_be_writable: bool = False) -> Tuple[bool, str]:
    p = Path(path)
    if not p.exists():
        return False, f"Missing: {path}"
    if not p.is_dir():
        return False, f"Not a directory: {path}"
    if must_be_writable:
        test_file = p / ".write_test"
        try:
            test_file.touch()
            test_file.unlink()
        except OSError:
            return False, f"Not writable: {path}"
    return True, str(p.resolve())


def _check_disk() -> Tuple[str, str]:
    """Returns (level, message)."""
    try:
        install_dir = str(ROOT)
        # On Windows use the drive; on Linux use the mount point
        usage = shutil.disk_usage(install_dir)
        pct = usage.used / usage.total * 100
        free_gb = usage.free / (1024 ** 3)
        msg = f"{pct:.1f}% used, {free_gb:.1f} GB free"
        if pct >= DISK_FAIL_PCT:
            return "fail", f"CRITICAL: {msg}"
        elif pct >= DISK_WARN_PCT:
            return "warn", f"WARNING: {msg}"
        return "ok", msg
    except Exception as exc:
        return "warn", f"Could not check disk: {exc}"


def _check_packages() -> str:
    import importlib
    missing = []
    for pkg in ["fastapi", "playwright", "passlib", "pydantic", "uvicorn", "requests"]:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        raise ImportError(f"Missing packages: {', '.join(missing)}")
    return f"Python {sys.version.split()[0]} — all packages present"


def _check_dashboard_http() -> Tuple[bool, str]:
    """Check that the dashboard /health endpoint responds."""
    try:
        import requests as req
        resp = req.get(DASHBOARD_URL, timeout=5)
        if resp.status_code == 200:
            return True, f"HTTP {resp.status_code} — dashboard responding"
        return False, f"HTTP {resp.status_code} — unexpected status"
    except Exception as exc:
        return False, f"Could not connect: {exc}"


def _check_systemd_service(service: str) -> Tuple[bool, str]:
    """Check a systemd service is active. No-op on non-Linux."""
    if sys.platform != "linux":
        return True, "N/A (not Linux)"
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=5
        )
        state = result.stdout.strip()
        if state == "active":
            return True, f"active"
        return False, f"state={state or 'unknown'}"
    except FileNotFoundError:
        return True, "systemctl not available"
    except Exception as exc:
        return False, f"Error: {exc}"


def _check_recent_activity() -> Tuple[str, str]:
    """Returns (level, message) — warn if no activity in INACTIVITY_WARN_HOURS."""
    try:
        tasks = get_all_tasks(limit=5)
        if not tasks:
            return "ok", "No tasks yet — fresh install"
        latest = tasks[0]
        created_str = latest.get("created_at", "")
        if not created_str:
            return "ok", "Tasks exist"
        created = datetime.fromisoformat(str(created_str))
        age_h = (datetime.now() - created).total_seconds() / 3600
        if age_h > INACTIVITY_WARN_HOURS:
            return "warn", f"Last task {age_h:.0f}h ago — no activity in {INACTIVITY_WARN_HOURS}h"
        return "ok", f"Last task {age_h:.1f}h ago"
    except Exception as exc:
        return "warn", f"Could not check activity: {exc}"


# ── Run all checks ────────────────────────────────────────────────────────────

def run_checks() -> List[Dict[str, Any]]:
    results = []

    def add(name: str, fn, warn_is_fail: bool = False):
        passed, level, msg = check(name, fn)
        # Upgrade 'warn' to displayed warning but not a hard failure
        results.append({
            "name": name,
            "passed": passed or (level == "warn" and not warn_is_fail),
            "level": level,
            "message": msg,
        })

    # 1. Database
    add("Database", _check_database)

    # 2. Claude status (always "passes" — just informational)
    add("Claude Status", _check_claude_status)

    # 3. Directories
    results_dir = os.getenv("RESULTS_DIR", str(ROOT / "results"))
    logs_dir = os.getenv("LOGS_DIR", str(ROOT / "logs"))
    browser_dir = os.getenv("BROWSER_DATA_DIR", str(ROOT / "browser_data"))

    for dir_name, path, must_write in [
        ("Results Dir", results_dir, True),
        ("Logs Dir", logs_dir, False),
        ("Browser Data Dir", browser_dir, False),
    ]:
        def _dir_check(p=path, w=must_write):
            return _check_directory(p, w)
        add(dir_name, _dir_check)

    # 4. Disk space
    def _disk_check():
        level, msg = _check_disk()
        return level, msg
    add("Disk Space", _disk_check)

    # 5. Python packages
    add("Python Packages", _check_packages)

    # 6. Dashboard HTTP (informational, doesn't fail health)
    add("Dashboard HTTP", _check_dashboard_http)

    # 7. systemd services
    for svc in ["claude-agent", "claude-dashboard"]:
        def _svc_check(s=svc):
            return _check_systemd_service(s)
        add(f"Service: {svc}", _svc_check)

    # 8. Recent activity (warn only)
    def _activity_check():
        return _check_recent_activity()
    add("Recent Activity", _activity_check, warn_is_fail=False)

    return results


# ── Output formatters ─────────────────────────────────────────────────────────

def _level_icon(level: str, passed: bool) -> str:
    if level == "warn":
        return "⚠️ "
    return "✅" if passed else "❌"


def _print_results(results: List[Dict[str, Any]]) -> None:
    print()
    print("╔══════════════════════════════════════════╗")
    print("║   Claude Nightcrawler — Health Check     ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝")
    print()

    for r in results:
        icon = _level_icon(r["level"], r["passed"])
        name = r["name"].ljust(24)
        print(f"  {icon}  {name}  {r['message']}")

    print()
    all_passed = all(r["passed"] for r in results)
    if all_passed:
        print("  All checks passed ✓")
    else:
        failed = [r["name"] for r in results if not r["passed"]]
        print(f"  ⚠️  Issues found: {', '.join(failed)}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    json_mode = "--json" in sys.argv
    alert_mode = "--alert" in sys.argv
    quiet_mode = "--quiet" in sys.argv

    results = run_checks()
    all_passed = all(r["passed"] for r in results)
    ts = datetime.now().isoformat()

    if json_mode:
        print(json.dumps({
            "timestamp": ts,
            "all_passed": all_passed,
            "checks": results,
        }, indent=2))
    elif not quiet_mode:
        _print_results(results)

    # Send Telegram alert if any check failed
    if alert_mode and not all_passed:
        try:
            from src.notifier import Notifier
            failed = [r for r in results if not r["passed"]]
            names = [r["name"] for r in failed]
            details = [r["message"] for r in failed]
            notifier = Notifier()
            sent = notifier.notify_health_alert(names, details)
            if not quiet_mode:
                if sent:
                    print("Health alert sent via Telegram ✓")
                else:
                    print("Telegram not configured — alert not sent.")
        except Exception as exc:
            if not quiet_mode:
                print(f"Failed to send Telegram alert: {exc}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
