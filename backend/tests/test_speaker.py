from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from backend.app.services import speaker as speaker_module
from backend.app.services.speaker import (
    EnrolledSpeaker,
    SpeakerEmbeddingModel,
    SpeakerTracker,
    extract_enrollment_embedding,
    normalize_embedding,
)


def test_speaker_tracker_assigns_stable_ids() -> None:
    tracker = SpeakerTracker(
        similarity_threshold=0.75,
        min_segment_sec=0.5,
        max_speakers=4,
    )

    first = tracker.assign(np.asarray([1.0, 0.0, 0.0]), duration_sec=1.0)
    same = tracker.assign(np.asarray([0.98, 0.02, 0.0]), duration_sec=1.0)
    second = tracker.assign(np.asarray([0.0, 1.0, 0.0]), duration_sec=1.0)

    assert first.speaker_id == "speaker_01"
    assert first.is_new is True
    assert same.speaker_id == "speaker_01"
    assert same.is_new is False
    assert second.speaker_id == "speaker_02"
    assert second.is_new is True


def test_speaker_tracker_short_noise_inherits_previous_speaker() -> None:
    tracker = SpeakerTracker(
        similarity_threshold=0.75,
        min_segment_sec=0.8,
        max_speakers=4,
    )
    tracker.assign(np.asarray([1.0, 0.0, 0.0]), duration_sec=1.2)

    short_noise = tracker.assign(np.asarray([0.0, 1.0, 0.0]), duration_sec=0.2)

    assert short_noise.speaker_id == "speaker_01"
    assert short_noise.pending is True
    assert len(tracker.profiles) == 1


def test_speaker_tracker_ambiguous_similarity_keeps_existing_speaker() -> None:
    tracker = SpeakerTracker(
        similarity_threshold=0.8,
        new_speaker_threshold=0.4,
        min_segment_sec=0.1,
        max_speakers=4,
    )
    tracker.assign(np.asarray([1.0, 0.0, 0.0]), duration_sec=1.0)

    ambiguous = tracker.assign(
        np.asarray([0.6, 0.8, 0.0]),
        duration_sec=1.0,
    )

    assert ambiguous.speaker_id == "speaker_01"
    assert ambiguous.pending is True
    assert len(tracker.profiles) == 1


def test_speaker_tracker_stream_window_does_not_create_new_speaker() -> None:
    tracker = SpeakerTracker(
        similarity_threshold=0.7,
        new_speaker_threshold=0.35,
        min_segment_sec=0.1,
        max_speakers=4,
    )
    tracker.assign(np.asarray([1.0, 0.0, 0.0]), duration_sec=1.0)

    window = tracker.assign(
        np.asarray([0.0, 1.0, 0.0]),
        duration_sec=1.0,
        update=False,
    )
    assert window.speaker_id == "speaker_01"
    assert window.pending is True
    assert len(tracker.profiles) == 1

    final = tracker.assign(
        np.asarray([0.0, 1.0, 0.0]),
        duration_sec=1.0,
        update=True,
    )
    assert final.speaker_id == "speaker_02"
    assert final.is_new is True


def test_speaker_tracker_max_speakers_uses_closest_existing() -> None:
    tracker = SpeakerTracker(
        similarity_threshold=0.9,
        min_segment_sec=0.1,
        max_speakers=2,
    )
    tracker.assign(np.asarray([1.0, 0.0, 0.0]), duration_sec=1.0)
    tracker.assign(np.asarray([0.0, 1.0, 0.0]), duration_sec=1.0)

    assignment = tracker.assign(np.asarray([0.0, 0.0, 1.0]), duration_sec=1.0)

    assert assignment.speaker_id in {"speaker_01", "speaker_02"}
    assert assignment.pending is True
    assert len(tracker.profiles) == 2


def test_normalize_embedding_returns_unit_vector() -> None:
    normalized = normalize_embedding(np.asarray([3.0, 4.0], dtype=np.float32))

    assert np.allclose(np.linalg.norm(normalized), 1.0)


