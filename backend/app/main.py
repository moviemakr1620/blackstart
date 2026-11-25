from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import buildinfo
from app import __version__
from app.config import settings as app_settings
from app.db import SessionLocal, init_db
from app.engine.service import engine
from app.routers import auth, hosts, notify_channels, policies, runs, settings, targets, ups, ws
from app.seed import seed_if_empty

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("blackstart")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        await seed_if_empty(session)
    if app_settings.secret_source == "file":
        log.info("Using the secret key stored in %s", app_settings.secret_key_file)
    ver = buildinfo.public_info()
    log.info(
        "Blackstart %s starting — commit %s (%s) — data dir %s",
        ver["version"],
        ver["commit"] or "unknown",
        ver["source"],
        app_settings.data_dir,
    )
    engine.start()
    await buildinfo.start_update_checker()
    yield
    await buildinfo.stop_update_checker()
    await engine.stop()


app = FastAPI(
    title="Blackstart",
    description="Power-event orchestrator for Proxmox, Synology, and Docker — driven by EcoFlow / NUT / PeaNUT.",
    version=__version__,
    lifespan=lifespan,
)

# No CORS middleware: the UI is served from this same origin (and the Vite
# dev server proxies /api), so browsers on other sites get no access.

app.include_router(auth.router)
app.include_router(ups.router)
app.include_router(hosts.router)
app.include_router(targets.router)
app.include_router(policies.router)
app.include_router(runs.router)
app.include_router(settings.router)
app.include_router(notify_channels.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health() -> dict:
    info = buildinfo.public_info()
    return {
        "ok": True,
        "version": info["version"],
        "commit": info["commit"],
        "mode": engine.power_mode,
        "ups_connected": bool(engine.last_status.get("connected")),
    }


@app.get("/api/version")
async def version() -> dict:
    # Kick a refresh if the cache is stale, but never wait on GitHub.
    try:
        import asyncio

        loop = asyncio.get_running_loop()
        loop.create_task(buildinfo.maybe_fetch_latest())
    except RuntimeError:
        pass
    return buildinfo.public_info()


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def static_file_for(full_path: str) -> Path | None:
    """Resolve a request path to a file inside STATIC_DIR, or None.

    Paths that escape the folder (../, %2f-encoded slashes, symlinks) are refused.
    """
    if not full_path:
        return None
    root = STATIC_DIR.resolve()
    try:
        candidate = (root / full_path).resolve()
    except (OSError, ValueError):
        return None
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate
if STATIC_DIR.exists():
    assets = STATIC_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = static_file_for(full_path)
        if candidate is not None:
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
