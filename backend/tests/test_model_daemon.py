from __future__ import annotations

import threading
from multiprocessing import Pipe
from multiprocessing.connection import Listener

import numpy as np
import pytest

from backend.app.core.config import Settings
from backend.app.services import model_daemon as model_daemon_client
from backend.app.services.model_daemon import ModelDaemonClient
from backend.scripts import model_daemon


class FakeOfflineModel:
    def transcribe(self, audio: np.ndarray, sr: int) -> str:
        return f"fake:{audio.size}:{sr}"


class FakeStreamingSession:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def feed(self, audio: np.ndarray) -> list[str]:
        text = f"p{audio.size}"
        self.parts.append(text)
        return [text]

    def finalize(self) -> list[str]:
        return ["end"]

    def reset(self) -> None:
        self.parts = []

    def close(self) -> None:
        self.parts = []


class FakeStreamingModel:
    def new_session(self) -> FakeStreamingSession:
        return FakeStreamingSession()


class FakeModelRegistry:
    def __init__(self, model_revision: str = "v2.0.4") -> None:
        self.model_revision = model_revision
        self.offline = FakeOfflineModel()
        self.streaming = FakeStreamingModel()

    def preload(self, targets) -> None:
        return None

    def get_offline(self) -> FakeOfflineModel:
        return self.offline

    def get_streaming(self) -> FakeStreamingModel:
        return self.streaming

    def status(self) -> dict[str, bool]:
        return {"offline_loaded": True, "streaming_loaded": True, "ready": True}


def test_model_daemon_protocol(monkeypatch) -> None:
    monkeypatch.setattr(model_daemon, "ModelRegistry", FakeModelRegistry)
    settings = Settings(
        preload_models=(),
        model_daemon_session_ttl=0,
        model_daemon_max_workers=2,
    )
    state = model_daemon.ModelDaemonState(settings)

    try:
        listener = Listener(("127.0.0.1", 0), authkey=b"test-auth-key")
    except PermissionError:
        pytest.skip("当前测试沙箱不允许绑定本地 TCP 端口")
    host, port = listener.address

    def serve() -> None:
        try:
            while True:
                connection = listener.accept()
                model_daemon._handle_connection(connection, state)
        except OSError:
            pass

    server_thread = threading.Thread(target=serve, daemon=True)
    server_thread.start()
    client = ModelDaemonClient(host, port, b"test-auth-key")
    try:
        ping = client.request({"op": "ping"}, timeout=2.0)
        assert ping["status"]["ready"] is True

        audio = np.arange(1600, dtype=np.float32)
        offline = client.request(
            {"op": "offline_transcribe", "audio": audio, "sr": 16000},
            timeout=2.0,
        )
        assert offline["text"] == "fake:1600:16000"

        created = client.request({"op": "stream_new"}, timeout=2.0)
        session_id = created["session_id"]
        fed = client.request(
            {"op": "stream_feed", "session_id": session_id, "audio": audio},
            timeout=2.0,
        )
        assert fed["partials"] == ["p1600"]
        finalized = client.request(
            {"op": "stream_finalize", "session_id": session_id},
            timeout=2.0,
        )
        assert finalized["partials"] == ["end"]
        client.request({"op": "stream_close", "session_id": session_id}, timeout=2.0)

        reloaded = client.request({"op": "reload", "force": True}, timeout=2.0)
        assert reloaded["status"]["ready"] is True
    finally:
        listener.close()


def test_model_daemon_state_operations(monkeypatch) -> None:
    monkeypatch.setattr(model_daemon, "ModelRegistry", FakeModelRegistry)
    settings = Settings(
        preload_models=(),
        model_daemon_session_ttl=0,
        model_daemon_max_workers=2,
    )
    state = model_daemon.ModelDaemonState(settings)

    assert state.ping()["status"]["ready"] is True

    audio = np.arange(1200, dtype=np.float32)
    assert state.offline_transcribe(audio, 16000)["text"] == "fake:1200:16000"

    session_id = state.stream_new()["session_id"]
    assert state.stream_feed(session_id, audio)["partials"] == ["p1200"]
    assert state.stream_finalize(session_id)["partials"] == ["end"]
    assert state.stream_close(session_id) == {"ok": True}

    assert state.reload(force=True)["status"]["ready"] is True


