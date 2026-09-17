"""Environment-backed backend settings."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    app_name: str = "FunASR Realtime API"
    api_prefix: str = "/api/v1"
    model_revision: str = "v2.0.4"
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    default_energy_threshold: float = 0.012
    default_hangover_sec: float = 0.6
    default_min_speech_sec: float = 0.25
    default_max_speech_sec: float = 30.0
    default_pre_roll_sec: float = 0.4
    max_push_sec: int = 120
    preload_models: tuple[str, ...] = ("offline", "streaming")
    preload_strict: bool = True
    model_daemon_enabled: bool = True
    model_daemon_autostart: bool = True
    model_daemon_host: str = "127.0.0.1"
    model_daemon_port: int = 8765
    model_daemon_authkey: str = "funasr-realtime-model-daemon"
    model_daemon_start_timeout: float = 600.0
    model_daemon_session_ttl: float = 1800.0
    model_daemon_max_workers: int = 8
    model_daemon_log: str = "~/.cache/funasr-realtime/model-daemon.log"
    reload_models: bool = False
    speaker_enabled: bool = True
    speaker_model: str = "cam++"
    speaker_model_revision: str = "master"
    speaker_db_path: str = "~/.cache/funasr-realtime/speakers.sqlite3"
    speaker_similarity_threshold: float = 0.70
    speaker_new_threshold: float = 0.45
    speaker_enrolled_match_threshold: float = 0.70
    speaker_switch_margin: float = 0.08
    speaker_min_segment_sec: float = 0.8
    speaker_max_speakers: int = 8
    speaker_embedding_window_sec: float = 1.5
    speaker_embedding_interval_sec: float = 0.8
    speaker_centroid_update_alpha: float = 0.1

    @classmethod
    def from_env(cls) -> Settings:
        origins = os.getenv(
            "FUNASR_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        )
        return cls(
            app_name=os.getenv("FUNASR_APP_NAME", cls.app_name),
            api_prefix=os.getenv("FUNASR_API_PREFIX", cls.api_prefix),
            model_revision=os.getenv("FUNASR_MODEL_REVISION", cls.model_revision),
            cors_origins=tuple(item.strip() for item in origins.split(",") if item.strip()),
            default_energy_threshold=_env_float("FUNASR_VAD_ENERGY", cls.default_energy_threshold),
            default_hangover_sec=_env_float("FUNASR_VAD_HANGOVER_SEC", cls.default_hangover_sec),
            default_min_speech_sec=_env_float(
                "FUNASR_VAD_MIN_SPEECH_SEC", cls.default_min_speech_sec
            ),
            default_max_speech_sec=_env_float(
                "FUNASR_VAD_MAX_SPEECH_SEC", cls.default_max_speech_sec
            ),
            default_pre_roll_sec=_env_float(
                "FUNASR_VAD_PRE_ROLL_SEC", cls.default_pre_roll_sec
            ),
            max_push_sec=_env_int("FUNASR_MAX_PUSH_SEC", cls.max_push_sec),
            preload_models=_env_csv(
                "FUNASR_PRELOAD_MODELS",
                ",".join(cls.preload_models),
            ),
            preload_strict=_env_bool("FUNASR_PRELOAD_STRICT", cls.preload_strict),
            model_daemon_enabled=_env_bool(
                "FUNASR_MODEL_DAEMON", cls.model_daemon_enabled
            ),
            model_daemon_autostart=_env_bool(
                "FUNASR_MODEL_DAEMON_AUTOSTART", cls.model_daemon_autostart
            ),
            model_daemon_host=os.getenv(
                "FUNASR_MODEL_DAEMON_HOST", cls.model_daemon_host
            ),
            model_daemon_port=_env_int(
                "FUNASR_MODEL_DAEMON_PORT", cls.model_daemon_port
            ),
            model_daemon_authkey=os.getenv(
                "FUNASR_MODEL_DAEMON_AUTHKEY", cls.model_daemon_authkey
            ),
            model_daemon_start_timeout=_env_float(
                "FUNASR_MODEL_DAEMON_START_TIMEOUT", cls.model_daemon_start_timeout
            ),
            model_daemon_session_ttl=_env_float(
                "FUNASR_MODEL_DAEMON_SESSION_TTL", cls.model_daemon_session_ttl
            ),
            model_daemon_max_workers=_env_int(
                "FUNASR_MODEL_DAEMON_MAX_WORKERS", cls.model_daemon_max_workers
            ),
            model_daemon_log=os.getenv(
                "FUNASR_MODEL_DAEMON_LOG", cls.model_daemon_log
            ),
            reload_models=_env_bool("FUNASR_RELOAD_MODELS", cls.reload_models),
            speaker_enabled=_env_bool("FUNASR_SPEAKER_ENABLED", cls.speaker_enabled),
            speaker_model=os.getenv("FUNASR_SPEAKER_MODEL", cls.speaker_model),
            speaker_model_revision=os.getenv(
                "FUNASR_SPEAKER_MODEL_REVISION", cls.speaker_model_revision
            ),
            speaker_db_path=os.getenv(
                "FUNASR_SPEAKER_DB", cls.speaker_db_path
            ),
            speaker_similarity_threshold=_env_float(
                "FUNASR_SPEAKER_SIMILARITY_THRESHOLD",
                cls.speaker_similarity_threshold,
            ),
            speaker_new_threshold=_env_float(
                "FUNASR_SPEAKER_NEW_THRESHOLD",
                cls.speaker_new_threshold,
            ),
            speaker_enrolled_match_threshold=_env_float(
                "FUNASR_SPEAKER_ENROLLED_MATCH_THRESHOLD",
                cls.speaker_enrolled_match_threshold,
            ),
            speaker_switch_margin=_env_float(
                "FUNASR_SPEAKER_SWITCH_MARGIN", cls.speaker_switch_margin
            ),
            speaker_min_segment_sec=_env_float(
                "FUNASR_SPEAKER_MIN_SEGMENT_SEC", cls.speaker_min_segment_sec
            ),
            speaker_max_speakers=_env_int(
                "FUNASR_SPEAKER_MAX_SPEAKERS", cls.speaker_max_speakers
            ),
            speaker_embedding_window_sec=_env_float(
                "FUNASR_SPEAKER_EMBEDDING_WINDOW_SEC",
                cls.speaker_embedding_window_sec,
            ),
            speaker_embedding_interval_sec=_env_float(
                "FUNASR_SPEAKER_EMBEDDING_INTERVAL_SEC",
                cls.speaker_embedding_interval_sec,
            ),
            speaker_centroid_update_alpha=_env_float(
                "FUNASR_SPEAKER_CENTROID_UPDATE_ALPHA",
                cls.speaker_centroid_update_alpha,
            ),
        )
