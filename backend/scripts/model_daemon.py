"""Persistent FunASR model daemon.

The daemon owns the model objects and can outlive multiple API process
restarts.  Run it once in the foreground with ``make model-daemon``, or let the
API start it automatically in local development.  Use ``--reload`` to reload
models in an already-running daemon.
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from multiprocessing.connection import Connection, Listener
from typing import Any

import numpy as np

from backend.app.core.config import Settings
from backend.app.core.logging import configure_logging
from backend.app.core.performance import configure_inference_threads
from backend.app.services.model_daemon import (
    ModelDaemonClient,
    ModelDaemonError,
)
from backend.app.services.model_registry import ModelRegistry

logger = logging.getLogger(__name__)


class ModelDaemonBusyError(RuntimeError):
    """Raised when models cannot be reloaded while work is in progress."""


class _StreamSession:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.lock = threading.Lock()
        self.last_used = time.monotonic()


class ModelDaemonState:
    """Own loaded models, streaming sessions and reload coordination."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._state_lock = threading.RLock()
        self._reload_lock = threading.Lock()
        self._active_requests = 0
        self._sessions: dict[str, _StreamSession] = {}
        self._shutdown = threading.Event()
        self._models = self._load_models()
        self._janitor = threading.Thread(
            target=self._cleanup_stale_sessions,
            name="model-daemon-janitor",
            daemon=True,
        )
        self._janitor.start()

    def _load_models(self, targets: tuple[str, ...] | list[str] | None = None) -> ModelRegistry:
        models = ModelRegistry(model_revision=self.settings.model_revision)
        selected = self.settings.preload_models if targets is None else tuple(targets)
        if selected:
            models.preload(selected)
        return models

    @contextmanager
    def _request(self) -> Iterator[None]:
        with self._state_lock:
            if self._shutdown.is_set():
                raise RuntimeError("模型服务正在关闭")
            self._active_requests += 1
        try:
            yield
        finally:
            with self._state_lock:
                self._active_requests -= 1

    def ping(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "status": self._models.status(),
                "pid": os.getpid(),
                "active_requests": self._active_requests,
                "sessions": len(self._sessions),
            }

    def reload(
        self,
        *,
        force: bool = True,
        targets: tuple[str, ...] | list[str] | None = None,
    ) -> dict[str, Any]:
        with self._reload_lock:
            with self._state_lock:
                if self._active_requests:
                    raise ModelDaemonBusyError("有正在进行的识别任务，无法重新加载模型")
                if self._sessions and not force:
                    raise ModelDaemonBusyError("有未关闭的流式会话，无法重新加载模型")

            # Load outside the state lock so ping and new requests stay
            # responsive while the (potentially slow) model load is running.
            new_models = self._load_models(targets)

            with self._state_lock:
                if self._active_requests:
                    raise ModelDaemonBusyError("加载期间有新的识别任务，请稍后重试重载")
                if self._sessions and not force:
                    raise ModelDaemonBusyError("加载期间有新的流式会话，请稍后重试重载")
                # The caller explicitly asked for a fresh model.  Drop idle
                # stream session references before replacing the registry.
                self._sessions.clear()
                self._models = new_models
                return {"status": self._models.status(), "pid": os.getpid()}

    def offline_transcribe(self, audio: np.ndarray, sr: int) -> dict[str, str]:
        with self._request():
            text = self._models.get_offline().transcribe(audio, sr)
            return {"text": text}

    def stream_new(self) -> dict[str, str]:
        with self._request():
            session = self._models.get_streaming().new_session()

        session_id = uuid.uuid4().hex
        with self._state_lock:
            self._sessions[session_id] = _StreamSession(session)
        return {"session_id": session_id}

    def _get_session(self, session_id: str) -> _StreamSession:
        with self._state_lock:
            holder = self._sessions.get(session_id)
            if holder is None:
                raise KeyError(f"未知流式会话: {session_id}")
            holder.last_used = time.monotonic()
            return holder

    def stream_feed(self, session_id: str, audio: np.ndarray) -> dict[str, list[str]]:
        holder = self._get_session(session_id)
        with self._request():
            with holder.lock:
                return {"partials": holder.session.feed(audio)}

    def stream_finalize(self, session_id: str) -> dict[str, list[str]]:
        holder = self._get_session(session_id)
        with self._request():
            with holder.lock:
                return {"partials": holder.session.finalize()}

    def stream_reset(self, session_id: str) -> dict[str, bool]:
        holder = self._get_session(session_id)
        with self._request():
            with holder.lock:
                holder.session.reset()
        return {"ok": True}

    def stream_close(self, session_id: str) -> dict[str, bool]:
        with self._state_lock:
            holder = self._sessions.pop(session_id, None)
        if holder is not None:
            with holder.lock:
                holder.session.close()
        return {"ok": True}

    def _cleanup_stale_sessions(self) -> None:
        ttl = self.settings.model_daemon_session_ttl
        if ttl <= 0:
            return

        while not self._shutdown.wait(60.0):
            cutoff = time.monotonic() - ttl
            stale: list[_StreamSession] = []
            with self._state_lock:
                for session_id, holder in list(self._sessions.items()):
                    if holder.last_used < cutoff:
                        stale.append(self._sessions.pop(session_id))
            for holder in stale:
                with holder.lock:
                    holder.session.close()


