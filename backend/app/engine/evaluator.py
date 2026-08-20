from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConditionState:
    """Tracks how long a named condition has been continuously true."""

    true_since: dict[str, float] = field(default_factory=dict)
    fired: set[str] = field(default_factory=set)

    def reset_episode(self) -> None:
        self.true_since.clear()
        self.fired.clear()

    def reset_source(self, source_id: int) -> None:
        prefix = f"{source_id}:"
        self.true_since = {k: v for k, v in self.true_since.items() if not k.startswith(prefix)}
        self.fired = {k for k in self.fired if not k.startswith(prefix)}

    def reset_restore_side(self) -> None:
        """Keep shed firings, clear restore ones — used when flipping back to battery."""
        self.fired = {k for k in self.fired if not k.startswith("restore:")}


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status_matches(current: str, wanted: str) -> bool:
    current = (current or "").lower()
    wanted = (wanted or "").lower()
    aliases = {
        "online": {"online", "ol", "charging"},
        "ol": {"online", "ol", "charging"},
        "on_battery": {"on_battery", "ob", "low_battery"},
        "ob": {"on_battery", "ob", "low_battery"},
        "low_battery": {"low_battery", "lb"},
        "charging": {"charging"},
    }
    if wanted in aliases:
        return current in aliases[wanted]
    return current == wanted


def eval_clause(clause: dict[str, Any], sample: dict[str, Any]) -> bool:
    ctype = (clause.get("type") or clause.get("op") or "").lower()
    if ctype in {"always", "true"}:
        return True
    if ctype in {"status_is", "status"}:
        return _status_matches(str(sample.get("status") or ""), str(clause.get("status") or clause.get("value") or ""))
    if ctype in {"on_battery"}:
        return bool(sample.get("on_battery"))
    if ctype in {"on_online", "online"}:
        return not bool(sample.get("on_battery")) and str(sample.get("status") or "") in {
            "online",
            "charging",
        }
    if ctype in {"battery_lte", "charge_lte", "battery_below"}:
        charge = _num(sample.get("charge"))
        threshold = _num(clause.get("value"))
        if charge is None or threshold is None:
            return False
        if clause.get("require_on_battery", True) and not sample.get("on_battery"):
            return False
        return charge <= threshold
    if ctype in {"battery_gte", "charge_gte", "battery_above"}:
        charge = _num(sample.get("charge"))
        threshold = _num(clause.get("value"))
        if charge is None or threshold is None:
            return False
        if clause.get("require_online") and sample.get("on_battery"):
            return False
        return charge >= threshold
    if ctype in {"runtime_lte", "runtime_below"}:
        runtime = _num(sample.get("runtime_sec"))
        minutes = _num(clause.get("minutes"))
        seconds = _num(clause.get("value"))
        if minutes is not None:
            seconds = minutes * 60
        if runtime is None or seconds is None:
            return False
        if clause.get("require_on_battery", True) and not sample.get("on_battery"):
            return False
        return runtime <= seconds
    if ctype in {"load_gte", "load_above"}:
        load = _num(sample.get("load_pct"))
        threshold = _num(clause.get("value"))
        if load is None or threshold is None:
            return False
        return load >= threshold
    if ctype in {"watts_gte"}:
        watts = _num(sample.get("watts"))
        threshold = _num(clause.get("value"))
        if watts is None or threshold is None:
            return False
        return watts >= threshold
    return False


def eval_trigger(trigger: dict[str, Any], sample: dict[str, Any]) -> bool:
    if not trigger:
        return False
    if "all" in trigger:
        return all(eval_trigger(c, sample) for c in trigger["all"])
    if "any" in trigger:
        return any(eval_trigger(c, sample) for c in trigger["any"])
    if "not" in trigger:
        return not eval_trigger(trigger["not"], sample)
    return eval_clause(trigger, sample)


def trigger_ready(
    step_key: str,
    trigger: dict[str, Any],
    sample: dict[str, Any],
    now: float,
    state: ConditionState,
    default_hold: float = 0.0,
) -> bool:
    """True when trigger holds continuously for hold_seconds and has not fired this episode."""
    if step_key in state.fired:
        return False
    holds = eval_trigger(trigger, sample)
    hold_for = float(trigger.get("hold_seconds", default_hold) or 0)
    if not holds:
        state.true_since.pop(step_key, None)
        return False
    since = state.true_since.get(step_key)
    if since is None:
        state.true_since[step_key] = now
        since = now
    if (now - since) + 1e-9 < hold_for:
        return False
    return True


def mark_fired(step_key: str, state: ConditionState) -> None:
    state.fired.add(step_key)
