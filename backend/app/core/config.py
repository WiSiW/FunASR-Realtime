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
    max_push_sec: int = 120
    preload_models: tuple[str, ...] = ("offline", "streaming")
    preload_strict: bool = True

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
            max_push_sec=_env_int("FUNASR_MAX_PUSH_SEC", cls.max_push_sec),
            preload_models=_env_csv(
                "FUNASR_PRELOAD_MODELS",
                ",".join(cls.preload_models),
            ),
            preload_strict=_env_bool("FUNASR_PRELOAD_STRICT", cls.preload_strict),
        )
