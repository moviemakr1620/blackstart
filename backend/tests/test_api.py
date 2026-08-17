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
