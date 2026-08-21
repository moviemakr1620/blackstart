from app.adapters.nut import classify_status, flatten_peanut_or_nut, snapshot_from_vars
from app.engine.evaluator import ConditionState, eval_trigger, trigger_ready


def test_classify_status():
    assert classify_status("OL")[0] == "online"
    assert classify_status("ONLINE")[0] == "online"
    assert classify_status("OL CHRG") == ("charging", False, True)
    assert classify_status("OB DISCHRG")[0] == "on_battery"
    assert classify_status("OB LB")[0] == "low_battery"


def test_flatten_nested_peanut():
    nested = {"battery": {"charge": 88}, "ups": {"status": "OL"}}
    flat = flatten_peanut_or_nut(nested)
    assert flat["battery.charge"] == 88
    assert snapshot_from_vars(nested)["charge"] == 88


def test_battery_lte_requires_on_battery():
    sample = {"status": "online", "on_battery": False, "charge": 10}
    assert not eval_trigger({"type": "battery_lte", "value": 20}, sample)
    sample["on_battery"] = True
    sample["status"] = "on_battery"
    assert eval_trigger({"type": "battery_lte", "value": 20}, sample)


def test_compound_restore_trigger():
    trigger = {
        "all": [
            {"type": "on_online"},
            {"type": "battery_gte", "value": 50, "require_online": True},
        ]
    }
    assert not eval_trigger(trigger, {"status": "on_battery", "on_battery": True, "charge": 90})
    assert eval_trigger(trigger, {"status": "online", "on_battery": False, "charge": 55})
    assert not eval_trigger(trigger, {"status": "online", "on_battery": False, "charge": 20})


def test_hold_and_once_per_episode():
    state = ConditionState()
    trigger = {"type": "on_battery", "hold_seconds": 10}
    sample = {"status": "on_battery", "on_battery": True}
    assert not trigger_ready("s1", trigger, sample, now=0, state=state)
    assert not trigger_ready("s1", trigger, sample, now=9, state=state)
    assert trigger_ready("s1", trigger, sample, now=10, state=state)
    state.fired.add("s1")
    assert not trigger_ready("s1", trigger, sample, now=30, state=state)
    # Drop then recover should reset hold clock, but stay fired this episode
    sample2 = {"status": "online", "on_battery": False}
    assert not trigger_ready("s1", trigger, sample2, now=31, state=state)
