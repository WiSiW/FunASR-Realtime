"""Download and initialize models so a Docker image can ship a warm cache."""

from __future__ import annotations

import logging

from backend.app.core.config import Settings
from backend.app.core.logging import configure_logging
from backend.app.services.model_registry import ModelRegistry


def main() -> None:
    configure_logging()
    logger = logging.getLogger("preload_models")
    settings = Settings.from_env()
    targets = settings.preload_models or ("offline", "streaming")

    logger.info("Preparing FunASR model cache: %s", ", ".join(targets))
    registry = ModelRegistry(
        model_revision=settings.model_revision,
        speaker_model=settings.speaker_model,
        speaker_model_revision=settings.speaker_model_revision,
    )
    registry.preload(targets)
    logger.info("FunASR model cache is ready")


if __name__ == "__main__":
    main()
