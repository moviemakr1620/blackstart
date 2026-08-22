import os
import tempfile

# Point the app at an isolated DB before importing it.
os.environ.setdefault("BLACKSTART_SECRET_KEY", "test-secret-key")
os.environ["BLACKSTART_DATA_DIR"] = tempfile.mkdtemp(prefix="blackstart-test-")
os.environ["BLACKSTART_LIVE_ACTIONS"] = "false"

from fastapi.testclient import TestClient

from app.main import app


def test_health_and_login_and_playbooks():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        bad = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        assert bad.status_code == 401

        login = client.post("/api/auth/login", json={"username": "admin", "password": "blackstart"})
        assert login.status_code == 200
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        me = client.get("/api/auth/me", headers=headers)
        assert me.json()["username"] == "admin"

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

        sources = client.get("/api/ups/sources", headers=headers)
        assert any(s["kind"] == "simulator" for s in sources.json())

        settings = client.get("/api/settings", headers=headers)
        assert settings.status_code == 200
        assert settings.json()["timezone"] == ""
        saved = client.put("/api/settings", headers=headers, json={"timezone": "America/Los_Angeles"})
        assert saved.status_code == 200
        assert saved.json()["timezone"] == "America/Los_Angeles"
        assert saved.json()["live_actions"] is False


def test_clear_history():
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "blackstart"})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        client.post("/api/ups/simulate", headers=headers, json={"event": "pull_plug"})
        cleared = client.delete("/api/history", headers=headers)
        assert cleared.status_code == 200
        body = cleared.json()
        assert body["ok"] is True
        assert client.get("/api/runs", headers=headers).json() == []
        assert client.get("/api/episodes", headers=headers).json() == []
        assert client.get("/api/events/recent", headers=headers).json() == []
