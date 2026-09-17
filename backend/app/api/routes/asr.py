"""Realtime ASR WebSocket endpoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from backend.app.api.protocol import (
    ProtocolError,
    parse_client_message,
    parse_start_options,
)
from backend.app.services.asr_session import RecognitionSession

logger = logging.getLogger(__name__)
router = APIRouter(tags=["asr"])

_Message = tuple[str, Any, str | None]


async def _receive_messages(
    websocket: WebSocket,
    session: RecognitionSession,
    queue: asyncio.Queue[_Message],
) -> None:
    """Read immediately and answer ping even while model inference is running."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return

        if (payload := message.get("bytes")) is not None:
            queue.put_nowait(("audio", payload, None))
            continue

        raw = message.get("text")
        if raw is None:
            continue

        envelope = None
        try:
            envelope = parse_client_message(raw)
            if envelope.type == "ping":
                await session.send_event("pong", request_id=envelope.request_id)
            elif envelope.type in {"start", "stop"}:
                queue.put_nowait((envelope.type, envelope.data, envelope.request_id))
            else:
                raise ProtocolError(f"不支持的消息类型: {envelope.type}")
        except ProtocolError as exc:
            await session.send_event(
                "error",
                request_id=envelope.request_id if envelope else None,
                data={"code": "bad_request", "message": str(exc)},
            )


async def _process_messages(
    session: RecognitionSession,
    queue: asyncio.Queue[_Message],
) -> None:
    """Process controls and audio in order without blocking heartbeat replies."""
    while True:
        message_type, data, request_id = await queue.get()
        try:
            if message_type == "start":
                options = parse_start_options(data)
                await session.start(options, request_id)
            elif message_type == "stop":
                await session.stop(request_id)
            elif message_type == "audio":
                await session.feed_audio(data)
        except ProtocolError as exc:
            await session.send_event(
                "error",
                request_id=request_id,
                data={"code": "bad_request", "message": str(exc)},
            )
        except Exception as exc:
            logger.exception("ASR WebSocket session failed")
            await session.send_event(
                "error",
                data={"code": "internal_error", "message": f"识别服务异常: {exc}"},
            )
            return


@router.websocket("/asr/stream")
async def asr_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    settings = websocket.app.state.settings
    models = websocket.app.state.models
    speaker_store = getattr(websocket.app.state, "speaker_store", None)
    audio_store = getattr(websocket.app.state, "audio_buffer_store", None)
    session = RecognitionSession(websocket, models, settings, speaker_store, audio_store)

    await session.send_event(
        "connected",
        data={
            "protocol_version": 1,
            "audio_format": "pcm_s16le",
            "sample_rate": 16000,
            "channels": 1,
            "heartbeat_interval_ms": 15_000,
        },
    )

    queue: asyncio.Queue[_Message] = asyncio.Queue()
    receive_task = asyncio.create_task(_receive_messages(websocket, session, queue))
    process_task = asyncio.create_task(_process_messages(session, queue))

    try:
        done, pending = await asyncio.wait(
            {receive_task, process_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            exception = task.exception()
            if exception and not isinstance(exception, WebSocketDisconnect):
                logger.warning(
                    "ASR WebSocket task ended with error",
                    exc_info=(type(exception), exception, exception.__traceback__),
                )
        for task in pending:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    finally:
        for task in (receive_task, process_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(receive_task, process_task, return_exceptions=True)
        await session.abort()
        logger.info("ASR WebSocket disconnected: %s", session.session_id)


@router.get("/asr/audio/{audio_id}")
async def get_audio(audio_id: str, request: Request) -> Response:
    store = getattr(request.app.state, "audio_buffer_store", None)
    if store is None:
        return Response(status_code=404)
    wav_bytes = await asyncio.to_thread(store.get_wav, audio_id)
    if wav_bytes is None:
        return Response(status_code=404)
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )
