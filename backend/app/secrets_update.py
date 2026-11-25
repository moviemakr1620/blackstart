"""Merge write-only secret updates: blank means keep, clear_fields means delete."""

from __future__ import annotations

from typing import Any, Iterable

# Credential keys that may be stripped from a host record.
HOST_CREDENTIAL_FIELDS = {
    "password",
    "private_key",
    "key_passphrase",
    "token_secret",
    "token_id",
    "username",
}
HOST_EXTRA_FIELDS = {"ca_pem", "ssh_host_key"}


def merge_secrets(
    existing: dict[str, Any] | None,
    updates: dict[str, Any] | None,
    clear_fields: Iterable[str] | None,
    allowed: Iterable[str],
) -> dict[str, Any]:
    """Return a new dict: drop allowed clear_fields, then apply non-empty updates.

    Empty or missing update values are ignored so a blank form field keeps the
    stored secret. A new non-empty value wins over a clear of the same field.
    """
    allowed_set = set(allowed)
    out = {k: v for k, v in dict(existing or {}).items() if v not in (None, "")}
    for field in clear_fields or []:
        if field in allowed_set:
            out.pop(field, None)
    for key, value in dict(updates or {}).items():
        if value not in (None, ""):
            out[key] = value
    return out


def extra_without(extra: dict[str, Any] | None, clear_fields: Iterable[str] | None) -> dict[str, Any]:
    out = dict(extra or {})
    for field in clear_fields or []:
        if field in HOST_EXTRA_FIELDS:
            out.pop(field, None)
    if not str(out.get("ca_pem") or "").strip():
        out.pop("ca_pem", None)
    return out
