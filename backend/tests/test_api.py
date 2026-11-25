from fastapi.testclient import TestClient

from app.main import app
from conftest import ADMIN_PASSWORD


def _login(client: TestClient) -> dict[str, str]:
    login = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_health_and_login_and_playbooks():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        assert "error" not in health.json()

        bad = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        assert bad.status_code == 401

        headers = _login(client)
        me = client.get("/api/auth/me", headers=headers)
        assert me.json()["username"] == "admin"
        assert me.json()["must_change_password"] is False

        policies = client.get("/api/policies", headers=headers)
        assert policies.status_code == 200
        body = policies.json()
        kinds = {p["kind"] for p in body}
        assert "shed" in kinds
        assert "restore" in kinds
        assert any(p["steps"] for p in body)

        sim = client.post("/api/ups/simulate", headers=headers, json={"event": "pull_plug"})
        assert sim.status_code == 200
        assert sim.json()["snapshot"]["on_battery"] is True
        client.post("/api/ups/simulate", headers=headers, json={"event": "restore_grid"})

        sources = client.get("/api/ups/sources", headers=headers)
        assert any(s["kind"] == "simulator" for s in sources.json())

        settings = client.get("/api/settings", headers=headers)
        assert settings.status_code == 200
        assert settings.json()["timezone"] == ""
        assert "gotify_token" not in settings.json()
        saved = client.put("/api/settings", headers=headers, json={"timezone": "America/Los_Angeles"})
        assert saved.status_code == 200
        assert saved.json()["timezone"] == "America/Los_Angeles"
        assert saved.json()["live_actions"] is False


def test_clear_history():
    with TestClient(app) as client:
        headers = _login(client)
        client.post("/api/ups/simulate", headers=headers, json={"event": "pull_plug"})
        cleared = client.delete("/api/history", headers=headers)
        client.post("/api/ups/simulate", headers=headers, json={"event": "restore_grid"})
        assert cleared.status_code == 200
        body = cleared.json()
        assert body["ok"] is True
        assert client.get("/api/runs", headers=headers).json() == []


def test_no_cors_for_other_sites():
    with TestClient(app) as client:
        resp = client.options(
            "/api/settings",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PUT"},
        )
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_notify_channel_url_is_never_returned():
    with TestClient(app) as client:
        headers = _login(client)
        url = "https://discord.com/api/webhooks/123/super-secret-token"
        made = client.post(
            "/api/notify-channels",
            headers=headers,
            json={"name": "Discord", "kind": "discord", "url": url},
        )
        assert made.status_code == 200, made.text
        listed = client.get("/api/notify-channels", headers=headers).text
        assert "super-secret-token" not in listed
        assert made.json()["has_url"] is True
        cid = made.json()["id"]
        # Saving without a URL keeps the stored one.
        upd = client.put(
            f"/api/notify-channels/{cid}", headers=headers, json={"name": "D2", "kind": "discord", "url": ""}
        )
        assert upd.json()["has_url"] is True
        client.delete(f"/api/notify-channels/{cid}", headers=headers)


def test_metadata_urls_refused():
    with TestClient(app) as client:
        headers = _login(client)
        resp = client.post(
            "/api/notify-channels",
            headers=headers,
            json={"name": "x", "kind": "webhook", "url": "http://169.254.169.254/latest/meta-data"},
        )
        assert resp.status_code == 400
