"""
Morning Report — Daily summary of agent activity

Generates and optionally sends a comprehensive daily summary including:
    • Tasks completed / failed in the last 24 hours
    • Current queue state
    • Average completion time
    • Claude availability status
    • Disk usage warning
    • Failed task details (up to 5)

Usage:
    python scripts/morning_report.py               # print only
    python scripts/morning_report.py --send        # print + send via Telegram
    python scripts/morning_report.py --send --quiet  # send only, no stdout

Schedule via cron at 08:00 daily:
    0 8 * * * /opt/claude-agent/venv/bin/python /opt/claude-agent/scripts/morning_report.py --send >> /opt/claude-agent/logs/morning_report.log 2>&1
"""

import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.database import get_all_tasks, get_claude_status, get_statistics
from src.notifier import Notifier


# ── Helpers ──────────────────────────────────────────────────────────────────

def _disk_usage_str(path: str = "/") -> tuple[str, bool]:
    """
    Return (human-readable disk usage string, is_warning).
    Warning threshold: > 85% used.
    Falls back gracefully on Windows / permission errors.
    """
    try:
        usage = shutil.disk_usage(path)
        used_pct = usage.used / usage.total * 100
        used_gb = usage.used / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        label = f"{used_gb:.1f} GB / {total_gb:.1f} GB ({used_pct:.0f}% used, {free_gb:.1f} GB free)"
        return label, used_pct > 85
    except Exception:
        return "N/A", False


def _results_dir_size() -> str:
    """Return human-readable size of the results/ directory."""
    try:
        results_path = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
        total = sum(f.stat().st_size for f in results_path.rglob("*") if f.is_file())
        return f"{total / (1024 ** 2):.1f} MB ({sum(1 for _ in results_path.rglob('*.md'))} files)"
    except Exception:
        return "N/A"


# ── Report builder ────────────────────────────────────────────────────────────

def build_report() -> str:
    """Build a full morning report as a plain/Markdown string."""
    stats = get_statistics()
    claude_status = get_claude_status()
    all_tasks = get_all_tasks(limit=500)

    now = datetime.now()
    yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def completed_after(t: dict, since: datetime) -> bool:
        ts = t.get("completed_at")
        if not ts:
            return False
        try:
            return datetime.fromisoformat(str(ts)) >= since
        except ValueError:
            return False

    # Tasks from last 24 hours
    recent_done = [t for t in all_tasks if t.get("status") == "completed" and completed_after(t, yesterday_start)]
    recent_failed = [t for t in all_tasks if t.get("status") == "failed" and completed_after(t, yesterday_start)]

    counts = stats.get("task_counts", {})
    queued = counts.get("queued", 0)
    running = counts.get("running", 0)
    waiting = counts.get("waiting_limit", 0)
    avg_min = stats.get("avg_completion_minutes", 0) or 0

    available = bool(claude_status.get("available", True))
    reset_time = claude_status.get("reset_time") or "N/A"
    requests_today = claude_status.get("total_requests_today", 0)

    disk_str, disk_warn = _disk_usage_str(str(ROOT))
    results_size = _results_dir_size()

    # ── Build message ────────────────────────────────────────────────────────
    lines = [
        f"🌅 *Claude Nightcrawler — Morning Report*",
        f"📅 {now.strftime('%A, %d %B %Y at %H:%M')}",
        "",
        "*📊 Last 24 Hours*",
        f"  ✅ Completed:     {len(recent_done)}",
        f"  ❌ Failed:        {len(recent_failed)}",
        f"  ⏱ Avg time:      {avg_min:.1f} min",
        f"  📡 API requests:  {requests_today}",
        "",
        "*📋 Current Queue*",
        f"  🕐 Queued:    {queued}",
        f"  🔄 Running:   {running}",
        f"  ⏸ Waiting:   {waiting}",
        "",
        "*🤖 Claude Status*",
        f"  {'🟢 Available' if available else f'🔴 Rate-limited — resets {str(reset_time)[:19]}'}",
        "",
        "*💾 Storage*",
        f"  {'⚠️ ' if disk_warn else ''}Disk:    {disk_str}",
        f"  Results: {results_size}",
        "",
        f"*📈 All-time*: {stats.get('total_tasks', 0)} total tasks",
    ]

    if recent_failed:
        lines += ["", "*❌ Failed Tasks (last 24h)*"]
        for t in recent_failed[:5]:
            err = str(t.get("error_message", "unknown error"))[:70]
            lines.append(f"  • Task {t['id']}: {err}")

    if disk_warn:
        lines += ["", "⚠️ *Disk space is above 85% — consider cleaning old results/logs*"]

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    send_flag = "--send" in sys.argv
    quiet_flag = "--quiet" in sys.argv

    report = build_report()

    if not quiet_flag:
        # Print plain-text version (strip Markdown asterisks for readability)
        print(report.replace("*", "").replace("_", ""))
        print()

    if send_flag:
        notifier = Notifier()
        sent = notifier.notify_daily_summary(report)
        if not quiet_flag:
            if sent:
                print("Report sent via Telegram ✓")
            else:
                print("Telegram not configured or disabled — report not sent.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
