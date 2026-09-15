from __future__ import annotations

import threading
import time

import numpy as np
from fastapi.testclient import TestClient

from backend.app.main import app


class FakeOfflineModel:
    def transcribe(self, audio: np.ndarray) -> str:
        return f"识别 {audio.size} 点"


class SlowOfflineModel:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def transcribe(self, audio: np.ndarray) -> str:
        self.started.set()
        self.release.wait(timeout=5)
        return "慢速识别完成"


class FakeStreamingSession:
    active = False

    def feed(self, audio: np.ndarray) -> list[str]:
        self.active = self.active or audio.size > 0
        return ["你好"] if audio.size else []

    def finalize(self) -> list[str]:
        if not self.active:
            return []
        self.active = False
        return ["你好世界"]


class FakeStreamingModel:
    def new_session(self) -> FakeStreamingSession:
        return FakeStreamingSession()


class FakeModelRegistry:
    def __init__(self, offline_model: FakeOfflineModel | SlowOfflineModel | None = None) -> None:
        self.offline_model = offline_model or FakeOfflineModel()

    def get_offline(self) -> FakeOfflineModel | SlowOfflineModel:
        return self.offline_model

    def get_streaming(self) -> FakeStreamingModel:
        return FakeStreamingModel()

    def status(self) -> dict[str, bool]:
        return {"offline_loaded": True, "streaming_loaded": True}


def pcm_block(value: int, samples: int = 1600) -> bytes:
    return np.full(samples, value, dtype="<i2").tobytes()


def test_streaming_websocket_session() -> None:
    with TestClient(app) as client:
        original_registry = app.state.models
        app.state.models = FakeModelRegistry()
        try:
            with client.websocket_connect("/api/v1/asr/stream") as websocket:
                assert websocket.receive_json()["type"] == "connected"

                websocket.send_json(
                    {
                        "type": "start",
                        "request_id": "start-1",
                        "data": {
                            "mode": "stream",
                            "vad": {
                                "energy_threshold": 0.02,
                                "hangover_sec": 0.1,
                                "min_speech_sec": 0.1,
                                "max_speech_sec": 2,
                            },
                        },
                    }
                )
                assert websocket.receive_json()["type"] == "ready"
                assert websocket.receive_json()["data"]["state"] == "listening"

                websocket.send_bytes(pcm_block(12000))
                events = []
                while not any(item["type"] == "partial" for item in events):
                    events.append(websocket.receive_json())
                assert any(item["data"].get("state") == "speech" for item in events)

                websocket.send_bytes(pcm_block(0))
                events = []
                while not any(item["type"] == "final" for item in events):
                    events.append(websocket.receive_json())
                final = next(item for item in events if item["type"] == "final")
                assert final["data"]["text"] == "你好世界"

                websocket.send_json({"type": "stop", "request_id": "stop-1"})
                stopped = websocket.receive_json()
                while stopped["type"] != "stopped":
                    stopped = websocket.receive_json()
                assert stopped["data"]["session_id"]
        finally:
            app.state.models = original_registry


def test_ping_responds_while_offline_model_is_busy() -> None:
    slow_model = SlowOfflineModel()
    with TestClient(app) as client:
        original_registry = app.state.models
        app.state.models = FakeModelRegistry(slow_model)
        try:
            with client.websocket_connect("/api/v1/asr/stream") as websocket:
                assert websocket.receive_json()["type"] == "connected"
                websocket.send_json(
                    {
                        "type": "start",
                        "request_id": "start-slow",
                        "data": {
                            "mode": "auto",
                            "vad": {
                                "energy_threshold": 0.02,
                                "hangover_sec": 0.1,
                                "min_speech_sec": 0.1,
                                "max_speech_sec": 2,
                            },
                        },
                    }
                )
                assert websocket.receive_json()["type"] == "ready"
                assert websocket.receive_json()["data"]["state"] == "listening"

                websocket.send_bytes(pcm_block(12000))
                while True:
                    event = websocket.receive_json()
                    if event["type"] == "status" and event["data"]["state"] == "speech":
                        break

                websocket.send_bytes(pcm_block(0))
                while True:
                    event = websocket.receive_json()
                    if event["type"] == "status" and event["data"]["state"] == "processing":
                        break
                assert slow_model.started.wait(timeout=1)

                release_timer = threading.Timer(2.0, slow_model.release.set)
                release_timer.start()
                started = time.monotonic()
                websocket.send_json({"type": "ping", "request_id": "ping-while-busy"})
                pong = websocket.receive_json()
                elapsed = time.monotonic() - started

                assert pong["type"] == "pong"
                assert pong["request_id"] == "ping-while-busy"
                assert elapsed < 1.0

                slow_model.release.set()
                release_timer.cancel()
                while websocket.receive_json()["type"] != "final":
                    pass
        finally:
            app.state.models = original_registry
