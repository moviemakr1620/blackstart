from app.adapters.notify import looks_like_discord
from app.adapters.nut import snapshot_from_vars


def test_discord_url_detection():
    assert looks_like_discord("https://discord.com/api/webhooks/123/abc")
    assert looks_like_discord("https://discordapp.com/api/webhooks/123/abc")
    assert not looks_like_discord("https://example.com/hook")


def test_snapshot_aliases_and_missing_ecoflow_fields():
    snap = snapshot_from_vars(
        {
            "battery.charge": 88,
            "ups.status": "OL",
            "output.realpower": 140,
        }
    )
    assert snap["charge"] == 88
    assert snap["watts"] == 140
    assert snap["load_pct"] is None
    assert snap["input_voltage"] is None
    assert "battery.charge" in snap["reported_keys"]


def test_ecoflow_delta3_nut_keys():
    snap = snapshot_from_vars(
        {
            "battery.charge": 90,
            "ups.status": "OL",
            "ups.realpower": 192.0,
            "ups.power.nominal": 1023,
            "input.realpower": 192.0,
            "experimental.ecoflow.battery.charge.runtime": 775620,
        }
    )
    assert snap["watts"] == 192
    assert snap["input_watts"] == 192
    assert snap["load_pct"] is not None
    assert 18 <= snap["load_pct"] <= 19
    # EcoFlow's experimental runtime is nonsense; estimate from charge/watts.
    assert snap["runtime_sec"] is not None
    assert 3 * 3600 < snap["runtime_sec"] < 6 * 3600
