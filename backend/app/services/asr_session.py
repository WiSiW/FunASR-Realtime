"""Per-WebSocket recognition session state machine."""

from __future__ import annotations

import asyncio
import time
import uuid

import numpy as np
from fastapi import WebSocket

from backend.app.api.protocol import (
    ProtocolError,
    RecognitionMode,
    StartOptions,
    decode_pcm_s16le,
    event,
)
from backend.app.core.config import Settings
from backend.app.services.asr_streaming import StreamingDecodeSession, merge_stream_text
from backend.app.services.audio_vad import EnergyVAD, rms_energy
from backend.app.services.model_registry import ModelRegistry


class RecognitionSession:
    """Process PCM sent over one WebSocket using the selected recognition mode."""

    def __init__(
        self,
        websocket: WebSocket,
        models: ModelRegistry,
        settings: Settings,
    ) -> None:
        self.websocket = websocket
        self.models = models
        self.settings = settings
        self.session_id = uuid.uuid4().hex
        self.options: StartOptions | None = None
        self.active = False
        self.mode: RecognitionMode | None = None
        self.vad: EnergyVAD | None = None
        self._segment_index = 0
        self._push_blocks: list[np.ndarray] = []
        self._push_samples = 0
        self._partial_text = ""
        self.streaming: StreamingDecodeSession | None = None
        self._last_level_sent = 0.0
        self._send_lock = asyncio.Lock()

    async def start(self, options: StartOptions, request_id: str | None) -> None:
        if self.active:
            raise ProtocolError("当前会话已启动，请先发送 stop")

        self.options = options
        self.mode = options.mode
        self.session_id = uuid.uuid4().hex
        self._segment_index = 0
        self._push_blocks = []
        self._push_samples = 0
        self._partial_text = ""
        await self._close_streaming()
        self._last_level_sent = 0.0
        self.vad = EnergyVAD(
            sample_rate=options.sample_rate,
            energy_threshold=(
                options.vad.energy_threshold
                if options.vad.energy_threshold is not None
                else self.settings.default_energy_threshold
            ),
            hangover_sec=(
                options.vad.hangover_sec
                if options.vad.hangover_sec is not None
                else self.settings.default_hangover_sec
            ),
            min_speech_sec=(
                options.vad.min_speech_sec
                if options.vad.min_speech_sec is not None
                else self.settings.default_min_speech_sec
            ),
            max_speech_sec=(
                options.vad.max_speech_sec
                if options.vad.max_speech_sec is not None
                else self.settings.default_max_speech_sec
            ),
        )

        # Load before emitting ready so the client does not send audio too early.
        if options.mode in (RecognitionMode.AUTO, RecognitionMode.PUSH):
            await asyncio.to_thread(self.models.get_offline)
        else:
            streaming_model = await asyncio.to_thread(self.models.get_streaming)
            self.streaming = await asyncio.to_thread(streaming_model.new_session)

        self.active = True
        await self._send(
            event(
                "ready",
                request_id=request_id,
                data={
                    "session_id": self.session_id,
                    "mode": options.mode.value,
                    "sample_rate": options.sample_rate,
                    "audio_format": options.audio_format,
                },
            )
        )
        await self._send_status("listening")

    async def feed_audio(self, payload: bytes) -> None:
        if not self.active or self.mode is None or self.options is None or self.vad is None:
            raise ProtocolError("请先发送 start 再发送音频数据")

        audio = decode_pcm_s16le(payload)
        if audio.size == 0:
            return
        energy = rms_energy(audio)
        await self._send_level(energy)

        if self.mode is RecognitionMode.PUSH:
            self._push_blocks.append(audio)
            self._push_samples += audio.size
            if self._push_samples > self.settings.max_push_sec * self.options.sample_rate:
                raise ProtocolError(
                    f"push 录音超过 {self.settings.max_push_sec} 秒上限，请停止后重试"
                )
            return

        update = self.vad.accept(audio)
        if update.speech_started:
            self._partial_text = ""
            await self._send_status("speech")

        if self.mode is RecognitionMode.AUTO:
            if update.utterance is not None:
                await self._transcribe_offline(update.utterance)
            elif update.speech_ended:
                await self._send_status("listening")
            return

        await self._feed_streaming(audio, update)

    async def stop(self, request_id: str | None) -> None:
        if not self.active or self.mode is None or self.vad is None:
            await self._send(
                event(
                    "stopped",
                    request_id=request_id,
                    data={"session_id": self.session_id, "already_stopped": True},
                )
            )
            return

        if self.mode is RecognitionMode.PUSH:
            await self._transcribe_push()
        elif self.mode is RecognitionMode.AUTO:
            pending = self.vad.flush()
            if pending is not None:
                await self._transcribe_offline(pending)
        else:
            await self._finish_streaming(flush_vad=True)

        await self._close_streaming()
        self.active = False
        self._partial_text = ""
        await self._send_status("stopped")
        await self._send(
            event(
                "stopped",
                request_id=request_id,
                data={"session_id": self.session_id},
            )
        )

    async def abort(self) -> None:
        """Release per-session state; cached models remain available."""
        self.active = False
        await self._close_streaming()
        self.vad = None
        self._push_blocks = []
        self._push_samples = 0
        self._partial_text = ""

    async def _close_streaming(self) -> None:
        if self.streaming is None:
            return
        streaming = self.streaming
        self.streaming = None
        close = getattr(streaming, "close", None)
        if close is not None:
            await asyncio.to_thread(close)

    async def _transcribe_offline(self, audio: np.ndarray) -> None:
        self._segment_index += 1
        segment_id = f"seg-{self._segment_index:04d}"
        await self._send_status("processing")
        started = time.time()
        model = await asyncio.to_thread(self.models.get_offline)
        text = await asyncio.to_thread(model.transcribe, audio)
        elapsed_ms = round((time.time() - started) * 1000)
        await self._send(
            event(
                "final",
                data={
                    "session_id": self.session_id,
                    "segment_id": segment_id,
                    "text": text,
                    "duration_ms": round(audio.size / 16000 * 1000),
                    "latency_ms": elapsed_ms,
                },
            )
        )
        await self._send_status("listening")

    async def _transcribe_push(self) -> None:
        if not self._push_blocks or self.options is None:
            return
        audio = np.concatenate(self._push_blocks).reshape(-1)
        self._push_blocks = []
        self._push_samples = 0
        if audio.size:
            await self._transcribe_offline(audio)
        if not self.active:
            return

    async def _feed_streaming(self, audio: np.ndarray, update) -> None:
        if self.streaming is None:
            raise RuntimeError("流式识别会话未初始化")
        partials = await asyncio.to_thread(self.streaming.feed, audio)
        if partials:
            previous_text = self._partial_text
            for piece in partials:
                self._partial_text = merge_stream_text(self._partial_text, piece)
            if self._partial_text and self._partial_text != previous_text:
                await self._send(
                    event(
                        "partial",
                        data={
                            "session_id": self.session_id,
                            "segment_id": f"seg-{self._segment_index + 1:04d}",
                            "text": self._partial_text,
                        },
                    )
                )

        if update.speech_ended:
            await self._finish_streaming(flush_vad=False)

    async def _finish_streaming(self, flush_vad: bool) -> None:
        if self.options is None or self.vad is None or self.streaming is None:
            return
        # Streaming audio was fed as it arrived. flush() only closes the VAD
        # utterance; feeding it again here would duplicate recognition.
        if flush_vad:
            self.vad.flush()

        final_parts = await asyncio.to_thread(self.streaming.finalize)
        for piece in final_parts:
            self._partial_text = merge_stream_text(self._partial_text, piece)
        text = self._partial_text.strip()
        if text:
            self._segment_index += 1
            await self._send(
                event(
                    "final",
                    data={
                        "session_id": self.session_id,
                        "segment_id": f"seg-{self._segment_index:04d}",
                        "text": text,
                    },
                )
            )
        self._partial_text = ""
        await self._send_status("listening" if self.active else "stopped")

    async def send_event(
        self,
        event_type: str,
        *,
        request_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        """Send a protocol event, serializing concurrent heartbeat writes."""
        await self._send(event(event_type, request_id=request_id, data=data))

    async def _send_status(self, state: str) -> None:
        await self._send(event("status", data={"session_id": self.session_id, "state": state}))

    async def _send_level(self, energy: float) -> None:
        now = time.monotonic()
        if now - self._last_level_sent < 0.1:
            return
        self._last_level_sent = now
        await self._send(
            event(
                "level",
                data={"session_id": self.session_id, "energy": round(min(energy, 1.0), 5)},
            )
        )

    async def _send(self, payload: str) -> None:
        async with self._send_lock:
            await self.websocket.send_text(payload)
