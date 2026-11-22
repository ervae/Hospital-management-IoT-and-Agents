"""Parola, JWT ve API anahtarı yardımcıları."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings

ALGO = "HS256"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_token(user_id: str, role: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.token_minutes)
    return jwt.encode({"sub": user_id, "role": role, "exp": exp}, settings.secret_key, algorithm=ALGO)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGO])
    except jwt.PyJWTError:
        return None


def new_api_key(prefix: str) -> str:
    """Cihaz/agent anahtarı. Sadece oluşturulurken bir kez gösterilir; veritabanında özeti tutulur."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_matches(key: str, hashed: str) -> bool:
    return bool(hashed) and hmac.compare_digest(hash_key(key), hashed)
