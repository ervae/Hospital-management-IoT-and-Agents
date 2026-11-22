"""Uygulama giriş noktası: `uvicorn app.main:app` (backend klasöründen)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from .config import settings
from .db import get_engine, init_db
from .routers import agents, auth, devices, flow, overview, rooms, users
from .seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"


def enabled_adapters():
    """Açık olan adaptörler. Yeni entegrasyon = buraya bir satır + ayar."""
    out = []
    if settings.simulator_enabled:
        from .adapters.simulator import Simulator
        out.append(Simulator())
    if settings.rule_agent_enabled:
        from .adapters.rule_agent import RuleAgents
        out.append(RuleAgents())
    if settings.auto_approve_s > 0:
        from .adapters.auto_approve import AutoApprove
        out.append(AutoApprove())
    if settings.mqtt_enabled:
        from .adapters.mqtt_bridge import MqttBridge
        out.append(MqttBridge())
    return out


def create_app(start_adapters: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        with Session(get_engine()) as s:
            seed(s)
        running = enabled_adapters() if start_adapters else []
        for a in running:
            await a.start()
        yield
        for a in running:
            await a.stop()

    app = FastAPI(title="Yatak Yönetimi API", version="2.0.0", lifespan=lifespan,
                  description="IoT destekli çok ajanlı hastane yatak yönetimi. Sözleşme: docs/API.md")
    api = APIRouter(prefix="/api/v1")
    for r in (auth, users, rooms, flow, devices, agents, overview):
        api.include_router(r.router)

    @api.get("/health", tags=["Genel görünüm"])
    def health():
        return {"status": "ok", "version": app.version}

    app.include_router(api)

    if FRONTEND.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(FRONTEND / "index.html")
    return app


app = create_app()
