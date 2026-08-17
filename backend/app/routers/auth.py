from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.schemas import LoginIn, PasswordIn, TokenOut, UserOut
from app.security import create_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    user = (await session.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return TokenOut(token=create_token(user.username), username=user.username)


@router.get("/me", response_model=UserOut)
async def me(username: str = Depends(get_current_user)) -> UserOut:
    return UserOut(username=username)


@router.post("/password")
async def change_password(
    body: PasswordIn,
    username: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    user = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if user is None or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is wrong")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    user.password_hash = hash_password(body.new_password)
    await session.commit()
    return {"ok": "password changed"}
