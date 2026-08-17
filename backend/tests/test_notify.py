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
