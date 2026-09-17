"""Per-WebSocket recognition session state machine."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import suppress

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
from backend.app.services.audio_store import AudioBufferStore
from backend.app.services.audio_vad import EnergyVAD, rms_energy
from backend.app.services.model_registry import ModelRegistry
from backend.app.services.speaker import SpeakerAssignment, SpeakerTracker
from backend.app.services.speaker_store import SpeakerProfileStore

logger = logging.getLogger(__name__)


class RecognitionSession:
    """Process PCM sent over one WebSocket using the selected recognition mode."""

    def __init__(
        self,
        websocket: WebSocket,
        models: ModelRegistry,
        settings: Settings,
        speaker_store: SpeakerProfileStore | None = None,
        audio_store: AudioBufferStore | None = None,
    ) -> None:
        self.websocket = websocket
        self.models = models
        self.settings = settings
        self.speaker_store = speaker_store
        self.audio_store = audio_store
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
        self.speaker_tracker: SpeakerTracker | None = None
        self._speaker_assign_lock = asyncio.Lock()
        self._speaker_task: asyncio.Task | None = None
        self._speaker_warmup_task: asyncio.Task | None = None
        self._speaker_ready = False
        self._speaker_generation = 0
        self._speaker_window_samples = 0
        self._speaker_interval_sec = 0.8
        self._speaker_max_utterance_samples = 0
        self._stream_utterance_audio = np.zeros(0, dtype=np.float32)
        self._stream_speaker_buffer = np.zeros(0, dtype=np.float32)
        self._stream_speaker_id: str | None = None
        self._last_speaker_update_at = 0.0
        self._stream_pre_roll = np.zeros(0, dtype=np.float32)
        self._stream_pre_roll_samples = 0

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
        if self.audio_store is not None:
            await asyncio.to_thread(
                self.audio_store.create_session,
                self.session_id,
                options.sample_rate,
            )
        await self._close_streaming()
        await self._reset_stream_speaker_state()
        await self._cancel_speaker_warmup()
        self.speaker_tracker = None
        self._last_level_sent = 0.0
        if options.speaker.enabled:
            enrolled_profiles = []
            if self.speaker_store is not None:
                try:
                    enrolled_profiles = await asyncio.to_thread(
                        self.speaker_store.list_profiles
                    )
                except Exception:
                    logger.exception("读取声纹库失败，将只使用临时说话人跟踪")
            self.speaker_tracker = SpeakerTracker(
                similarity_threshold=(
                    options.speaker.similarity_threshold
                    if options.speaker.similarity_threshold is not None
                    else self.settings.speaker_similarity_threshold
                ),
                new_speaker_threshold=(
                    options.speaker.new_speaker_threshold
                    if options.speaker.new_speaker_threshold is not None
                    else self.settings.speaker_new_threshold
                ),
                switch_margin=(
                    options.speaker.switch_margin
                    if options.speaker.switch_margin is not None
                    else self.settings.speaker_switch_margin
                ),
                min_segment_sec=(
                    options.speaker.min_segment_sec
                    if options.speaker.min_segment_sec is not None
                    else self.settings.speaker_min_segment_sec
                ),
                max_speakers=(
                    options.speaker.max_speakers
                    if options.speaker.max_speakers is not None
                    else self.settings.speaker_max_speakers
                ),
                centroid_update_alpha=(
                    options.speaker.centroid_update_alpha
                    if options.speaker.centroid_update_alpha is not None
                    else self.settings.speaker_centroid_update_alpha
                ),
                enrolled_profiles=enrolled_profiles,
                enrolled_match_threshold=self.settings.speaker_enrolled_match_threshold,
            )
            self._speaker_window_samples = max(
                1,
                int(
                    (
                        options.speaker.embedding_window_sec
                        if options.speaker.embedding_window_sec is not None
                        else self.settings.speaker_embedding_window_sec
                    )
                    * options.sample_rate
                ),
            )
            self._speaker_interval_sec = max(
                0.2,
                options.speaker.embedding_interval_sec
                if options.speaker.embedding_interval_sec is not None
                else self.settings.speaker_embedding_interval_sec,
            )
            self._speaker_max_utterance_samples = int(
                (
                    options.vad.max_speech_sec
                    if options.vad.max_speech_sec is not None
                    else self.settings.default_max_speech_sec
                )
                * options.sample_rate
            )
            self._speaker_ready = False
            self._speaker_warmup_task = asyncio.create_task(self._warmup_speaker())
        pre_roll_sec = (
            options.vad.pre_roll_sec
            if options.vad.pre_roll_sec is not None
            else self.settings.default_pre_roll_sec
        )
        self._stream_pre_roll = np.zeros(0, dtype=np.float32)
        self._stream_pre_roll_samples = max(0, int(pre_roll_sec * options.sample_rate))
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
            pre_roll_sec=pre_roll_sec,
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
                    "session_audio_id": self.session_id,
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
        if self.audio_store is not None and self.options is not None:
            await asyncio.to_thread(
                self.audio_store.append,
                self.session_id,
                audio,
                self.options.sample_rate,
            )
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
        if self.mode is RecognitionMode.STREAM:
            self._append_stream_pre_roll(audio)
        if update.speech_started:
            self._partial_text = ""
            await self._reset_streaming_decoder()
            await self._send_status("speech")

        if self.mode is RecognitionMode.AUTO:
            if update.utterance is not None:
                await self._transcribe_offline(update.utterance)
            elif update.speech_ended:
                await self._send_status("listening")
            return

        if update.speech_started:
            audio_to_feed = self._take_stream_pre_roll()
            if audio_to_feed.size == 0:
                audio_to_feed = audio
            await self._track_stream_speaker(audio_to_feed, update)
            await self._feed_streaming(audio_to_feed, update)
        elif update.speaking or update.speech_ended:
            await self._track_stream_speaker(audio, update)
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
        self._clear_stream_pre_roll()
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
        await self._reset_stream_speaker_state()
        self._clear_stream_pre_roll()
        await self._cancel_speaker_warmup()
        self.speaker_tracker = None
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

    async def _reset_streaming_decoder(self) -> None:
        if self.streaming is None:
            return
        reset = getattr(self.streaming, "reset", None)
        if reset is not None:
            await asyncio.to_thread(reset)

    def _append_stream_pre_roll(self, audio: np.ndarray) -> None:
        if self._stream_pre_roll_samples <= 0:
            return
        samples = audio.astype(np.float32, copy=False).reshape(-1)
        if samples.size == 0:
            return
        self._stream_pre_roll = np.concatenate([self._stream_pre_roll, samples])
        if self._stream_pre_roll.size > self._stream_pre_roll_samples:
            self._stream_pre_roll = self._stream_pre_roll[
                -self._stream_pre_roll_samples :
            ]

    def _take_stream_pre_roll(self) -> np.ndarray:
        pre_roll = self._stream_pre_roll
        self._stream_pre_roll = np.zeros(0, dtype=np.float32)
        return pre_roll

    def _clear_stream_pre_roll(self) -> None:
        self._stream_pre_roll = np.zeros(0, dtype=np.float32)

    async def _transcribe_offline(self, audio: np.ndarray) -> None:
        self._segment_index += 1
        segment_id = f"seg-{self._segment_index:04d}"
        audio_id: str | None = None
        if self.audio_store is not None:
            audio_id = uuid.uuid4().hex
            await asyncio.to_thread(
                self.audio_store.put_segment,
                audio_id,
                audio,
                self.options.sample_rate if self.options is not None else 16000,
            )
        await self._send_status("processing")
        started = time.time()

        async def transcribe() -> str:
            model = await asyncio.to_thread(self.models.get_offline)
            return await asyncio.to_thread(model.transcribe, audio)

        if self.speaker_tracker is not None:
            text, assignment = await asyncio.gather(
                transcribe(),
                self._safe_assign_speaker(audio),
            )
        else:
            text = await transcribe()
            assignment = None

        elapsed_ms = round((time.time() - started) * 1000)
        data: dict = {
            "session_id": self.session_id,
            "segment_id": segment_id,
            "text": text,
            "duration_ms": round(audio.size / 16000 * 1000),
            "latency_ms": elapsed_ms,
        }
        if audio_id is not None:
            data["audio_id"] = audio_id
        data.update(self._speaker_event_fields(assignment))
        await self._send(
            event(
                "final",
                data=data,
            )
        )
        await self._send_status("listening")

    async def _safe_assign_speaker(
        self,
        audio: np.ndarray,
        *,
        update_tracker: bool = True,
    ) -> SpeakerAssignment | None:
        tracker = self.speaker_tracker
        if tracker is None or audio.size == 0:
            return None
        if not self._speaker_ready:
            return None
        try:
            async with self._speaker_assign_lock:
                embedding = await asyncio.to_thread(self._embed_speaker, audio)
                sample_rate = self.options.sample_rate if self.options is not None else 16000
                return tracker.assign(
                    embedding,
                    duration_sec=audio.size / sample_rate,
                    update=update_tracker,
                )
        except Exception:
            logger.exception("说话人识别失败，当前片段不标记说话人")
            return None

    def _embed_speaker(self, audio: np.ndarray) -> np.ndarray:
        model = self.models.get_speaker()
        return model.embed(audio)

    async def _warmup_speaker(self) -> None:
        tracker = self.speaker_tracker
        try:
            # Force both the local and daemon-backed speaker model to load.
            model = await asyncio.to_thread(self.models.get_speaker)
            await asyncio.to_thread(model.ready)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("说话人模型预热失败，将不标记说话人")
            if self.speaker_tracker is tracker:
                self.speaker_tracker = None
            self._speaker_ready = False
        else:
            self._speaker_ready = True
            logger.info("说话人模型已就绪")

    async def _cancel_speaker_warmup(self) -> None:
        task = self._speaker_warmup_task
        self._speaker_warmup_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._speaker_ready = False

    @staticmethod
    def _speaker_event_fields(
        assignment: SpeakerAssignment | None,
    ) -> dict[str, bool | float | str | None]:
        if assignment is None:
            return {}
        return {
            "speaker_id": assignment.speaker_id,
            "speaker_confidence": round(assignment.confidence, 4),
            "speaker_pending": assignment.pending,
            "speaker_is_new": assignment.is_new,
            "speaker_name": assignment.speaker_name,
            "speaker_enrolled": assignment.is_enrolled,
        }

    async def _transcribe_push(self) -> None:
        if not self._push_blocks or self.options is None:
            return
        audio = np.concatenate(self._push_blocks).reshape(-1)
        self._push_blocks = []
        self._push_samples = 0
        if audio.size == 0:
            return

        # Push mode buffers raw audio.  Re-run the configured VAD so multi-
        # speaker recordings become separate utterances with their own labels.
        utterances: list[np.ndarray] = []
        block_size = max(1, int(0.16 * self.options.sample_rate))
        for start in range(0, audio.size, block_size):
            update = self.vad.accept(audio[start : start + block_size]) if self.vad else None
            if update is not None and update.utterance is not None:
                utterances.append(update.utterance)
        if self.vad is not None:
            pending = self.vad.flush()
            if pending is not None:
                utterances.append(pending)

        if not utterances:
            utterances = [audio]
        for utterance in utterances:
            if not self.active:
                return
            await self._transcribe_offline(utterance)

    async def _feed_streaming(self, audio: np.ndarray, update) -> None:
        if self.streaming is None:
            raise RuntimeError("流式识别会话未初始化")
        partials = await asyncio.to_thread(self.streaming.feed, audio)
        if partials:
            previous_text = self._partial_text
            for piece in partials:
                self._partial_text = merge_stream_text(self._partial_text, piece)
            if self._partial_text and self._partial_text != previous_text:
                data: dict = {
                    "session_id": self.session_id,
                    "segment_id": f"seg-{self._segment_index + 1:04d}",
                    "text": self._partial_text,
                }
                if self.speaker_tracker is not None:
                    if self._stream_speaker_id is not None:
                        data["speaker_id"] = self._stream_speaker_id
                    data["speaker_pending"] = self._stream_speaker_id is None
                await self._send(
                    event(
                        "partial",
                        data=data,
                    )
                )

        if update.speech_ended:
            await self._finish_streaming(
                flush_vad=False,
                utterance=update.utterance,
            )

    async def _finish_streaming(
        self,
        flush_vad: bool,
        utterance: np.ndarray | None = None,
    ) -> None:
        if self.options is None or self.vad is None or self.streaming is None:
            return
        # Streaming audio was fed as it arrived. flush() only closes the VAD
        # utterance; feeding it again here would duplicate recognition.
        if flush_vad:
            utterance = self.vad.flush()

        await self._wait_speaker_task()
        speaker_audio = (
            utterance
            if utterance is not None and utterance.size > 0
            else self._stream_utterance_audio
        )

        if self.speaker_tracker is not None and speaker_audio.size > 0:
            final_parts, assignment = await asyncio.gather(
                asyncio.to_thread(self.streaming.finalize),
                self._safe_assign_speaker(speaker_audio),
            )
        else:
            final_parts = await asyncio.to_thread(self.streaming.finalize)
            assignment = None
        for piece in final_parts:
            self._partial_text = merge_stream_text(self._partial_text, piece)
        text = self._partial_text.strip()
        if text:
            self._segment_index += 1
            audio_id: str | None = None
            if self.audio_store is not None and speaker_audio.size > 0:
                audio_id = uuid.uuid4().hex
                await asyncio.to_thread(
                    self.audio_store.put_segment,
                    audio_id,
                    speaker_audio,
                    self.options.sample_rate,
                )
            if assignment is not None:
                self._stream_speaker_id = assignment.speaker_id
            data: dict = {
                "session_id": self.session_id,
                "segment_id": f"seg-{self._segment_index:04d}",
                "text": text,
            }
            if assignment is not None:
                data.update(self._speaker_event_fields(assignment))
            elif self.speaker_tracker is not None and self._stream_speaker_id is not None:
                data["speaker_id"] = self._stream_speaker_id
            if audio_id is not None:
                data["audio_id"] = audio_id
            await self._send(
                event(
                    "final",
                    data=data,
                )
            )
        self._partial_text = ""
        await self._reset_stream_speaker_state()
        self._clear_stream_pre_roll()
        self._speaker_generation += 1
        await self._send_status("listening" if self.active else "stopped")

    async def _track_stream_speaker(self, audio: np.ndarray, update) -> None:
        if self.options is None:
            return

        if update.speech_started:
            await self._reset_stream_speaker_state()
            self._speaker_generation += 1

        samples = audio.astype(np.float32, copy=False).reshape(-1)
        if samples.size == 0:
            return
        self._stream_utterance_audio = np.concatenate(
            [self._stream_utterance_audio, samples]
        )
        if self._speaker_max_utterance_samples > 0:
            self._stream_utterance_audio = self._stream_utterance_audio[
                -self._speaker_max_utterance_samples :
            ]
        if self.speaker_tracker is None:
            return
        self._stream_speaker_buffer = np.concatenate(
            [self._stream_speaker_buffer, samples]
        )
        if self._speaker_window_samples > 0:
            self._stream_speaker_buffer = self._stream_speaker_buffer[
                -self._speaker_window_samples :
            ]

        if not self._speaker_ready:
            return
        minimum_samples = int(
            self.speaker_tracker.min_segment_sec * self.options.sample_rate
        )
        if self._stream_utterance_audio.size < minimum_samples:
            return
        now = time.monotonic()
        if now - self._last_speaker_update_at < self._speaker_interval_sec:
            return
        if self._speaker_task is not None and not self._speaker_task.done():
            return

        self._last_speaker_update_at = now
        generation = self._speaker_generation
        window = self._stream_speaker_buffer.copy()
        self._speaker_task = asyncio.create_task(
            self._identify_stream_speaker(window, generation)
        )

    async def _identify_stream_speaker(
        self,
        audio: np.ndarray,
        generation: int,
    ) -> None:
        assignment = await self._safe_assign_speaker(audio, update_tracker=False)
        if assignment is None or generation != self._speaker_generation:
            return
        previous = self._stream_speaker_id
        self._stream_speaker_id = assignment.speaker_id
        if previous == assignment.speaker_id:
            return
        data = {
            "session_id": self.session_id,
            "speaker_id": assignment.speaker_id,
            "status": "created" if previous is None else "changed",
        }
        data.update(self._speaker_event_fields(assignment))
        await self._send(event("speaker", data=data))

    async def _wait_speaker_task(self) -> None:
        task = self._speaker_task
        self._speaker_task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("流式说话人识别任务失败")

    async def _reset_stream_speaker_state(self) -> None:
        task = self._speaker_task
        self._speaker_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._stream_utterance_audio = np.zeros(0, dtype=np.float32)
        self._stream_speaker_buffer = np.zeros(0, dtype=np.float32)
        self._stream_speaker_id = None
        self._last_speaker_update_at = 0.0

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
