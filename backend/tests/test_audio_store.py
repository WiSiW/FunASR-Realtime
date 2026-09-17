from __future__ import annotations

import numpy as np

from backend.app.services.audio_store import AudioBufferStore


def test_audio_buffer_store_returns_wav() -> None:
    store = AudioBufferStore()
    store.create_session("session-1", 16000)
    store.append(
        "session-1",
        np.full(1600, 0.1, dtype=np.float32),
        16000,
    )
    store.append(
        "session-1",
        np.full(1600, 0.2, dtype=np.float32),
        16000,
    )

    wav_bytes = store.get_wav("session-1")

    assert wav_bytes is not None
    assert wav_bytes[:4] == b"RIFF"
    assert len(wav_bytes) > 44


def test_audio_buffer_store_segment_and_delete() -> None:
    store = AudioBufferStore()
    store.put_segment(
        "segment-1",
        np.full(800, 0.1, dtype=np.float32),
        16000,
    )

    assert store.get_wav("segment-1") is not None
    assert store.delete("segment-1") is True
    assert store.get_wav("segment-1") is None
