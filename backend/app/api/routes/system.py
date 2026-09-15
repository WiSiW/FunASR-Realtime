"""Service discovery and health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request) -> dict:
    models = request.app.state.models
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": settings.app_name,
        "preload_models": settings.preload_models,
        "models": models.status(),
    }


@router.get("/modes")
async def modes() -> dict:
    return {
        "items": [
            {
                "id": "auto",
                "name": "自动分段",
                "description": "静音自动断句，使用离线模型和标点恢复",
            },
            {
                "id": "stream",
                "name": "实时流式",
                "description": "边说边出字，适合低延迟实时字幕",
            },
            {
                "id": "push",
                "name": "按键说话",
                "description": "持续采集，停止后统一识别并恢复标点",
            },
        ]
    }
