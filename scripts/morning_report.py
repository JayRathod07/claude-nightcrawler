"""
Morning Report — Daily summary of agent activity

Prints (or sends via Telegram) a summary of:
    • Tasks completed yesterday
    • Tasks queued / pending
    • Average completion time
    • Any failed tasks
    • Claude availability status

Usage:
    python scripts/morning_report.py
    python scripts/morning_report.py --send   (also send via Telegram)

Schedule via cron for 8 AM daily:
    0 8 * * * /opt/claude-agent/venv/bin/python /opt/claude-agent/scripts/morning_report.py --send
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.database import get_all_tasks, get_claude_status, get_statistics
from src.notifier import Notifier


def build_report() -> str:
    stats = get_statistics()
    claude_status = get_claude_status()
    all_tasks = get_all_tasks(limit=500)

    now = datetime.now()
    yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0)

    yesterday_done = [
        t for t in all_tasks
        if t.get("status") == "completed"
        and t.get("completed_at")
        and datetime.fromisoformat(str(t["completed_at"])) >= yesterday_start
    ]
    yesterday_failed = [
        t for t in all_tasks
        if t.get("status") == "failed"
        and t.get("completed_at")
        and datetime.fromisoformat(str(t["completed_at"])) >= yesterday_start
    ]

    counts = stats.get("task_counts", {})
    queued = counts.get("queued", 0)
    running = counts.get("running", 0)
    waiting = counts.get("waiting_limit", 0)
    avg_min = stats.get("avg_completion_minutes", 0)

    available = bool(claude_status.get("available", True))
    reset_time = claude_status.get("reset_time", "N/A")

    lines = [
        f"🌅 *Claude Nightcrawler — Morning Report*",
        f"📅 {now.strftime('%A, %d %B %Y')}",
        "",
        f"*Yesterday's Activity*",
        f"  ✅ Completed: {len(yesterday_done)}",
        f"  ❌ Failed:    {len(yesterday_failed)}",
        f"  ⏱ Avg time:  {avg_min:.1f} min",
        "",
        f"*Current Queue*",
        f"  📋 Queued:    {queued}",
        f"  🔄 Running:   {running}",
        f"  ⏸ Waiting:   {waiting}",
        "",
        f"*Claude Status*",
        f"  {'🟢 Available' if available else f'🔴 Rate-limited (resets {reset_time})'}",
        "",
        f"*All-time Total*: {stats.get('total_tasks', 0)} tasks",
    ]

    if yesterday_failed:
        lines += ["", "*Failed Tasks Yesterday*"]
        for t in yesterday_failed[:5]:
            lines.append(f"  • Task {t['id']}: {str(t.get('error_message', ''))[:60]}")

    return "\n".join(lines)


def main() -> int:
    report = build_report()

    # Always print
    print(report.replace("*", "").replace("_", ""))

    # Optionally send via Telegram
    if "--send" in sys.argv:
        notifier = Notifier()
        sent = notifier._send(report)
        if sent:
            print("\nReport sent via Telegram ✓")
        else:
            print("\nTelegram not configured or disabled.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