def _dispatch(state: ModelDaemonState, payload: dict[str, Any]) -> Any:
    operation = payload.get("op")
    if operation == "ping":
        return state.ping()
    if operation == "reload":
        return state.reload(
            force=bool(payload.get("force", True)),
            targets=payload.get("targets"),
        )
    if operation == "offline_transcribe":
        return state.offline_transcribe(
            payload["audio"],
            int(payload.get("sr", 16000)),
        )
    if operation == "stream_new":
        return state.stream_new()
    if operation == "stream_feed":
        return state.stream_feed(payload["session_id"], payload["audio"])
    if operation == "stream_finalize":
        return state.stream_finalize(payload["session_id"])
    if operation == "stream_reset":
        return state.stream_reset(payload["session_id"])
    if operation == "stream_close":
        return state.stream_close(payload["session_id"])
    if operation == "shutdown":
        return {"shutdown": True}
    raise ValueError(f"未知模型服务操作: {operation}")


def _handle_connection(connection: Connection, state: ModelDaemonState) -> None:
    request: dict[str, Any] = {}
    try:
        request = connection.recv()
        result = _dispatch(state, request)
        connection.send({"ok": True, "result": result})
        if request.get("op") == "shutdown":
            # The main thread is blocked in Listener.accept(); exit directly
            # after the client has received the acknowledgement.
            os._exit(0)
    except Exception as exc:
        logger.exception("Model daemon request failed")
        try:
            connection.send(
                {
                    "ok": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
        except Exception:
            pass
    finally:
        connection.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent FunASR model daemon")
    parser.add_argument(
        "--host",
        default=os.getenv("FUNASR_MODEL_DAEMON_HOST", "127.0.0.1"),
        help="Bind host (default: FUNASR_MODEL_DAEMON_HOST or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("FUNASR_MODEL_DAEMON_PORT", "8765")),
        help="Bind port (default: FUNASR_MODEL_DAEMON_PORT or 8765)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload models in an already-running daemon and exit",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop an already-running daemon and exit",
    )
    return parser


def _client(settings: Settings, host: str, port: int) -> ModelDaemonClient:
    return ModelDaemonClient(host, port, settings.model_daemon_authkey)


def main() -> None:
    args = _build_parser().parse_args()
    configure_logging()
    settings = Settings.from_env()
    host = args.host or settings.model_daemon_host
    port = args.port or settings.model_daemon_port

    if args.reload or args.stop:
        client = _client(settings, host, port)
        operation = "reload" if args.reload else "shutdown"
        payload: dict[str, Any] = {"op": operation}
        if args.reload:
            payload.update(
                {
                    "targets": list(settings.preload_models),
                    "force": True,
                }
            )
        try:
            result = client.request(payload, timeout=None)
        except ModelDaemonError as exc:
            logger.error("模型服务操作失败: %s", exc)
            raise SystemExit(1) from exc
        logger.info("模型服务操作完成: %s", result)
        return

    configure_inference_threads()
    authkey = settings.model_daemon_authkey.encode()

    # Reuse an existing daemon instead of failing on a duplicate start.
    existing = _client(settings, host, port)
    try:
        existing.request({"op": "ping"}, timeout=2.0)
    except ModelDaemonError:
        pass
    else:
        logger.info("模型服务已在 %s:%d 运行", host, port)
        return

    state = ModelDaemonState(settings)
    listener = Listener((host, port), authkey=authkey)
    logger.info("模型服务已启动: %s:%d pid=%d", host, port, os.getpid())

    try:
        with ThreadPoolExecutor(
            max_workers=max(1, settings.model_daemon_max_workers),
            thread_name_prefix="model-daemon",
        ) as executor:
            while True:
                connection = listener.accept()
                executor.submit(_handle_connection, connection, state)
    except KeyboardInterrupt:
        logger.info("模型服务收到退出信号")
    finally:
        listener.close()


if __name__ == "__main__":
    main()
