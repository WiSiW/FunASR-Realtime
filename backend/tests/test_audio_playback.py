from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from backend.app.main import app


def test_audio_playback_endpoint_returns_wav() -> None:
    with TestClient(app) as client:
        app.state.audio_buffer_store.put_segment(
            "playback-test",
            np.full(1600, 0.1, dtype=np.float32),
            16000,
        )

        response = client.get("/api/v1/asr/audio/playback-test")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/wav")
        assert response.content[:4] == b"RIFF"


def test_audio_playback_endpoint_unknown_returns_404() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/asr/audio/unknown-audio")

        assert response.status_code == 404
