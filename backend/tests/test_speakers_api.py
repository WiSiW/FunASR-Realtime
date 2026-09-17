from __future__ import annotations

import time

import numpy as np
from fastapi.testclient import TestClient

from backend.app.main import app


class FakeSpeakerModel:
    def embed(self, audio: np.ndarray, sr: int = 16000) -> np.ndarray:
        return np.asarray([1.0, 0.0, 0.0], dtype=np.float32)

    def ready(self) -> None:
        return None


class FakeOfflineModel:
    def transcribe(self, audio: np.ndarray) -> str:
        return "测试文本"


class FakeModelRegistry:
    def get_speaker(self) -> FakeSpeakerModel:
        return FakeSpeakerModel()

    def get_offline(self) -> FakeOfflineModel:
        return FakeOfflineModel()

    def status(self) -> dict[str, bool]:
        return {
            "offline_loaded": True,
            "streaming_loaded": False,
            "speaker_loaded": True,
            "ready": False,
        }


def enroll_pcm(seconds: float = 1.5) -> bytes:
    samples = int(16000 * seconds)
    return np.full(samples, 12000, dtype="<i2").tobytes()


def test_speaker_enrollment_api() -> None:
    with TestClient(app) as client:
        original_registry = app.state.models
        app.state.models = FakeModelRegistry()
        try:
            response = client.post(
                "/api/v1/speakers/enroll",
                params={"name": "张三", "sample_rate": 16000},
                content=enroll_pcm(),
                headers={"Content-Type": "application/octet-stream"},
            )
            assert response.status_code == 200
            profile = response.json()
            assert profile["speaker_id"] == "speaker_01"
            assert profile["name"] == "张三"
            assert profile["sample_count"] == 1

            response = client.post(
                "/api/v1/speakers/enroll",
                params={"name": "张三", "sample_rate": 16000},
                content=enroll_pcm(),
                headers={"Content-Type": "application/octet-stream"},
            )
            assert response.status_code == 200
            assert response.json()["sample_count"] == 2

            listed = client.get("/api/v1/speakers")
            assert listed.status_code == 200
            assert len(listed.json()["items"]) == 1

            deleted = client.delete("/api/v1/speakers/speaker_01")
            assert deleted.status_code == 200
            assert client.get("/api/v1/speakers").json()["items"] == []
        finally:
            app.state.models = original_registry


def test_enrolled_speaker_is_matched_in_websocket_session() -> None:
    with TestClient(app) as client:
        original_registry = app.state.models
        app.state.models = FakeModelRegistry()
        try:
            response = client.post(
                "/api/v1/speakers/enroll",
                params={"name": "张三", "sample_rate": 16000},
                content=enroll_pcm(),
                headers={"Content-Type": "application/octet-stream"},
            )
            assert response.status_code == 200

            with client.websocket_connect("/api/v1/asr/stream") as websocket:
                assert websocket.receive_json()["type"] == "connected"
                websocket.send_json(
                    {
                        "type": "start",
                        "request_id": "start-enrolled",
                        "data": {
                            "mode": "auto",
                            "vad": {
                                "energy_threshold": 0.02,
                                "hangover_sec": 0.1,
                                "min_speech_sec": 0.1,
                                "max_speech_sec": 2,
                            },
                            "speaker": {"enabled": True},
                        },
                    }
                )
                assert websocket.receive_json()["type"] == "ready"
                assert websocket.receive_json()["data"]["state"] == "listening"
                time.sleep(0.05)

                websocket.send_bytes(np.full(1600, 12000, dtype="<i2").tobytes())
                while True:
                    event = websocket.receive_json()
                    if event["type"] == "status" and event["data"]["state"] == "speech":
                        break
                websocket.send_bytes(np.zeros(1600, dtype="<i2").tobytes())
                final = None
                while final is None:
                    event = websocket.receive_json()
                    if event["type"] == "final":
                        final = event

                assert final["data"]["speaker_id"] == "speaker_01"
                assert final["data"]["speaker_name"] == "张三"
                assert final["data"]["speaker_enrolled"] is True
        finally:
            app.state.models = original_registry
