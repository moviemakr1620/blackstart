from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session

ALGORITHM = "HS256"
TOKEN_HOURS = 24 * 7
PBKDF2_ITERATIONS = 600_000
LEGACY_PBKDF2_ITERATIONS = 200_000
bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    parts = (stored or "").split("$")
    if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
        _, iters, salt, digest = parts
        try:
            rounds = int(iters)
        except ValueError:
            return False
    elif len(parts) == 3 and parts[0] == "pbkdf2":
        _, salt, digest = parts
        rounds = LEGACY_PBKDF2_ITERATIONS
    else:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds)
    return hmac.compare_digest(dk.hex(), digest)


def password_needs_rehash(stored: str) -> bool:
    return not (stored or "").startswith(f"pbkdf2_sha256${PBKDF2_ITERATIONS}$")


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password(secrets.token_hex(8))


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check so unknown usernames are not revealed."""
    verify_password(password, _dummy_hash())


# ------------------------------------------------------------ login throttle


class LoginThrottle:
    """Small in-memory limiter: N failures per window per client+username."""

    def __init__(self, max_failures: int = 8, window_sec: float = 300.0) -> None:
        self.max_failures = max_failures
        self.window = window_sec
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._fails[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> float:
        now = time.monotonic()
        q = self._prune(key, now)
        if len(q) >= self.max_failures:
            return max(1.0, self.window - (now - q[0]))
        return 0.0

    def fail(self, key: str) -> None:
        now = time.monotonic()
        self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


login_throttle = LoginThrottle()


# ------------------------------------------------------------------- tokens


def create_token(username: str, version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "ver": int(version or 0),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=TOKEN_HOURS)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token_payload(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    if not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return payload


def decode_token(token: str) -> str:
    return str(decode_token_payload(token)["sub"])


async def user_from_token(session: AsyncSession, token: str):
    """Validate signature, expiry, that the user still exists, and token version."""
    from app.models import User

    payload = decode_token_payload(token)
    user = (
        await session.execute(select(User).where(User.username == str(payload["sub"])))
    ).scalar_one_or_none()
    if user is None or int(payload.get("ver", 0)) != int(user.token_version or 0):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is no longer valid")
    return user


async def get_current_user_any(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
):
    """Authenticated user, even if they still must change their password."""
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return await user_from_token(session, creds.credentials)


async def get_current_user(user=Depends(get_current_user_any)) -> str:
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password_change_required: change the admin password before using Blackstart.",
        )
    return user.username


def client_key(request: Request, username: str) -> str:
    host = request.client.host if request.client else "?"
    return f"{host}|{username.lower()}"


# --------------------------------------------------------- secret encryption
#
# v2 values look like "v2:<fernet token>", keyed by PBKDF2(secret_key).
# Older installs stored Fernet(SHA-256(secret_key)) tokens without a prefix,
# sometimes under a published default key. Those are still readable so the
# startup migration can re-encrypt them; nothing new is written that way.

V2_PREFIX = "v2:"
_V2_SALT = b"blackstart/credentials/v2"


class SecretDecryptError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _fernet_v2(key: str) -> Fernet:
    raw = hashlib.pbkdf2_hmac("sha256", key.encode(), _V2_SALT, 200_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(raw))


@lru_cache(maxsize=16)
def _fernet_legacy(key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return V2_PREFIX + _fernet_v2(settings.secret_key).encrypt(value.encode()).decode()


def _looks_like_fernet(value: str) -> bool:
    return value.startswith("gAAAAA") and len(value) >= 80


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith(V2_PREFIX):
        token = value[len(V2_PREFIX) :].encode()
        for key in [settings.secret_key, *settings.legacy_secret_keys]:
            try:
                return _fernet_v2(key).decrypt(token).decode()
            except InvalidToken:
                continue
        raise SecretDecryptError(
            "A stored credential could not be decrypted (the secret key changed). Re-enter it in Blackstart."
        )
    if _looks_like_fernet(value):
        for key in [settings.secret_key, *settings.legacy_secret_keys]:
            try:
                return _fernet_legacy(key).decrypt(value.encode()).decode()
            except InvalidToken:
                continue
        raise SecretDecryptError(
            "A stored credential could not be decrypted (the secret key changed). Re-enter it in Blackstart."
        )
    # Very old installs may hold plain text; the startup migration encrypts it.
    return value


def secret_needs_migration(value: str) -> bool:
    return bool(value) and not value.startswith(V2_PREFIX)


def encrypt_json(data: dict[str, Any] | None) -> str:
    return encrypt_secret(json.dumps(data or {}))


def decrypt_json(value: str) -> dict[str, Any]:
    raw = decrypt_secret(value) if value else "{}"
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}
