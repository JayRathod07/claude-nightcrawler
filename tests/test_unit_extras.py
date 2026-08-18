"""
Unit tests for src/auth.py — HTTP Basic Auth module

Covers:
    • verify_password / hash_password round-trip
    • _is_bcrypt detection
    • authenticate() with plain-text password (timing-safe)
    • authenticate() with pre-hashed bcrypt password
    • authenticate() rejection on wrong username / password
    • authenticate() raises 401 with correct headers
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _creds(username: str, password: str) -> HTTPBasicCredentials:
    return HTTPBasicCredentials(username=username, password=password)


# ══════════════════════════════════════════════════════════════════════════════
class TestPasswordHashing:

    def test_hash_returns_bcrypt_string(self):
        from src.auth import hash_password
        h = hash_password("secret")
        assert h.startswith("$2b$") or h.startswith("$2a$")

    def test_verify_correct_password(self):
        from src.auth import hash_password, verify_password
        h = hash_password("my_password")
        assert verify_password("my_password", h) is True

    def test_verify_wrong_password_fails(self):
        from src.auth import hash_password, verify_password
        h = hash_password("my_password")
        assert verify_password("wrong_password", h) is False

    def test_hash_is_non_deterministic(self):
        """bcrypt should produce different hashes each call (random salt)."""
        from src.auth import hash_password
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2

    def test_empty_password_can_be_hashed(self):
        from src.auth import hash_password, verify_password
        h = hash_password("")
        assert verify_password("", h) is True


# ══════════════════════════════════════════════════════════════════════════════
class TestIsBcrypt:

    def test_recognises_2b_prefix(self):
        from src.auth import _is_bcrypt
        assert _is_bcrypt("$2b$12$abcdef") is True

    def test_recognises_2a_prefix(self):
        from src.auth import _is_bcrypt
        assert _is_bcrypt("$2a$10$abcdef") is True

    def test_plain_text_is_not_bcrypt(self):
        from src.auth import _is_bcrypt
        assert _is_bcrypt("changeme") is False

    def test_empty_string_is_not_bcrypt(self):
        from src.auth import _is_bcrypt
        assert _is_bcrypt("") is False

    def test_sha256_hash_not_bcrypt(self):
        from src.auth import _is_bcrypt
        assert _is_bcrypt("$5$rounds=10000$example") is False


# ══════════════════════════════════════════════════════════════════════════════
class TestAuthenticatePlainText:

    def setup_method(self):
        """Reload auth module with test credentials."""
        import importlib
        import src.auth as auth_mod
        with patch.dict(os.environ, {
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "testpass123",
        }):
            importlib.reload(auth_mod)
        self.auth = auth_mod

    def test_correct_credentials_return_username(self):
        with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "testpass123"}):
            import importlib, src.auth as m; importlib.reload(m)
            result = m.authenticate(_creds("admin", "testpass123"))
        assert result == "admin"

    def test_wrong_password_raises_401(self):
        with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "testpass123"}):
            import importlib, src.auth as m; importlib.reload(m)
            with pytest.raises(HTTPException) as exc_info:
                m.authenticate(_creds("admin", "wrongpass"))
        assert exc_info.value.status_code == 401

    def test_wrong_username_raises_401(self):
        with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "testpass123"}):
            import importlib, src.auth as m; importlib.reload(m)
            with pytest.raises(HTTPException) as exc_info:
                m.authenticate(_creds("hacker", "testpass123"))
        assert exc_info.value.status_code == 401

    def test_empty_credentials_raise_401(self):
        with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "testpass123"}):
            import importlib, src.auth as m; importlib.reload(m)
            with pytest.raises(HTTPException):
                m.authenticate(_creds("", ""))

    def test_401_includes_www_authenticate_header(self):
        with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "testpass123"}):
            import importlib, src.auth as m; importlib.reload(m)
            with pytest.raises(HTTPException) as exc_info:
                m.authenticate(_creds("admin", "wrong"))
        assert "WWW-Authenticate" in exc_info.value.headers

    def test_case_sensitive_username(self):
        with patch.dict(os.environ, {"ADMIN_USERNAME": "Admin", "ADMIN_PASSWORD": "pass"}):
            import importlib, src.auth as m; importlib.reload(m)
            with pytest.raises(HTTPException):
                m.authenticate(_creds("admin", "pass"))  # lowercase 'admin' != 'Admin'


# ══════════════════════════════════════════════════════════════════════════════
class TestAuthenticateBcryptPassword:

    def test_bcrypt_hash_accepted(self):
        from src.auth import hash_password
        hashed = hash_password("supersecret")
        with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": hashed}):
            import importlib, src.auth as m; importlib.reload(m)
            result = m.authenticate(_creds("admin", "supersecret"))
        assert result == "admin"

    def test_wrong_plain_against_bcrypt_raises_401(self):
        from src.auth import hash_password
        hashed = hash_password("supersecret")
        with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": hashed}):
            import importlib, src.auth as m; importlib.reload(m)
            with pytest.raises(HTTPException) as exc_info:
                m.authenticate(_creds("admin", "notthesecret"))
        assert exc_info.value.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
class TestUtils:
    """Unit tests for src/utils.py helpers."""

    def test_format_duration_seconds(self):
        from src.utils import format_duration
        assert format_duration(45) == "45s"

    def test_format_duration_minutes(self):
        from src.utils import format_duration
        assert "m" in format_duration(90)

    def test_format_duration_hours(self):
        from src.utils import format_duration
        assert "h" in format_duration(7200)

    def test_truncate_short_string_unchanged(self):
        from src.utils import truncate
        assert truncate("hello", 100) == "hello"

    def test_truncate_long_string(self):
        from src.utils import truncate
        result = truncate("a" * 200, 50)
        assert len(result) <= 50
        assert result.endswith("…")

    def test_truncate_exact_max_unchanged(self):
        from src.utils import truncate
        text = "x" * 100
        assert truncate(text, 100) == text

    def test_sanitise_filename_removes_illegal_chars(self):
        from src.utils import sanitise_filename
        result = sanitise_filename('file<name>/with:bad*chars')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert "*" not in result

    def test_sanitise_filename_preserves_normal_chars(self):
        from src.utils import sanitise_filename
        assert sanitise_filename("my_file-v2.md") == "my_file-v2.md"

    def test_ensure_dirs_creates_directories(self, tmp_path):
        from src.utils import ensure_dirs
        new_dir = str(tmp_path / "a" / "b" / "c")
        ensure_dirs(new_dir)
        assert Path(new_dir).is_dir()

    def test_ensure_dirs_idempotent(self, tmp_path):
        from src.utils import ensure_dirs
        d = str(tmp_path / "test_dir")
        ensure_dirs(d)
        ensure_dirs(d)  # second call should not raise
        assert Path(d).is_dir()

    def test_now_iso_is_string(self):
        from src.utils import now_iso
        result = now_iso()
        assert isinstance(result, str)
        assert "T" in result  # ISO 8601 format

    def test_file_size_human_bytes(self, tmp_path):
        from src.utils import file_size_human
        f = tmp_path / "test.txt"
        f.write_bytes(b"x" * 500)
        result = file_size_human(str(f))
        assert "B" in result

    def test_file_size_human_kilobytes(self, tmp_path):
        from src.utils import file_size_human
        f = tmp_path / "test.txt"
        f.write_bytes(b"x" * 2048)
        result = file_size_human(str(f))
        assert "KB" in result

    def test_file_size_human_missing_file(self):
        from src.utils import file_size_human
        result = file_size_human("/nonexistent/path/file.txt")
        assert result == "unknown"

    def test_mask_secret_hides_middle(self):
        from src.utils import mask_secret
        result = mask_secret("supersecrettoken", visible=4)
        assert result.startswith("supe")
        assert "*" in result

    def test_mask_secret_empty_string(self):
        from src.utils import mask_secret
        assert mask_secret("") == "(not set)"

    def test_mask_secret_short_value(self):
        from src.utils import mask_secret
        result = mask_secret("ab", visible=4)
        assert "ab" in result
