from __future__ import annotations

import pytest

from backend.app.services import model_registry


class FakeOfflineModel:
    calls = 0

    def __init__(self, model_revision: str) -> None:
        self.model_revision = model_revision
        type(self).calls += 1


class FakeStreamingModel:
    calls = 0

    def __init__(self, model_revision: str) -> None:
        self.model_revision = model_revision
        type(self).calls += 1


def test_preload_loads_selected_models_once(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeOfflineModel.calls = 0
    FakeStreamingModel.calls = 0
    monkeypatch.setattr(model_registry, "ASR", FakeOfflineModel)
    monkeypatch.setattr(model_registry, "StreamingASR", FakeStreamingModel)

    registry = model_registry.ModelRegistry(model_revision="test-revision")
    registry.preload(("offline", "streaming"))
    registry.preload(("offline", "streaming"))

    assert FakeOfflineModel.calls == 1
    assert FakeStreamingModel.calls == 1
    assert registry.status() == {
        "offline_loaded": True,
        "streaming_loaded": True,
        "ready": True,
    }


def test_preload_rejects_unknown_model() -> None:
    registry = model_registry.ModelRegistry()

    with pytest.raises(ValueError, match="未知预加载模型"):
        registry.preload(("offline", "unknown"))
