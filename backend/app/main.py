"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes import asr, system
from backend.app.core.config import Settings
from backend.app.core.logging import configure_logging
from backend.app.services.model_registry import ModelRegistry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    configure_logging()
    app.state.settings = settings
    registry = ModelRegistry(model_revision=settings.model_revision)
    app.state.models = registry

    if settings.preload_models:
        logger.info(
            "Preloading FunASR models before startup: %s",
            ", ".join(settings.preload_models),
        )
        try:
            await asyncio.to_thread(registry.preload, settings.preload_models)
            logger.info("FunASR model preloading completed")
        except Exception:
            logger.exception("FunASR model preloading failed")
            if settings.preload_strict:
                raise
    else:
        logger.warning(
            "Model preloading is disabled; the first recognition request may load models"
        )

    logger.info("%s started", settings.app_name)
    yield
    logger.info("%s stopped", settings.app_name)


app = FastAPI(
    title="FunASR Realtime API",
    version="0.2.0",
    description="Browser microphone recognition over WebSocket",
    lifespan=lifespan,
)

settings = Settings.from_env()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router, prefix=settings.api_prefix)
app.include_router(asr.router, prefix=settings.api_prefix)


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.is_dir():
    # API routers are registered first, so only unmatched browser routes reach
    # the static frontend.
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
else:

    @app.get("/", include_in_schema=False)
    async def root():
        return JSONResponse(
            {
                "service": settings.app_name,
                "docs": "/docs",
                "websocket": f"{settings.api_prefix}/asr/stream",
            }
        )