class FakeConnection:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.sent = None

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args) -> None:
        return None

    def send(self, payload: dict) -> None:
        self.sent = payload

    def poll(self, timeout: float | None) -> bool:
        return True

    def recv(self) -> dict:
        return self.response


class FakeServerConnection(FakeConnection):
    def __init__(self, request: dict) -> None:
        super().__init__({})
        self.request = request
        self.closed = False

    def recv(self) -> dict:
        return self.request

    def close(self) -> None:
        self.closed = True


def test_model_daemon_client_request(monkeypatch) -> None:
    fake_connection = FakeConnection({"ok": True, "result": {"value": 42}})
    monkeypatch.setattr(
        model_daemon_client,
        "Client",
        lambda *args, **kwargs: fake_connection,
    )

    client = ModelDaemonClient("127.0.0.1", 8765, "secret")
    assert client.request({"op": "ping"}, timeout=1.0) == {"value": 42}
    assert fake_connection.sent == {"op": "ping"}


def test_model_daemon_client_error(monkeypatch) -> None:
    fake_connection = FakeConnection({"ok": False, "error": "reload failed"})
    monkeypatch.setattr(
        model_daemon_client,
        "Client",
        lambda *args, **kwargs: fake_connection,
    )

    client = ModelDaemonClient("127.0.0.1", 8765, "secret")
    with pytest.raises(model_daemon_client.ModelDaemonError, match="reload failed"):
        client.request({"op": "reload"}, timeout=1.0)


def test_model_daemon_handle_connection(monkeypatch) -> None:
    monkeypatch.setattr(model_daemon, "ModelRegistry", FakeModelRegistry)
    state = model_daemon.ModelDaemonState(
        Settings(preload_models=(), model_daemon_session_ttl=0)
    )
    connection = FakeServerConnection(
        {
            "op": "offline_transcribe",
            "audio": np.arange(800, dtype=np.float32),
            "sr": 16000,
        }
    )

    model_daemon._handle_connection(connection, state)

    assert connection.sent["ok"] is True
    assert connection.sent["result"]["text"] == "fake:800:16000"
    assert connection.closed is True


def test_model_daemon_pipe_protocol(monkeypatch) -> None:
    monkeypatch.setattr(model_daemon, "ModelRegistry", FakeModelRegistry)
    state = model_daemon.ModelDaemonState(
        Settings(preload_models=(), model_daemon_session_ttl=0)
    )
    client_connection, server_connection = Pipe()
    server_thread = threading.Thread(
        target=model_daemon._handle_connection,
        args=(server_connection, state),
        daemon=True,
    )
    server_thread.start()
    try:
        client_connection.send(
            {
                "op": "offline_transcribe",
                "audio": np.arange(400, dtype=np.float32),
                "sr": 16000,
            }
        )
        assert client_connection.poll(2.0)
        response = client_connection.recv()
        assert response["ok"] is True
        assert response["result"]["text"] == "fake:400:16000"
    finally:
        client_connection.close()
        server_thread.join(timeout=2.0)


def test_ensure_model_daemon_reuses_running_process(monkeypatch) -> None:
    monkeypatch.setattr(model_daemon_client, "_ping", lambda client: True)

    client, started = model_daemon_client.ensure_model_daemon(Settings())

    assert isinstance(client, ModelDaemonClient)
    assert started is False


def test_ensure_model_daemon_starts_when_missing(monkeypatch) -> None:
    started_processes: list[Settings] = []
    # First ping fails, then the freshly started daemon responds.
    ping_results = iter([False, True])
    monkeypatch.setattr(
        model_daemon_client,
        "_ping",
        lambda client: next(ping_results),
    )
    monkeypatch.setattr(
        model_daemon_client,
        "_start_model_daemon",
        lambda settings: started_processes.append(settings),
    )

    client, started = model_daemon_client.ensure_model_daemon(Settings())

    assert isinstance(client, ModelDaemonClient)
    assert started is True
    assert len(started_processes) == 1


def test_ensure_model_daemon_requires_running_process_when_autostart_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setattr(model_daemon_client, "_ping", lambda client: False)

    with pytest.raises(model_daemon_client.ModelDaemonError, match="已禁用自动启动"):
        model_daemon_client.ensure_model_daemon(
            Settings(
                model_daemon_autostart=False,
                model_daemon_start_timeout=0.01,
            )
        )
