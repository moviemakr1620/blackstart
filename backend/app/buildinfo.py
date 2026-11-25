"""App version, git commit, and a quiet check for a newer commit on GitHub."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from app import __version__

log = logging.getLogger("blackstart.build")

GITHUB_REPO = os.environ.get("BLACKSTART_GITHUB_REPO", "moviemakr1620/blackstart")
GITHUB_BRANCH = os.environ.get("BLACKSTART_GITHUB_BRANCH", "main")
CHECK_EVERY_SEC = 6 * 3600
GITHUB_TIMEOUT_SEC = 3.0

_commit = ""
_commit_source = "unknown"
_build_date = ""
_latest_commit = ""
_checked_at = 0.0
_lock: asyncio.Lock | None = None
_task: asyncio.Task[None] | None = None


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _git(*args: str, cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _discover() -> tuple[str, str, str]:
    env_commit = (os.environ.get("BLACKSTART_GIT_COMMIT") or "").strip()
    env_date = (os.environ.get("BLACKSTART_BUILD_DATE") or "").strip()
    if env_commit:
        return env_commit, "env", env_date
    root = _repo_root()
    if root is not None:
        sha = _git("rev-parse", "HEAD", cwd=root)
        if sha:
            date = _git("log", "-1", "--format=%cI", cwd=root) or env_date
            return sha, "git", date
    return "", "unknown", env_date


def refresh_local() -> None:
    global _commit, _commit_source, _build_date
    _commit, _commit_source, _build_date = _discover()


refresh_local()


def short_sha(value: str, length: int = 7) -> str:
    text = (value or "").strip()
    if text.lower().startswith("sha256:"):
        text = text.split(":")[-1]
    return text[:length] if text else ""


def _same_commit(left: str, right: str) -> bool:
    a, b = left.strip().lower(), right.strip().lower()
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def _git_has_commit(sha: str) -> bool | None:
    """True if sha is already in this checkout (on it or ahead)."""
    if _commit_source != "git":
        return None
    root = _repo_root()
    if root is None or not sha:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", sha, "HEAD"],
            check=False,
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.returncode == 0


def app_version() -> str:
    return (os.environ.get("BLACKSTART_VERSION") or "").strip() or __version__


def local_info() -> dict[str, Any]:
    return {
        "version": app_version(),
        "commit": short_sha(_commit),
        "commit_full": _commit,
        "build_date": _build_date,
        "source": _commit_source,
    }


def _update_available() -> bool:
    if not _commit or not _latest_commit:
        return False
    if _same_commit(_commit, _latest_commit):
        return False
    inherited = _git_has_commit(_latest_commit)
    if inherited is True:
        return False
    return True


def public_info() -> dict[str, Any]:
    info = local_info()
    info["update_available"] = _update_available()
    info["latest_commit"] = short_sha(_latest_commit)
    return info


async def fetch_latest() -> None:
    """Talk to GitHub; any failure is ignored so the app stays up offline."""
    global _latest_commit, _checked_at
    url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/{GITHUB_BRANCH}"
    try:
        async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT_SEC) as client:
            response = await client.get(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"blackstart/{app_version()}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if response.status_code == 200:
            sha = str((response.json() or {}).get("sha") or "")
            if sha:
                _latest_commit = sha
        # Non-200 (rate limit, 404) is also silent.
    except Exception:  # noqa: BLE001
        log.debug("GitHub update check skipped", exc_info=True)
    _checked_at = time.monotonic()


async def maybe_fetch_latest(*, force: bool = False) -> None:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        age = time.monotonic() - _checked_at if _checked_at else CHECK_EVERY_SEC + 1
        if not force and _checked_at and age < CHECK_EVERY_SEC:
            return
        # Mark the attempt first so a stampede does not pile on.
        if not force:
            # _checked_at is updated in fetch_latest; set a placeholder only if never checked
            pass
        await fetch_latest()


async def start_update_checker() -> asyncio.Task[None]:
    global _task

    async def loop() -> None:
        await maybe_fetch_latest(force=True)
        while True:
            await asyncio.sleep(CHECK_EVERY_SEC)
            await maybe_fetch_latest(force=True)

    _task = asyncio.create_task(loop(), name="blackstart-update-check")
    return _task


async def stop_update_checker() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None


def reset_cache_for_tests() -> None:
    global _latest_commit, _checked_at
    _latest_commit = ""
    _checked_at = 0.0
    refresh_local()
