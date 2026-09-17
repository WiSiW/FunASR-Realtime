"""Client helpers for the persistent FunASR model daemon.

The FastAPI process must be restartable without paying the model-loading cost
again.  In daemon mode all model objects live in a separate long-lived process;
the API only sends inference requests over a local TCP connection.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client
from pathlib import Path
from typing import Any

import numpy as np

from backend.app.core.config import Settings

logger = logging.getLogger(__name__)
_PING_TIMEOUT_SECONDS = 2.0
DAEMON_PROTOCOL_VERSION = 2


class ModelDaemonError(RuntimeError):
    """Raised when the persistent model daemon is unavailable or fails."""


class ModelDaemonClient:
    """Open a short-lived local connection for one daemon operation."""

    def __init__(self, host: str, port: int, authkey: str | bytes) -> None:
        self.host = host
        self.port = port
        self.authkey = authkey.encode() if isinstance(authkey, str) else authkey

    def request(self, payload: dict[str, Any], timeout: float | None = None) -> Any:
        try:
            with Client(
                (self.host, self.port),
                authkey=self.authkey,
            ) as connection:
                connection.send(payload)
                if not connection.poll(timeout):
                    raise TimeoutError(f"模型服务 {self.host}:{self.port} 响应超时")
                response = connection.recv()
        except (OSError, EOFError, AuthenticationError) as exc:
            raise ModelDaemonError(
                f"无法连接模型服务 {self.host}:{self.port}: {exc}"
            ) from exc

        if not isinstance(response, dict) or not response.get("ok"):
            message = (
                response.get("error", "模型服务返回了无效响应")
                if isinstance(response, dict)
                else "模型服务返回了无效响应"
            )
            raise ModelDaemonError(str(message))
        return response.get("result")


class RemoteOfflineModel:
    """Proxy that keeps the public ``ASR.transcribe`` interface."""

    def __init__(self, client: ModelDaemonClient) -> None:
        self.client = client

    def transcribe(
        self,
        audio: np.ndarray,
        sr: int = 16000,
        *,
        show_progress: bool = False,
    ) -> str:
        result = self.client.request(
            {
                "op": "offline_transcribe",
                "audio": audio,
                "sr": sr,
            }
        )
        return str((result or {}).get("text", ""))


class RemoteStreamingSession:
    """Proxy for one stateful streaming decoder session in the daemon."""

    def __init__(self, client: ModelDaemonClient, session_id: str) -> None:
        self.client = client
        self.session_id = session_id
        self._closed = False

    def feed(self, audio: np.ndarray) -> list[str]:
        result = self.client.request(
            {
                "op": "stream_feed",
                "session_id": self.session_id,
                "audio": audio,
            }
        )
        return list((result or {}).get("partials", []))

    def finalize(self) -> list[str]:
        result = self.client.request(
            {
                "op": "stream_finalize",
                "session_id": self.session_id,
            }
        )
        return list((result or {}).get("partials", []))

    def reset(self) -> None:
        self.client.request(
            {
                "op": "stream_reset",
                "session_id": self.session_id,
            },
            timeout=30.0,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.client.request(
                {
                    "op": "stream_close",
                    "session_id": self.session_id,
                },
                timeout=5.0,
            )
        except ModelDaemonError:
            # Closing is best effort.  The daemon also expires idle sessions.
            logger.debug("Failed to close remote stream session %s", self.session_id, exc_info=True)


class RemoteStreamingModel:
    """Proxy that creates stateful sessions in the daemon."""

    def __init__(self, client: ModelDaemonClient) -> None:
        self.client = client

    def new_session(self) -> RemoteStreamingSession:
        result = self.client.request({"op": "stream_new"})
        session_id = str((result or {}).get("session_id", ""))
        if not session_id:
            raise ModelDaemonError("模型服务未返回流式会话 ID")
        return RemoteStreamingSession(self.client, session_id)


class RemoteSpeakerEmbeddingModel:
    """Proxy for the daemon-hosted CAM++ embedding model."""

    def __init__(self, client: ModelDaemonClient) -> None:
        self.client = client

    def embed(self, audio: np.ndarray, sr: int = 16000) -> np.ndarray:
        result = self.client.request(
            {
                "op": "speaker_embed",
                "audio": audio,
                "sr": sr,
            }
        )
        embedding = (result or {}).get("embedding", [])
        return np.asarray(embedding, dtype=np.float32).reshape(-1)

    def ready(self) -> None:
        self.client.request({"op": "speaker_ready"}, timeout=None)


class RemoteModelRegistry:
    """ModelRegistry-compatible facade backed by the persistent daemon."""

    def __init__(self, client: ModelDaemonClient) -> None:
        self.client = client
        self._offline = RemoteOfflineModel(client)
        self._streaming = RemoteStreamingModel(client)
        self._speaker = RemoteSpeakerEmbeddingModel(client)

    def get_offline(self) -> RemoteOfflineModel:
        return self._offline

    def get_streaming(self) -> RemoteStreamingModel:
        return self._streaming

    def get_speaker(self) -> RemoteSpeakerEmbeddingModel:
        return self._speaker

    def preload(self, targets: tuple[str, ...] | list[str] | set[str]) -> None:
        # The daemon owns model loading.  Ping so startup fails visibly if it
        # disappeared between the initial check and this call.
        self.client.request({"op": "ping"}, timeout=5.0)

    def status(self) -> dict[str, bool | int | str]:
        result = self.client.request({"op": "ping"}, timeout=5.0)
        return dict((result or {}).get("status", {}))

    def reload(
        self,
        targets: tuple[str, ...] | list[str] | set[str],
        *,
        force: bool = True,
    ) -> Any:
        return self.client.request(
            {
                "op": "reload",
                "targets": list(targets),
                "force": force,
            },
            timeout=None,
        )


def model_daemon_client(settings: Settings) -> ModelDaemonClient:
    return ModelDaemonClient(
        settings.model_daemon_host,
        settings.model_daemon_port,
        settings.model_daemon_authkey,
    )


def ensure_model_daemon(settings: Settings) -> tuple[ModelDaemonClient, bool]:
    """Return a live daemon client and whether this call started it."""
    client = model_daemon_client(settings)
    ping = _ping_result(client)
    if ping is not None:
        protocol_version = _protocol_version(ping)
        if protocol_version >= DAEMON_PROTOCOL_VERSION:
            logger.info(
                "Reusing persistent model daemon at %s:%d",
                settings.model_daemon_host,
                settings.model_daemon_port,
            )
            return client, False

        logger.warning(
            "Detected old model daemon protocol %s; restarting daemon",
            protocol_version or "unknown",
        )
        _stop_old_daemon(client)

    started = False
    if settings.model_daemon_autostart:
        _start_model_daemon(settings)
        started = True

    deadline = time.monotonic() + settings.model_daemon_start_timeout
    while time.monotonic() < deadline:
        ping = _ping_result(client)
        if ping is not None and _protocol_version(ping) >= DAEMON_PROTOCOL_VERSION:
            logger.info(
                "Persistent model daemon is ready at %s:%d",
                settings.model_daemon_host,
                settings.model_daemon_port,
            )
            return client, started
        time.sleep(0.5)

    if not settings.model_daemon_autostart:
        raise ModelDaemonError(
            "模型常驻服务未运行，且已禁用自动启动。请先执行 "
            "`make model-daemon`，或设置 FUNASR_MODEL_DAEMON=off 使用旧模式。"
        )
    log_path = Path(settings.model_daemon_log).expanduser()
    raise ModelDaemonError(f"模型常驻服务启动超时，请查看日志: {log_path}")


def reload_model_daemon(settings: Settings) -> Any:
    """Reload models in an already-running daemon."""
    client = model_daemon_client(settings)
    return client.request(
        {
            "op": "reload",
            "targets": list(settings.preload_models),
            "force": True,
        },
        timeout=None,
    )


def _ping_result(client: ModelDaemonClient) -> dict[str, Any] | None:
    try:
        result = client.request({"op": "ping"}, timeout=_PING_TIMEOUT_SECONDS)
    except ModelDaemonError:
        return None
    return result if isinstance(result, dict) else {}


def _protocol_version(ping: dict[str, Any]) -> int:
    try:
        return int(ping.get("protocol_version", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _stop_old_daemon(client: ModelDaemonClient) -> None:
    try:
        client.request({"op": "shutdown"}, timeout=5.0)
    except ModelDaemonError:
        pass

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if _ping_result(client) is None:
            return
        time.sleep(0.2)
    logger.warning("Old model daemon did not stop in time; attempting to start a new one")


def _start_model_daemon(settings: Settings) -> None:
    project_root = Path(__file__).resolve().parents[3]
    log_path = Path(settings.model_daemon_log).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "backend.scripts.model_daemon",
        "--host",
        settings.model_daemon_host,
        "--port",
        str(settings.model_daemon_port),
    ]
    environment = os.environ.copy()
    with log_path.open("ab") as log_file:
        popen_kwargs: dict[str, Any] = {
            "cwd": project_root,
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": log_file,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if os.name == "posix":
            # Detach from the API process group so Ctrl+C and uvicorn --reload
            # do not terminate the model process.
            popen_kwargs["start_new_session"] = True
        subprocess.Popen(command, **popen_kwargs)
    logger.info("Started persistent model daemon; log file: %s", log_path)
