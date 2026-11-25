"""Expand a playbook step action into one item per selected target.

Existing steps store a single target_id / host_id / container / stack. New
steps may also send target_ids, host_ids, containers, or stacks lists. Both
shapes are accepted; a single-target step still expands to one item.
"""

from __future__ import annotations

from typing import Any

CONTAINER_ACTIONS = {
    "docker_stop",
    "docker_start",
    "docker_restart",
    "synology_stop_container",
    "synology_start_container",
}
STACK_ACTIONS = {"docker_stop_stack", "docker_start_stack"}


def _int_ids(action: dict[str, Any], singular: str, plural: str) -> list[int]:
    raw = action.get(plural)
    values = raw if isinstance(raw, list) else ([] if raw in (None, "") else [raw])
    if not values:
        one = action.get(singular)
        if one not in (None, ""):
            values = [one]
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number and number not in seen:
            seen.add(number)
            out.append(number)
    return out


def _name_list(action: dict[str, Any], singular: str, plural: str) -> list[str]:
    raw = action.get(plural)
    values: list[Any] = raw if isinstance(raw, list) else ([] if raw in (None, "") else [raw])
    if not values:
        one = action.get(singular)
        if one not in (None, ""):
            values = [one]
        elif action.get("name"):
            values = [action.get("name")]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            parts = [part.strip() for part in value.replace("\n", ",").split(",")]
        else:
            parts = [str(value).strip()] if value not in (None, "") else []
        for part in parts:
            key = part.lower()
            if part and key not in seen:
                seen.add(key)
                out.append(part)
    return out


def expand_action_items(action: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return one action dict per selected target, host, container, or stack."""
    action = dict(action or {})
    target_ids = _int_ids(action, "target_id", "target_ids")
    if target_ids:
        items: list[dict[str, Any]] = []
        for target_id in target_ids:
            item = dict(action)
            item["target_id"] = target_id
            item["target_ids"] = [target_id]
            items.append(item)
        return items

    atype = str(action.get("type") or "").lower()
    if atype in STACK_ACTIONS:
        names = _name_list(action, "stack", "stacks")
        if len(names) > 1:
            return [{**action, "stack": name, "name": name, "stacks": [name], "target_id": None} for name in names]
    elif atype in CONTAINER_ACTIONS:
        names = _name_list(action, "container", "containers")
        if len(names) > 1:
            return [
                {**action, "container": name, "name": name, "containers": [name], "target_id": None} for name in names
            ]

    host_ids = _int_ids(action, "host_id", "host_ids")
    if len(host_ids) > 1:
        return [{**action, "host_id": host_id, "host_ids": [host_id], "target_id": None} for host_id in host_ids]
    return [action]


def item_label(action: dict[str, Any], target_name: str | None = None, host_name: str | None = None) -> str:
    """Short label for run logs (one line per selected thing)."""
    if target_name:
        return target_name
    atype = str(action.get("type") or "").lower()
    if atype in STACK_ACTIONS:
        return str(action.get("stack") or action.get("name") or "stack")
    if atype in CONTAINER_ACTIONS:
        return str(action.get("container") or action.get("name") or "container")
    if host_name:
        return host_name
    return atype or "action"
