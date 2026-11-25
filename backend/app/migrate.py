"""One-time upgrades for existing databases. Safe to run on every start."""

from __future__ import annotations

import json
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import KNOWN_DEFAULT_PASSWORDS
from app.models import Host, NotifyChannel, Setting, UpsSource, User
from app.security import (
    SecretDecryptError,
    decrypt_secret,
    encrypt_secret,
    secret_needs_migration,
    verify_password,
)
from app.store import RETIRED_KEYS

log = logging.getLogger("blackstart.migrate")


def _reencrypt(value: str, what: str) -> str:
    if not secret_needs_migration(value):
        return value
    try:
        return encrypt_secret(decrypt_secret(value))
    except SecretDecryptError:
        log.error("Could not decrypt %s; leaving it as is. Re-enter it in the UI.", what)
        return value


async def _legacy_notify_settings(session: AsyncSession) -> None:
    rows = {r.key: r for r in (await session.execute(select(Setting).where(Setting.key.in_(RETIRED_KEYS)))).scalars()}
    if not rows:
        return

    def val(key: str) -> str:
        row = rows.get(key)
        if row is None:
            return ""
        try:
            v = json.loads(row.value_json)
        except json.JSONDecodeError:
            v = row.value_json
        return "" if v is None else str(v)

    url, kind = val("notify_url"), val("notify_kind") or "webhook"
    if url and kind != "none":
        channels = (await session.execute(select(NotifyChannel))).scalars().all()
        already = False
        for ch in channels:
            try:
                existing = decrypt_secret(ch.url_enc) if ch.url_enc else ch.url
            except SecretDecryptError:
                existing = ""
            already = already or existing == url
        if not already:
            # Before this release the Settings URL was only used when no channel
            # was enabled, so keep that meaning.
            any_enabled = any(c.enabled for c in channels)
            token = val("gotify_token")
            session.add(
                NotifyChannel(
                    name=f"{kind.title()} (from Settings)",
                    kind=kind if kind in {"discord", "ntfy", "gotify", "webhook"} else "webhook",
                    enabled=not any_enabled,
                    url="",
                    url_enc=encrypt_secret(url),
                    topic=val("ntfy_topic"),
                    token_enc=encrypt_secret(token) if token else "",
                )
            )
            log.info("Moved the old Settings notification URL into a notification channel.")
    await session.execute(delete(Setting).where(Setting.key.in_(RETIRED_KEYS)))


async def migrate_existing(session: AsyncSession) -> None:
    await _legacy_notify_settings(session)

    for src in (await session.execute(select(UpsSource))).scalars():
        src.password_enc = _reencrypt(src.password_enc or "", f"UPS source '{src.name}' password")
    for host in (await session.execute(select(Host))).scalars():
        host.credentials_enc = _reencrypt(host.credentials_enc or "", f"host '{host.name}' credentials")
    for ch in (await session.execute(select(NotifyChannel))).scalars():
        ch.token_enc = _reencrypt(ch.token_enc or "", f"channel '{ch.name}' token")
        if ch.url and not ch.url_enc:
            ch.url_enc = encrypt_secret(ch.url)
            ch.url = ""
        else:
            ch.url_enc = _reencrypt(ch.url_enc or "", f"channel '{ch.name}' URL")

    hb = await session.get(Setting, "heartbeat_url")
    if hb is not None:
        try:
            raw = json.loads(hb.value_json)
        except json.JSONDecodeError:
            raw = ""
        if isinstance(raw, str) and raw and secret_needs_migration(raw) and "://" in raw:
            hb.value_json = json.dumps(encrypt_secret(raw))

    for user in (await session.execute(select(User))).scalars():
        if not user.must_change_password and any(
            verify_password(pw, user.password_hash) for pw in KNOWN_DEFAULT_PASSWORDS
        ):
            user.must_change_password = True
            log.warning("User '%s' still uses a default password; a change is required at next sign-in.", user.username)
    await session.commit()
