"""
Authentication utilities for the FastAPI dashboard.

Uses HTTP Basic Auth with bcrypt password hashing.
The admin username and password are loaded from environment variables.
"""
import logging
import os
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

# ─── Password Hashing ──────────────────────────────────────────────────────────
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ─── Config ────────────────────────────────────────────────────────────────────
ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "changeme")

# ─── FastAPI Security Scheme ───────────────────────────────────────────────────
security = HTTPBasic()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    return _pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    """Hash a plain password with bcrypt."""
    return _pwd_context.hash(plain)


def _is_bcrypt(value: str) -> bool:
    """Return True if the value looks like a bcrypt hash."""
    return value.startswith("$2b$") or value.startswith("$2a$")


def authenticate(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """
    FastAPI dependency — validates HTTP Basic credentials.

    Compares submitted username/password against ADMIN_USERNAME / ADMIN_PASSWORD.
    The password in .env can be either plain-text or a bcrypt hash.

    Returns:
        The authenticated username string on success.

    Raises:
        HTTPException 401: On invalid credentials.
    """
    # Username check (constant-time to prevent timing attacks)
    correct_user = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        ADMIN_USERNAME.encode("utf-8"),
    )

    # Password check
    if _is_bcrypt(ADMIN_PASSWORD):
        correct_pass = _pwd_context.verify(credentials.password, ADMIN_PASSWORD)
    else:
        correct_pass = secrets.compare_digest(
            credentials.password.encode("utf-8"),
            ADMIN_PASSWORD.encode("utf-8"),
        )

    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username
