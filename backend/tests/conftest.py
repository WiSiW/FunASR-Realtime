from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_model_preloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Keep unit tests fast and independent from FunASR model downloads."""
    monkeypatch.setenv("FUNASR_PRELOAD_MODELS", "")
    monkeypatch.setenv("FUNASR_MODEL_DAEMON", "off")
    monkeypatch.setenv(
        "FUNASR_SPEAKER_DB",
        str(tmp_path / "speakers.sqlite3"),
    )
