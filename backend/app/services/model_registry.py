"""Thread-safe lazy loading for the expensive FunASR models."""

from __future__ import annotations

import threading

from backend.app.services.asr import ASR
from backend.app.services.asr_streaming import StreamingASR
from backend.app.services.speaker import SpeakerEmbeddingModel


class ModelRegistry:
    """Load each model once at startup and share it across sessions."""

    def __init__(
        self,
        model_revision: str = "v2.0.4",
        speaker_model: str = "cam++",
        speaker_model_revision: str = "master",
    ) -> None:
        self.model_revision = model_revision
        self.speaker_model = speaker_model
        self.speaker_model_revision = speaker_model_revision
        self._offline: ASR | None = None
        self._streaming: StreamingASR | None = None
        self._speaker: SpeakerEmbeddingModel | None = None
        self._lock = threading.Lock()

    def get_offline(self) -> ASR:
        if self._offline is None:
            with self._lock:
                if self._offline is None:
                    self._offline = ASR(model_revision=self.model_revision)
        return self._offline

    def get_streaming(self) -> StreamingASR:
        if self._streaming is None:
            with self._lock:
                if self._streaming is None:
                    self._streaming = StreamingASR(model_revision=self.model_revision)
        return self._streaming

    def get_speaker(self) -> SpeakerEmbeddingModel:
        if self._speaker is None:
            with self._lock:
                if self._speaker is None:
                    self._speaker = SpeakerEmbeddingModel(
                        model=self.speaker_model,
                        model_revision=self.speaker_model_revision,
                    )
        return self._speaker

    def preload(self, targets: tuple[str, ...] | list[str] | set[str]) -> None:
        """Load selected models sequentially before the API accepts requests."""
        requested = set(targets)
        unknown = requested - {"offline", "streaming", "speaker"}
        if unknown:
            raise ValueError(f"未知预加载模型: {', '.join(sorted(unknown))}")

        if "offline" in requested:
            self.get_offline()
        if "streaming" in requested:
            self.get_streaming()
        if "speaker" in requested:
            self.get_speaker()

    def status(self) -> dict[str, bool]:
        return {
            "offline_loaded": self._offline is not None,
            "streaming_loaded": self._streaming is not None,
            "speaker_loaded": self._speaker is not None,
            "ready": self._offline is not None and self._streaming is not None,
        }
