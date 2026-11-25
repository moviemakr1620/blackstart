from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import KNOWN_DEFAULT_PASSWORDS, settings
from app.db import get_session
from app.models import AuditLog, User
from app.schemas import LoginIn, PasswordIn, TokenOut, UserOut
from app.security import (
    burn_password_check,
    client_key,
    create_token,
    get_current_user_any,
    hash_password,
    login_throttle,
    password_needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, session: AsyncSession = Depends(get_session)) -> TokenOut:
    key = client_key(request, body.username)
    wait = login_throttle.blocked(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed sign-ins. Try again in {int(wait)} seconds.",
            headers={"Retry-After": str(int(wait))},
        )
    user = (await session.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if user is None:
        burn_password_check(body.password)
        ok = False
    else:
        ok = verify_password(body.password, user.password_hash)
    if not ok:
        login_throttle.fail(key)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    login_throttle.reset(key)
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        await session.commit()
    return TokenOut(
        token=create_token(user.username, user.token_version or 0),
        username=user.username,
        must_change_password=bool(user.must_change_password),
    )


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user_any)) -> UserOut:
    return UserOut(username=user.username, must_change_password=bool(user.must_change_password))


@router.post("/password", response_model=TokenOut)
async def change_password(
    body: PasswordIn,
    user: User = Depends(get_current_user_any),
    session: AsyncSession = Depends(get_session),
) -> TokenOut:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is wrong")
    if len(body.new_password) < 10:
        raise HTTPException(status_code=400, detail="New password must be at least 10 characters")
    if body.new_password in KNOWN_DEFAULT_PASSWORDS or body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="Pick a new password that is not a default or your old one")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # Bump the version so every other session signed in with the old password ends.
    user.token_version = int(user.token_version or 0) + 1
    session.add(AuditLog(actor=user.username, action="auth.password_changed", detail=""))
    await session.commit()
    try:
        settings.initial_password_file.unlink(missing_ok=True)
    except OSError:
        pass
    return TokenOut(token=create_token(user.username, user.token_version), username=user.username)
