"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes import asr, speakers, system
from backend.app.core.config import Settings
from backend.app.core.logging import configure_logging
from backend.app.services.audio_store import AudioBufferStore
from backend.app.services.model_daemon import RemoteModelRegistry, ensure_model_daemon
from backend.app.services.model_registry import ModelRegistry
from backend.app.services.speaker_store import SpeakerProfileStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    configure_logging()
    app.state.settings = settings
    app.state.speaker_store = SpeakerProfileStore(settings.speaker_db_path)
    app.state.audio_buffer_store = AudioBufferStore()

    def build_registry():
        if settings.model_daemon_enabled:
            client, daemon_started = ensure_model_daemon(settings)
            registry = RemoteModelRegistry(client)
            if settings.reload_models and not daemon_started:
                logger.info("Reloading models in the persistent model daemon")
                registry.reload(settings.preload_models, force=True)
            registry.preload(settings.preload_models)
            return registry

        registry = ModelRegistry(
            model_revision=settings.model_revision,
            speaker_model=settings.speaker_model,
            speaker_model_revision=settings.speaker_model_revision,
        )
        if settings.preload_models:
            logger.info(
                "Preloading FunASR models before startup: %s",
                ", ".join(settings.preload_models),
            )
            registry.preload(settings.preload_models)
            logger.info("FunASR model preloading completed")
        else:
            logger.warning(
                "Model preloading is disabled; the first recognition request may load models"
            )
        return registry

    try:
        registry = await asyncio.to_thread(build_registry)
    except Exception:
        logger.exception("FunASR model initialization failed")
        if settings.preload_strict:
            raise
        logger.warning("Falling back to lazy in-process model loading")
        registry = ModelRegistry(
            model_revision=settings.model_revision,
            speaker_model=settings.speaker_model,
            speaker_model_revision=settings.speaker_model_revision,
        )
    app.state.models = registry

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


@app.middleware("http")
async def allow_browser_extension_local_network_access(
    request: Request,
    call_next,
):
    """Allow extension clients to pass Chromium private-network preflights."""
    origin = request.headers.get("origin", "")
    is_extension_origin = origin.startswith(
        ("chrome-extension://", "moz-extension://")
    )
    private_network_request = (
        request.headers.get("access-control-request-private-network", "").lower()
        == "true"
    )

    if (
        is_extension_origin
        and request.method == "OPTIONS"
        and private_network_request
    ):
        requested_headers = request.headers.get(
            "access-control-request-headers",
            "*",
        )
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": requested_headers,
                "Access-Control-Allow-Private-Network": "true",
                "Access-Control-Max-Age": "600",
                "Vary": "Origin",
            },
        )

    response = await call_next(request)
    if is_extension_origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        response.headers["Vary"] = "Origin"
    return response


app.include_router(system.router, prefix=settings.api_prefix)
app.include_router(asr.router, prefix=settings.api_prefix)
app.include_router(speakers.router, prefix=settings.api_prefix)


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
