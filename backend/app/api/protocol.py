"""WebSocket message contracts for browser ASR sessions."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, ValidationError


class RecognitionMode(str, Enum):
    AUTO = "auto"
    STREAM = "stream"
    PUSH = "push"


class ProtocolError(ValueError):
    """Raised when a client message does not follow the ASR protocol."""


class VADOptions(BaseModel):
    energy_threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    hangover_sec: float | None = Field(default=None, ge=0.1, le=5.0)
    min_speech_sec: float | None = Field(default=None, ge=0.05, le=3.0)
    max_speech_sec: float | None = Field(default=None, ge=1.0, le=120.0)
    pre_roll_sec: float | None = Field(default=None, ge=0.0, le=2.0)


class SpeakerOptions(BaseModel):
    enabled: bool = True
    similarity_threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    new_speaker_threshold: float | None = Field(default=None, gt=0.0, lt=1.0)
    switch_margin: float | None = Field(default=None, ge=0.0, le=1.0)
    min_segment_sec: float | None = Field(default=None, ge=0.0, le=10.0)
    max_speakers: int | None = Field(default=None, ge=1, le=50)
    embedding_window_sec: float | None = Field(default=None, ge=0.5, le=5.0)
    embedding_interval_sec: float | None = Field(default=None, ge=0.2, le=5.0)
    centroid_update_alpha: float | None = Field(default=None, gt=0.0, le=1.0)


class StartOptions(BaseModel):
    mode: RecognitionMode
    sample_rate: Literal[16000] = 16000
    channels: Literal[1] = 1
    audio_format: Literal["pcm_s16le"] = "pcm_s16le"
    vad: VADOptions = Field(default_factory=VADOptions)
    speaker: SpeakerOptions = Field(default_factory=SpeakerOptions)


class ClientEnvelope(BaseModel):
    type: str
    request_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


def parse_client_message(raw: str) -> ClientEnvelope:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("消息不是有效的 JSON") from exc

    if not isinstance(payload, dict):
        raise ProtocolError("消息必须是 JSON 对象")

    try:
        return ClientEnvelope.model_validate(payload)
    except ValidationError as exc:
        raise ProtocolError("消息字段不合法") from exc


def parse_start_options(data: dict[str, Any]) -> StartOptions:
    try:
        return StartOptions.model_validate(data)
    except ValidationError as exc:
        raise ProtocolError(f"start 参数不合法: {exc.errors()[0]['msg']}") from exc


def decode_pcm_s16le(payload: bytes) -> np.ndarray:
    """Decode little-endian signed 16-bit PCM into float32 [-1, 1]."""
    if len(payload) == 0:
        return np.zeros(0, dtype=np.float32)
    if len(payload) % 2:
        raise ProtocolError("PCM 数据长度必须为偶数")
    samples = np.frombuffer(payload, dtype="<i2").astype(np.float32)
    return samples / 32768.0


def event(
    event_type: str,
    *,
    request_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {"type": event_type, "data": data or {}}
    if request_id is not None:
        payload["request_id"] = request_id
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
