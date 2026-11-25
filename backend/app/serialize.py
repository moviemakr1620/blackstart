from __future__ import annotations

import json
from typing import Any

from app.models import Host, Policy, PolicyStep, Target, UpsSource
from app.schemas import HostOut, PolicyOut, PolicyStepOut, TargetOut, UpsSourceOut
from app.security import SecretDecryptError, decrypt_json


def _json(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def source_out(row: UpsSource) -> UpsSourceOut:
    return UpsSourceOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        enabled=row.enabled,
        host=row.host,
        port=row.port,
        base_url=row.base_url,
        device_name=row.device_name,
        username=row.username,
        has_password=bool(row.password_enc),
        poll_interval_sec=row.poll_interval_sec,
        extra=_json(row.extra_json),
    )


def host_out(row: Host) -> HostOut:
    creds_error = ""
    try:
        creds = decrypt_json(row.credentials_enc)
    except SecretDecryptError as exc:
        creds, creds_error = {}, str(exc)
    return HostOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        enabled=row.enabled,
        address=row.address,
        port=row.port,
        verify_tls=row.verify_tls,
        extra=_json(row.extra_json),
        last_ok_at=row.last_ok_at,
        last_error=row.last_error,
        credential_keys=sorted(k for k, v in creds.items() if v),
        credentials_error=creds_error,
    )


def target_out(row: Target) -> TargetOut:
    return TargetOut(
        id=row.id,
        host_id=row.host_id,
        host_name=row.host.name if row.host else None,
        host_kind=row.host.kind if row.host else None,
        kind=row.kind,
        name=row.name,
        external_id=row.external_id,
        extra=_json(row.extra_json),
        critical=row.critical,
        enabled=row.enabled,
        notes=row.notes,
        last_status=row.last_status,
        last_checked=row.last_checked,
    )


def binding_warning(row: Policy, sources: dict[int, UpsSource] | None) -> str:
    if sources is None:
        return ""
    if row.ups_source_id is None:
        return "Not linked to a UPS, so it will never run on its own. Pick a UPS for this playbook."
    src = sources.get(row.ups_source_id)
    if src is None:
        return "Linked to a UPS that was deleted, so it will never run on its own. Pick a UPS."
    if not src.enabled:
        return f"Linked to '{src.name}', which is turned off, so it will not run."
    if src.kind == "simulator":
        others = [s for s in sources.values() if s.enabled and s.kind != "simulator"]
        if others:
            return f"Linked to the simulator, not your real UPS ('{others[0].name}'). A real outage will not run it."
    return ""


def policy_out(row: Policy, sources: dict[int, UpsSource] | None = None) -> PolicyOut:
    steps = [step_out(s) for s in sorted(row.steps, key=lambda x: x.order)]
    return PolicyOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        description=row.description,
        enabled=row.enabled,
        ups_source_id=row.ups_source_id,
        priority=row.priority,
        steps=steps,
        updated_at=row.updated_at,
        binding_warning=binding_warning(row, sources),
    )


def step_out(row: PolicyStep) -> PolicyStepOut:
    return PolicyStepOut(
        id=row.id,
        order=row.order,
        name=row.name,
        enabled=row.enabled,
        trigger=_json(row.trigger_json),
        action=_json(row.action_json),
        delay_before_sec=row.delay_before_sec,
        timeout_sec=row.timeout_sec,
        continue_on_error=row.continue_on_error,
    )
