"""FunASR streaming decoder with isolated state for every WebSocket client."""

from __future__ import annotations

import logging
import threading

import numpy as np

logger = logging.getLogger(__name__)

_CHUNK_SIZE = [0, 10, 5]
_ENCODER_LOOK_BACK = 4
_DECODER_LOOK_BACK = 1
_FRAME_SAMPLES = 960


class StreamingDecodeSession:
    """Own cache and PCM buffer for one utterance/session."""

    def __init__(self, model, inference_lock: threading.Lock) -> None:
        self.model = model
        self.inference_lock = inference_lock
        self.chunk_stride = _CHUNK_SIZE[1] * _FRAME_SAMPLES
        self.reset()

    def reset(self) -> None:
        self._cache: dict = {}
        self._buffer = np.zeros(0, dtype=np.float32)

    @staticmethod
    def _extract(result) -> list[str]:
        texts: list[str] = []
        if result:
            for item in result:
                text = item.get("text", "") if isinstance(item, dict) else ""
                if text:
                    texts.append(text)
        return texts

    def feed(self, audio: np.ndarray) -> list[str]:
        if audio.size == 0:
            return []
        self._buffer = np.concatenate([self._buffer, audio.astype(np.float32).reshape(-1)])
        outputs: list[str] = []
        while len(self._buffer) >= self.chunk_stride:
            chunk = self._buffer[: self.chunk_stride]
            self._buffer = self._buffer[self.chunk_stride :]
            with self.inference_lock:
                result = self.model.generate(
                    input=chunk,
                    cache=self._cache,
                    is_final=0,
                    chunk_size=_CHUNK_SIZE,
                    encoder_chunk_look_back=_ENCODER_LOOK_BACK,
                    decoder_chunk_look_back=_DECODER_LOOK_BACK,
                )
            outputs.extend(self._extract(result))
        return outputs

    def finalize(self) -> list[str]:
        outputs: list[str] = []
        if len(self._buffer) > 0:
            with self.inference_lock:
                result = self.model.generate(
                    input=self._buffer,
                    cache=self._cache,
                    is_final=1,
                    chunk_size=_CHUNK_SIZE,
                    encoder_chunk_look_back=_ENCODER_LOOK_BACK,
                    decoder_chunk_look_back=_DECODER_LOOK_BACK,
                )
            outputs.extend(self._extract(result))
        self.reset()
        return outputs


class StreamingASR:
    """Load the streaming model once; create independent sessions per client."""

    def __init__(self, model_revision: str = "v2.0.4"):
        from funasr import AutoModel

        logger.info("正在加载流式 FunASR 模型 paraformer-zh-streaming …")
        self.model = AutoModel(
            model="paraformer-zh-streaming",
            model_revision=model_revision,
        )
        self.chunk_stride = _CHUNK_SIZE[1] * _FRAME_SAMPLES
        self._inference_lock = threading.Lock()
        logger.info("流式模型加载完成。")

    def new_session(self) -> StreamingDecodeSession:
        return StreamingDecodeSession(self.model, self._inference_lock)

    # Compatibility methods keep the existing CLI implementation working.
    @property
    def _session(self) -> StreamingDecodeSession:
        if not hasattr(self, "_cli_session"):
            self._cli_session = self.new_session()
        return self._cli_session

    def reset(self) -> None:
        self._session.reset()

    def feed(self, audio: np.ndarray) -> list[str]:
        return self._session.feed(audio)

    def finalize(self) -> list[str]:
        return self._session.finalize()
