import base64
import hashlib
import time
from pathlib import Path

import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings, bootstrap_secret_key
from app.db import SessionLocal
from app.main import STATIC_DIR, app, static_file_for
from app.migrate import migrate_existing
from app.models import Host, User
from app.security import (
    V2_PREFIX,
    decrypt_json,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    login_throttle,
    verify_password,
)
from conftest import ADMIN_PASSWORD


def test_default_secret_key_is_refused(tmp_path: Path):
    cfg = Settings(secret_key="change-me-in-production", data_dir=tmp_path)
    bootstrap_secret_key(cfg)
    assert cfg.secret_key != "change-me-in-production"
    assert len(cfg.secret_key) >= 40
    assert cfg.secret_source == "file"
    keyfile = tmp_path / "secret.key"
    assert keyfile.read_text().strip() == cfg.secret_key
    assert oct(keyfile.stat().st_mode & 0o777) == "0o600"
    # The same key is reused on the next start.
    again = Settings(secret_key="", data_dir=tmp_path)
    bootstrap_secret_key(again)
    assert again.secret_key == cfg.secret_key
    # The rejected default stays readable only for migrating old credentials.
    assert "change-me-in-production" in cfg.legacy_secret_keys


def test_strong_env_key_is_used(tmp_path: Path):
    cfg = Settings(secret_key="a-real-long-random-secret-value-1234", data_dir=tmp_path)
    bootstrap_secret_key(cfg)
    assert cfg.secret_key == "a-real-long-random-secret-value-1234"
    assert not (tmp_path / "secret.key").exists()


def test_token_signed_with_default_key_rejected():
    forged = jwt.encode({"sub": "admin", "ver": 0, "exp": int(time.time()) + 600}, "change-me-in-production", "HS256")
    with TestClient(app) as client:
        resp = client.get("/api/settings", headers={"Authorization": f"Bearer {forged}"})
        assert resp.status_code == 401


def test_path_traversal_blocked():
    assert static_file_for("../../../../etc/passwd") is None
    assert static_file_for("..%2f..%2fetc/passwd") is None
    assert static_file_for("/etc/passwd") is None
    with TestClient(app) as client:
        for path in ("/..%2f..%2f..%2f..%2fetc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/..%2f..%2fapp/config.py"):
            resp = client.get(path)
            assert "root:" not in resp.text
            assert "BaseSettings" not in resp.text
    if (STATIC_DIR / "index.html").is_file():
        assert static_file_for("index.html") is not None


def test_password_hash_upgrade_and_legacy_verify():
    salt = "abcd"
    legacy = f"pbkdf2${salt}${hashlib.pbkdf2_hmac('sha256', b'pw', salt.encode(), 200_000).hex()}"
    assert verify_password("pw", legacy)
    new = hash_password("pw")
    assert new.startswith("pbkdf2_sha256$600000$")
    assert verify_password("pw", new) and not verify_password("nope", new)


def test_legacy_credentials_decrypt_and_migrate():
    # Exactly how releases before this one stored secrets under the published default.
    old_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"change-me-to-a-long-random-string").digest()))
    legacy_value = old_fernet.encrypt(b'{"password": "hunter2-legacy"}').decode()
    assert decrypt_json(legacy_value)["password"] == "hunter2-legacy"

    async def run():
        async with SessionLocal() as s:
            h = Host(name="legacy-host", kind="ssh", address="192.0.2.5", credentials_enc=legacy_value)
            s.add(h)
            await s.commit()
            await migrate_existing(s)
            await s.refresh(h)
            assert h.credentials_enc.startswith(V2_PREFIX)
            assert decrypt_json(h.credentials_enc)["password"] == "hunter2-legacy"
            await s.delete(h)
            await s.commit()

    import asyncio

    with TestClient(app):
        pass
    asyncio.run(run())


def test_undecryptable_secret_raises_instead_of_leaking_ciphertext():
    other = Fernet(Fernet.generate_key()).encrypt(b"x").decode()
    with pytest.raises(Exception):
        decrypt_secret(other)
    assert decrypt_secret(encrypt_secret("hello")) == "hello"


def test_forced_password_change_and_token_revocation():
    import asyncio

    async def make_user():
        async with SessionLocal() as s:
            existing = (await s.execute(select(User).where(User.username == "olduser"))).scalar_one_or_none()
            if existing:
                await s.delete(existing)
                await s.commit()
            s.add(User(username="olduser", password_hash=hash_password("blackstart")))
            await s.commit()
            await migrate_existing(s)
            u = (await s.execute(select(User).where(User.username == "olduser"))).scalar_one()
            assert u.must_change_password is True

    with TestClient(app) as client:
        asyncio.run(make_user())
        login = client.post("/api/auth/login", json={"username": "olduser", "password": "blackstart"})
        assert login.status_code == 200
        assert login.json()["must_change_password"] is True
        old = {"Authorization": f"Bearer {login.json()['token']}"}
        assert client.get("/api/settings", headers=old).status_code == 403
        assert client.get("/api/auth/me", headers=old).status_code == 200
        weak = client.post(
            "/api/auth/password", headers=old, json={"current_password": "blackstart", "new_password": "short"}
        )
        assert weak.status_code == 400
        changed = client.post(
            "/api/auth/password",
            headers=old,
            json={"current_password": "blackstart", "new_password": "A-much-better-pass-42"},
        )
        assert changed.status_code == 200, changed.text
        new = {"Authorization": f"Bearer {changed.json()['token']}"}
        assert client.get("/api/settings", headers=new).status_code == 200
        # The token from before the change no longer works.
        assert client.get("/api/settings", headers=old).status_code == 401


def test_login_is_throttled():
    login_throttle.reset("testclient|admin")
    with TestClient(app) as client:
        codes = [
            client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code
            for _ in range(10)
        ]
        assert codes[0] == 401
        assert codes[-1] == 429
    login_throttle._fails.clear()
    with TestClient(app) as client:
        ok = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        assert ok.status_code == 200


def test_websocket_needs_token_message():
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}).json()["token"]
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json({"token": token})
            hello = ws.receive_json()
            assert hello["event"] == "hello"
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json({"token": "garbage"})
            with pytest.raises(Exception):
                ws.receive_json()
