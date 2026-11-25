"""Blank secret fields keep the stored value; clear_fields deletes it."""

from fastapi.testclient import TestClient

from app.main import app
from app.security import decrypt_json
from conftest import ADMIN_PASSWORD
from tests.test_api import _login


def _host_payload(**creds):
    return {
        "name": "lab-ssh",
        "kind": "ssh",
        "address": "192.0.2.20",
        "port": 22,
        "verify_tls": True,
        "credentials": creds,
        "extra": {"ca_pem": "-----BEGIN CERTIFICATE-----\nLAB\n-----END CERTIFICATE-----"},
    }


def test_blank_update_keeps_secrets_clear_fields_removes_them():
    with TestClient(app) as client:
        headers = _login(client)
        created = client.post(
            "/api/hosts",
            headers=headers,
            json=_host_payload(
                username="ops",
                password="s3cret-keep",
                private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nstub\n-----END OPENSSH PRIVATE KEY-----",
                key_passphrase="correct-horse",
            ),
        )
        assert created.status_code == 200, created.text
        host_id = created.json()["id"]
        keys = set(created.json()["credential_keys"])
        assert keys >= {"username", "password", "private_key", "key_passphrase"}
        assert created.json()["extra"].get("ca_pem", "").startswith("-----BEGIN")
        # The API must never echo the secret values back.
        raw = created.text
        assert "s3cret-keep" not in raw
        assert "correct-horse" not in raw
        assert "BEGIN OPENSSH PRIVATE KEY" not in raw

        kept = client.put(
            f"/api/hosts/{host_id}",
            headers=headers,
            json={
                "name": "lab-ssh",
                "kind": "ssh",
                "address": "192.0.2.20",
                "port": 22,
                "verify_tls": True,
                "credentials": {},
                "extra": created.json()["extra"],
            },
        )
        assert kept.status_code == 200, kept.text
        assert set(kept.json()["credential_keys"]) >= {"username", "password", "private_key", "key_passphrase"}
        assert kept.json()["extra"].get("ca_pem")

        cleared = client.put(
            f"/api/hosts/{host_id}",
            headers=headers,
            json={
                "name": "lab-ssh",
                "kind": "ssh",
                "address": "192.0.2.20",
                "port": 22,
                "verify_tls": True,
                "credentials": {},
                "extra": created.json()["extra"],
                "clear_fields": ["private_key", "key_passphrase", "ca_pem"],
            },
        )
        assert cleared.status_code == 200, cleared.text
        left = set(cleared.json()["credential_keys"])
        assert "private_key" not in left
        assert "key_passphrase" not in left
        assert "password" in left
        assert "username" in left
        assert "ca_pem" not in (cleared.json()["extra"] or {})
        assert "s3cret-keep" not in cleared.text

        # Replacement wins over a simultaneous clear of the same field.
        replaced = client.put(
            f"/api/hosts/{host_id}",
            headers=headers,
            json={
                "name": "lab-ssh",
                "kind": "ssh",
                "address": "192.0.2.20",
                "port": 22,
                "credentials": {"password": "new-pass"},
                "clear_fields": ["password"],
            },
        )
        assert replaced.status_code == 200
        assert "password" in replaced.json()["credential_keys"]
        from app.db import SessionLocal
        from app.models import Host
        import asyncio

        async def _read_password() -> str:
            async with SessionLocal() as session:
                row = await session.get(Host, host_id)
                return decrypt_json(row.credentials_enc)["password"]

        assert asyncio.run(_read_password()) == "new-pass"


def test_forget_pinned_ssh_host_key():
    with TestClient(app) as client:
        headers = _login(client)
        created = client.post(
            "/api/hosts",
            headers=headers,
            json={
                "name": "pi",
                "kind": "ssh",
                "address": "192.0.2.21",
                "port": 22,
                "credentials": {"username": "root"},
                "extra": {"ssh_host_key": "SHA256:abcdefghijklmnopqrstuvwxyz0123456789abcd"},
            },
        )
        assert created.status_code == 200, created.text
        host_id = created.json()["id"]
        assert created.json()["extra"]["ssh_host_key"].startswith("SHA256:")

        forgotten = client.post(f"/api/hosts/{host_id}/forget-host-key", headers=headers)
        assert forgotten.status_code == 200, forgotten.text
        assert "ssh_host_key" not in (forgotten.json()["extra"] or {})

        # Also via clear_fields on save (e.g. a form that batches the change).
        client.put(
            f"/api/hosts/{host_id}",
            headers=headers,
            json={
                "name": "pi",
                "kind": "ssh",
                "address": "192.0.2.21",
                "port": 22,
                "credentials": {},
                "extra": {"ssh_host_key": "SHA256:old-pin-should-go"},
                "clear_fields": ["ssh_host_key"],
            },
        )
        again = client.get("/api/hosts", headers=headers).json()
        row = next(h for h in again if h["id"] == host_id)
        assert "ssh_host_key" not in (row["extra"] or {})


def test_clear_ups_password_and_notify_token():
    with TestClient(app) as client:
        headers = _login(client)
        src = client.post(
            "/api/ups/sources",
            headers=headers,
            json={
                "name": "nut-lab",
                "kind": "nut",
                "host": "192.0.2.30",
                "port": 3493,
                "username": "monuser",
                "password": "nut-secret",
            },
        )
        assert src.status_code == 200, src.text
        assert src.json()["has_password"] is True
        assert "nut-secret" not in src.text
        sid = src.json()["id"]
        kept = client.put(
            f"/api/ups/sources/{sid}",
            headers=headers,
            json={"name": "nut-lab", "kind": "nut", "host": "192.0.2.30", "port": 3493, "username": "monuser"},
        )
        assert kept.json()["has_password"] is True
        gone = client.put(
            f"/api/ups/sources/{sid}",
            headers=headers,
            json={
                "name": "nut-lab",
                "kind": "nut",
                "host": "192.0.2.30",
                "port": 3493,
                "username": "monuser",
                "clear_fields": ["password"],
            },
        )
        assert gone.json()["has_password"] is False

        ch = client.post(
            "/api/notify-channels",
            headers=headers,
            json={"name": "gotify", "kind": "gotify", "url": "https://gotify.example/hook", "token": "g-token"},
        )
        assert ch.status_code == 200, ch.text
        assert ch.json()["has_token"] is True
        assert "g-token" not in ch.text
        cid = ch.json()["id"]
        updated = client.put(
            f"/api/notify-channels/{cid}",
            headers=headers,
            json={"name": "gotify", "kind": "gotify", "url": "", "clear_fields": ["token"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_token"] is False
        assert updated.json()["has_url"] is True


def test_clear_heartbeat_url():
    with TestClient(app) as client:
        headers = _login(client)
        saved = client.put("/api/settings", headers=headers, json={"heartbeat_url": "https://hc.example/ping"})
        assert saved.json()["heartbeat_url_set"] is True
        assert "hc.example" not in saved.text
        # Omitting the field keeps it.
        kept = client.put("/api/settings", headers=headers, json={"timezone": "America/New_York"})
        assert kept.json()["heartbeat_url_set"] is True
        cleared = client.put("/api/settings", headers=headers, json={"clear_fields": ["heartbeat_url"]})
        assert cleared.json()["heartbeat_url_set"] is False
