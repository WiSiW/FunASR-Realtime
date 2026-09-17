"""Voiceprint enrollment and speaker library endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from backend.app.api.protocol import ProtocolError, decode_pcm_s16le
from backend.app.services.audio_vad import rms_energy
from backend.app.services.speaker import (
    EnrolledSpeaker,
    extract_enrollment_embedding,
)

router = APIRouter(prefix="/speakers", tags=["speakers"])

_MIN_ENROLL_SEC = 1.0
_MAX_ENROLL_SEC = 60.0


def _profile_dict(profile: EnrolledSpeaker) -> dict[str, object]:
    return {
        "speaker_id": profile.speaker_id,
        "name": profile.name,
        "dimension": int(profile.centroid.size),
        "sample_count": profile.sample_count,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _speaker_store(request: Request):
    store = getattr(request.app.state, "speaker_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="声纹库尚未初始化")
    return store


@router.get("")
async def list_speakers(request: Request) -> dict:
    store = _speaker_store(request)
    profiles = await asyncio.to_thread(store.list_profiles)
    return {"items": [_profile_dict(profile) for profile in profiles]}


@router.post("/enroll")
async def enroll_speaker(
    request: Request,
    name: str = Query(..., min_length=1, max_length=64),
    sample_rate: int = Query(default=16000),
) -> dict:
    if sample_rate != 16000:
        raise HTTPException(status_code=400, detail="当前只支持 16000 Hz PCM")

    body = await request.body()
    try:
        audio = decode_pcm_s16le(body)
    except ProtocolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    duration_sec = audio.size / sample_rate
    if duration_sec < _MIN_ENROLL_SEC:
        raise HTTPException(status_code=400, detail="注册音频至少需要 1 秒")
    if duration_sec > _MAX_ENROLL_SEC:
        raise HTTPException(status_code=400, detail="注册音频最长 60 秒")
    energy_threshold = max(
        0.003,
        float(getattr(request.app.state.settings, "default_energy_threshold", 0.012))
        * 0.5,
    )
    if rms_energy(audio) < energy_threshold:
        raise HTTPException(status_code=400, detail="没有检测到有效语音，请重新录制")

    models = request.app.state.models
    try:
        model = await asyncio.to_thread(models.get_speaker)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"说话人模型不可用: {exc}",
        ) from exc
    try:
        embedding = await asyncio.to_thread(
            extract_enrollment_embedding,
            model,
            audio,
            sample_rate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"说话人 embedding 提取失败: {exc}",
        ) from exc

    store = _speaker_store(request)
    try:
        profile = await asyncio.to_thread(store.upsert, name, embedding)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _profile_dict(profile)


@router.delete("/{speaker_id}")
async def delete_speaker(speaker_id: str, request: Request) -> dict:
    store = _speaker_store(request)
    deleted = await asyncio.to_thread(store.delete, speaker_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="说话人不存在")
    return {"deleted": True, "speaker_id": speaker_id}