def test_speaker_embedding_model_parses_spk_embedding(monkeypatch) -> None:
    class FakeAutoModel:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def generate(self, **kwargs):
            return [{"spk_embedding": np.asarray([[3.0, 4.0]], dtype=np.float32)}]

    monkeypatch.setattr(
        speaker_module,
        "configure_inference_threads",
        lambda: 2,
    )
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeAutoModel),
    )

    model = SpeakerEmbeddingModel()
    embedding = model.embed(np.ones(1600, dtype=np.float32))

    assert np.allclose(embedding, np.asarray([0.6, 0.8], dtype=np.float32))


def test_speaker_tracker_matches_enrolled_speaker() -> None:
    tracker = SpeakerTracker(
        similarity_threshold=0.7,
        new_speaker_threshold=0.35,
        min_segment_sec=0.1,
        enrolled_profiles=[
            EnrolledSpeaker(
                speaker_id="speaker_01",
                name="张三",
                centroid=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            )
        ],
        enrolled_match_threshold=0.6,
    )

    match = tracker.assign(
        np.asarray([0.98, 0.02, 0.0]),
        duration_sec=1.0,
    )

    assert match.speaker_id == "speaker_01"
    assert match.speaker_name == "张三"
    assert match.is_enrolled is True
    assert tracker.profiles == ()


def test_speaker_tracker_uses_unknown_namespace_when_enrolled_profiles_exist() -> None:
    tracker = SpeakerTracker(
        similarity_threshold=0.7,
        new_speaker_threshold=0.35,
        min_segment_sec=0.1,
        enrolled_profiles=[
            EnrolledSpeaker(
                speaker_id="speaker_01",
                name="张三",
                centroid=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            )
        ],
        enrolled_match_threshold=0.7,
    )

    unknown = tracker.assign(
        np.asarray([0.0, 1.0, 0.0]),
        duration_sec=1.0,
    )

    assert unknown.speaker_id == "unknown_01"
    assert unknown.is_enrolled is False
    assert len(tracker.profiles) == 1


def test_speaker_tracker_does_not_force_mid_similarity_into_enrolled_speaker() -> None:
    tracker = SpeakerTracker(
        similarity_threshold=0.8,
        new_speaker_threshold=0.45,
        min_segment_sec=0.1,
        enrolled_profiles=[
            EnrolledSpeaker(
                speaker_id="speaker_01",
                name="张三",
                centroid=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            )
        ],
        enrolled_match_threshold=0.8,
    )

    assignment = tracker.assign(
        np.asarray([0.6, 0.8, 0.0]),
        duration_sec=1.0,
    )

    assert assignment.speaker_id == "unknown_01"
    assert assignment.is_enrolled is False
    assert assignment.speaker_name is None


class FakeEmbedder:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self.embeddings = iter(embeddings)

    def embed(self, audio: np.ndarray, sr: int = 16000) -> np.ndarray:
        return np.asarray(next(self.embeddings), dtype=np.float32)


def test_extract_enrollment_embedding_keeps_consistent_windows() -> None:
    embedder = FakeEmbedder(
        [
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
            [0.98, 0.02, 0.0],
        ]
    )

    embedding = extract_enrollment_embedding(
        embedder,
        np.ones(32000, dtype=np.float32),
        16000,
        window_sec=1.0,
        hop_sec=0.5,
        min_consistency=0.8,
    )

    assert float(np.dot(embedding, np.asarray([1.0, 0.0, 0.0]))) > 0.99


def test_extract_enrollment_embedding_rejects_inconsistent_windows() -> None:
    embedder = FakeEmbedder(
        [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ]
    )

    with pytest.raises(ValueError, match="一致性太低"):
        extract_enrollment_embedding(
            embedder,
            np.ones(24000, dtype=np.float32),
            16000,
            window_sec=1.0,
            hop_sec=0.5,
            min_consistency=0.8,
        )
