"""Build info and the public version/health endpoints."""

from fastapi.testclient import TestClient

import app.buildinfo as buildinfo
from app.main import app


def setup_function() -> None:
    buildinfo.reset_cache_for_tests()


def test_health_includes_version_and_commit():
    with TestClient(app) as c:
        body = c.get("/api/health").json()
        assert body["ok"] is True
        assert body["version"]
        # Tests run from the git checkout, so a short SHA should be present.
        assert body["commit"]
        assert "error" not in body


def test_version_endpoint_shape():
    with TestClient(app) as c:
        body = c.get("/api/version").json()
        assert body["version"]
        assert body["commit"]
        assert body["source"] in {"git", "env", "unknown"}
        assert body["update_available"] is False or body["update_available"] is True
        assert "latest_commit" in body


def test_version_survives_github_failure(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("offline")

    monkeypatch.setattr(buildinfo, "fetch_latest", boom)
    buildinfo.reset_cache_for_tests()
    with TestClient(app) as c:
        body = c.get("/api/version").json()
        assert body["version"]
        assert body["update_available"] is False


def test_update_available_when_main_moved(monkeypatch):
    buildinfo.reset_cache_for_tests()
    buildinfo._commit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    buildinfo._commit_source = "env"
    buildinfo._latest_commit = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    async def noop() -> None:
        return None

    monkeypatch.setattr(buildinfo, "maybe_fetch_latest", noop)
    info = buildinfo.public_info()
    assert info["update_available"] is True
    assert info["latest_commit"] == "bbbbbbb"
    assert info["commit"] == "aaaaaaa"


def test_same_prefix_is_not_an_update():
    buildinfo._commit = "abcdef1234"
    buildinfo._commit_source = "env"
    buildinfo._latest_commit = "abcdef1234567890"
    assert buildinfo.public_info()["update_available"] is False
