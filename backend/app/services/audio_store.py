"""In-memory PCM buffers for original-audio playback and diagnostics."""

from __future__ import annotations

import io
import threading
import time
import wave
from dataclasses import dataclass, field

import numpy as np


@dataclass(slots=True)
class _AudioBuffer:
    sample_rate: int
    chunks: list[np.ndarray] = field(default_factory=list)
    size_bytes: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0


class AudioBufferStore:
    """Keep bounded, thread-safe PCM buffers addressable by audio ID."""

    def __init__(
        self,
        *,
        ttl_sec: float = 3600.0,
        max_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self.ttl_sec = ttl_sec
        self.max_bytes = max_bytes
        self._entries: dict[str, _AudioBuffer] = {}
        self._total_bytes = 0
        self._lock = threading.RLock()

    def create_session(self, audio_id: str, sample_rate: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            self._put_locked(
                audio_id,
                _AudioBuffer(
                    sample_rate=sample_rate,
                    created_at=now,
                    updated_at=now,
                ),
            )

    def append(self, audio_id: str, audio: np.ndarray, sample_rate: int) -> None:
        pcm = _to_pcm16(audio)
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            entry = self._entries.get(audio_id)
            if entry is None or entry.sample_rate != sample_rate:
                entry = _AudioBuffer(
                    sample_rate=sample_rate,
                    created_at=now,
                    updated_at=now,
                )
                self._put_locked(audio_id, entry)
            entry.chunks.append(pcm)
            entry.size_bytes += int(pcm.nbytes)
            entry.updated_at = now
            self._total_bytes += int(pcm.nbytes)
            self._trim_locked()

    def put_segment(self, audio_id: str, audio: np.ndarray, sample_rate: int) -> None:
        pcm = _to_pcm16(audio)
        now = time.monotonic()
        with self._lock:
            self._cleanup_locked(now)
            self._put_locked(
                audio_id,
                _AudioBuffer(
                    sample_rate=sample_rate,
                    chunks=[pcm],
                    size_bytes=int(pcm.nbytes),
                    created_at=now,
                    updated_at=now,
                ),
            )
            self._trim_locked()

    def get_wav(self, audio_id: str) -> bytes | None:
        with self._lock:
            self._cleanup_locked(time.monotonic())
            entry = self._entries.get(audio_id)
            if entry is None:
                return None
            chunks = [chunk.copy() for chunk in entry.chunks]
            sample_rate = entry.sample_rate
        return _encode_wav(chunks, sample_rate)

    def delete(self, audio_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(audio_id)
            if entry is None:
                return False
            self._remove_locked(audio_id)
            return True

    def _put_locked(self, audio_id: str, entry: _AudioBuffer) -> None:
        existing = self._entries.get(audio_id)
        if existing is not None:
            self._total_bytes -= existing.size_bytes
        self._entries[audio_id] = entry
        self._total_bytes += entry.size_bytes

    def _remove_locked(self, audio_id: str) -> None:
        entry = self._entries.pop(audio_id, None)
        if entry is not None:
            self._total_bytes -= entry.size_bytes

    def _cleanup_locked(self, now: float) -> None:
        if self.ttl_sec <= 0:
            return
        expired = [
            audio_id
            for audio_id, entry in self._entries.items()
            if now - entry.updated_at > self.ttl_sec
        ]
        for audio_id in expired:
            self._remove_locked(audio_id)

    def _trim_locked(self) -> None:
        if self.max_bytes <= 0 or self._total_bytes <= self.max_bytes:
            return
        while self._total_bytes > self.max_bytes and self._entries:
            oldest_id = min(
                self._entries,
                key=lambda audio_id: self._entries[audio_id].updated_at,
            )
            self._remove_locked(oldest_id)


def _to_pcm16(audio: np.ndarray) -> np.ndarray:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2")


def _encode_wav(chunks: list[np.ndarray], sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        if chunks:
            wav_file.writeframes(np.concatenate(chunks).tobytes())
        else:
            wav_file.writeframes(b"")
    return buffer.getvalue()
