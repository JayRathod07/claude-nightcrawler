"""
Utility helpers used across the project.
"""
import hashlib
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def format_duration(seconds: float) -> str:
    """Convert a duration in seconds to a human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    else:
        return f"{seconds / 3600:.1f}h"


def truncate(text: str, max_len: int = 100, suffix: str = "…") -> str:
    """Truncate a string to max_len characters, appending suffix if cut."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def sanitise_filename(name: str) -> str:
    """Strip characters unsafe for filenames."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()


def ensure_dirs(*paths: str) -> None:
    """Create directories if they don't exist."""
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    """Return the current UTC-local datetime as an ISO 8601 string."""
    return datetime.now().isoformat()


def file_size_human(path: str) -> str:
    """Return human-readable file size for the given path."""
    try:
        size = Path(path).stat().st_size
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    except OSError:
        return "unknown"


def mask_secret(value: str, visible: int = 4) -> str:
    """Mask a secret string, showing only the first `visible` chars."""
    if not value:
        return "(not set)"
    return value[:visible] + "*" * max(0, len(value) - visible)
