from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings as app_settings
from app.db import SessionLocal, init_db
from app.engine.service import engine
from app.routers import auth, hosts, policies, runs, settings, targets, ups, ws
from app.seed import seed_if_empty

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("blackstart")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        await seed_if_empty(session)
    if app_settings.secret_key in {"change-me-in-production", "change-me-to-a-long-random-string"}:
        log.warning("BLACKSTART_SECRET_KEY is still the default. Set a long random value in production.")
    log.info("Blackstart %s starting — data dir %s", __version__, app_settings.data_dir)
    engine.start()
    yield
    await engine.stop()


app = FastAPI(
    title="Blackstart",
    description="Power-event orchestrator for Proxmox, Synology, and Docker — driven by EcoFlow / NUT / PeaNUT.",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(ups.router)
app.include_router(hosts.router)
app.include_router(targets.router)
app.include_router(policies.router)
app.include_router(runs.router)
app.include_router(settings.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "mode": engine.power_mode,
        "ups_connected": bool(engine.last_status.get("connected")),
        "error": engine.last_error,
    }


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    assets = STATIC_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
