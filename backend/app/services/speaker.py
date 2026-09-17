"""Speaker embedding extraction and session-level online diarization."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np

from backend.app.core.performance import configure_inference_threads

logger = logging.getLogger(__name__)


def normalize_embedding(embedding: np.ndarray) -> np.ndarray:
    """Return an L2-normalized float32 speaker embedding."""
    vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        return vector
    return vector / norm


@dataclass(slots=True)
class SpeakerProfile:
    speaker_id: str
    centroid: np.ndarray
    segment_count: int = 0


@dataclass(slots=True)
class EnrolledSpeaker:
    speaker_id: str
    name: str
    centroid: np.ndarray
    sample_count: int = 1
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class SpeakerAssignment:
    speaker_id: str
    confidence: float
    is_new: bool = False
    pending: bool = False
    speaker_name: str | None = None
    is_enrolled: bool = False


class SpeakerEmbeddingModel:
    """Load CAM++ once and expose a simple 192-dimensional embedding API."""

    def __init__(self, model: str = "cam++", model_revision: str = "master") -> None:
        inference_threads = configure_inference_threads()
        from funasr import AutoModel

        logger.info("正在加载说话人 embedding 模型 %s …", model)
        self.model = AutoModel(
            model=model,
            model_revision=model_revision,
            disable_update=True,
            ncpu=inference_threads,
        )
        self._inference_lock = threading.Lock()
        logger.info("说话人 embedding 模型加载完成。")

    def embed(self, audio: np.ndarray, sr: int = 16000) -> np.ndarray:
        """Extract a normalized speaker embedding from mono float32 audio."""
        if audio.size == 0:
            return np.zeros(0, dtype=np.float32)

        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        with self._inference_lock:
            results = self.model.generate(
                input=samples,
                fs=sr,
                disable_pbar=True,
            )
        if not results:
            raise RuntimeError("说话人模型未返回结果")

        embedding = results[0].get("spk_embedding")
        if embedding is None:
            raise RuntimeError("说话人模型未返回 spk_embedding")
        if hasattr(embedding, "detach"):
            embedding = embedding.detach().cpu().numpy()
        return normalize_embedding(embedding)

    def ready(self) -> None:
        """Ensure the model is loaded; used by the API warmup path."""
        return None


def extract_enrollment_embedding(
    embedder,
    audio: np.ndarray,
    sample_rate: int,
    *,
    window_sec: float = 1.5,
    hop_sec: float = 0.75,
    min_consistency: float = 0.55,
    max_windows: int = 8,
) -> np.ndarray:
    """Extract a consistent centroid from one enrollment recording."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    window_samples = max(1, int(window_sec * sample_rate))
    hop_samples = max(1, int(hop_sec * sample_rate))
    if samples.size <= window_samples:
        return normalize_embedding(embedder.embed(samples))

    starts = list(range(0, samples.size - window_samples + 1, hop_samples))
    final_start = samples.size - window_samples
    if not starts or starts[-1] != final_start:
        starts.append(final_start)
    if len(starts) > max_windows:
        indices = np.linspace(0, len(starts) - 1, max_windows).round().astype(int)
        starts = [starts[int(index)] for index in indices]

    embeddings = [
        normalize_embedding(embedder.embed(samples[start : start + window_samples]))
        for start in starts
    ]
    if len(embeddings) == 1:
        return embeddings[0]

    matrix = np.asarray(
        [
            [float(np.dot(left, right)) for right in embeddings]
            for left in embeddings
        ],
        dtype=np.float32,
    )
    scores = matrix.mean(axis=1)
    best_index = int(np.argmax(scores))
    if float(scores[best_index]) < min_consistency:
        raise ValueError("注册音频一致性太低，请只让一个说话人录音")

    selected = [
        embeddings[index]
        for index in range(len(embeddings))
        if float(matrix[best_index, index]) >= min_consistency
    ]
    return normalize_embedding(np.mean(selected, axis=0))


class SpeakerTracker:
    """Assign stable speaker_01/02 IDs within one recognition session."""

    def __init__(
        self,
        *,
        similarity_threshold: float = 0.70,
        new_speaker_threshold: float = 0.45,
        switch_margin: float = 0.08,
        min_segment_sec: float = 0.8,
        max_speakers: int = 8,
        centroid_update_alpha: float = 0.1,
        enrolled_profiles: list[EnrolledSpeaker] | tuple[EnrolledSpeaker, ...] = (),
        enrolled_match_threshold: float = 0.70,
    ) -> None:
        if not 0.0 < similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold 必须在 (0, 1] 内")
        if not 0.0 < new_speaker_threshold < similarity_threshold:
            raise ValueError("new_speaker_threshold 必须在 (0, similarity_threshold) 内")
        if switch_margin < 0.0:
            raise ValueError("switch_margin 不能为负数")
        if min_segment_sec < 0.0:
            raise ValueError("min_segment_sec 不能为负数")
        if max_speakers < 1:
            raise ValueError("max_speakers 必须大于等于 1")
        if not 0.0 < centroid_update_alpha <= 1.0:
            raise ValueError("centroid_update_alpha 必须在 (0, 1] 内")
        if not 0.0 < enrolled_match_threshold <= 1.0:
            raise ValueError("enrolled_match_threshold 必须在 (0, 1] 内")

        self.similarity_threshold = similarity_threshold
        self.new_speaker_threshold = new_speaker_threshold
        self.switch_margin = switch_margin
        self.min_segment_sec = min_segment_sec
        self.max_speakers = max_speakers
        self.centroid_update_alpha = centroid_update_alpha
        self.enrolled_match_threshold = enrolled_match_threshold
        self._enrolled_profiles = [
            EnrolledSpeaker(
                speaker_id=profile.speaker_id,
                name=profile.name,
                centroid=normalize_embedding(profile.centroid),
                sample_count=profile.sample_count,
                created_at=profile.created_at,
                updated_at=profile.updated_at,
            )
            for profile in enrolled_profiles
        ]
        self.session_prefix = "unknown" if self._enrolled_profiles else "speaker"
        self._profiles: list[SpeakerProfile] = []
        self._last_speaker_id: str | None = None

    @property
    def profiles(self) -> tuple[SpeakerProfile, ...]:
        return tuple(self._profiles)

    def reset(self) -> None:
        self._profiles = []
        self._last_speaker_id = None

    @property
    def enrolled_profiles(self) -> tuple[EnrolledSpeaker, ...]:
        return tuple(self._enrolled_profiles)

    def assign(
        self,
        embedding: np.ndarray,
        *,
        duration_sec: float,
        update: bool = True,
    ) -> SpeakerAssignment:
        """Assign one utterance embedding to a stable session speaker ID."""
        vector = normalize_embedding(embedding)
        if vector.size == 0:
            if self._last_speaker_id is not None:
                return SpeakerAssignment(
                    self._last_speaker_id,
                    confidence=0.0,
                    pending=True,
                )
            profile = self._create_profile(np.zeros(1, dtype=np.float32))
            return SpeakerAssignment(
                profile.speaker_id,
                confidence=0.0,
                is_new=True,
                pending=True,
            )

        if not self._profiles:
            enrolled_assignment = self._match_enrolled(vector)
            if enrolled_assignment is not None:
                return enrolled_assignment
            profile = self._create_profile(vector)
            logger.info("新建说话人 %s（会话首个说话人）", profile.speaker_id)
            return SpeakerAssignment(profile.speaker_id, confidence=1.0, is_new=True)

        enrolled_assignment = self._match_enrolled(vector)
        if enrolled_assignment is not None:
            return enrolled_assignment

        similarities = np.asarray(
            [float(np.dot(vector, profile.centroid)) for profile in self._profiles],
            dtype=np.float32,
        )
        best_index = int(np.argmax(similarities))
        best_similarity = float(similarities[best_index])
        best_profile = self._profiles[best_index]
        current_profile = self._find_profile(self._last_speaker_id)

        # Keep the current speaker unless another existing profile is clearly
        # better.  This reduces one-off speaker flips caused by short pauses.
        if current_profile is not None:
            current_similarity = float(np.dot(vector, current_profile.centroid))
            if current_similarity >= self.similarity_threshold:
                if (
                    best_profile is not current_profile
                    and best_similarity < current_similarity + self.switch_margin
                ):
                    if update:
                        self._update_profile(current_profile, vector)
                    self._last_speaker_id = current_profile.speaker_id
                    return SpeakerAssignment(
                        current_profile.speaker_id,
                        confidence=current_similarity,
                    )

        if best_similarity >= self.similarity_threshold:
            if update:
                self._update_profile(best_profile, vector)
            self._last_speaker_id = best_profile.speaker_id
            return SpeakerAssignment(best_profile.speaker_id, confidence=best_similarity)

        # Ambiguous region: prefer the closest existing speaker instead of
        # creating a new ID for one noisy/short segment.  Adapt slowly so a
        # bad window cannot pull the centroid away.
        if best_similarity >= self.new_speaker_threshold:
            if update:
                self._update_profile(
                    best_profile,
                    vector,
                    weight=self.centroid_update_alpha * 0.25,
                )
            self._last_speaker_id = best_profile.speaker_id
            return SpeakerAssignment(
                best_profile.speaker_id,
                confidence=best_similarity,
                pending=True,
            )

        # Short inserts and noise inherit the previous speaker instead of
        # creating a new speaker ID.
        if duration_sec < self.min_segment_sec and self._last_speaker_id is not None:
            return SpeakerAssignment(
                self._last_speaker_id,
                confidence=best_similarity,
                pending=True,
            )

        if len(self._profiles) >= self.max_speakers:
            return SpeakerAssignment(
                best_profile.speaker_id,
                confidence=best_similarity,
                pending=True,
            )

        # Streaming windows are deliberately conservative: they may assign a
        # tentative existing speaker, but only the final full-utterance pass is
        # allowed to create a new speaker ID.
        if not update:
            return SpeakerAssignment(
                best_profile.speaker_id,
                confidence=best_similarity,
                pending=True,
            )

        profile = self._create_profile(vector)
        logger.info(
            "新建说话人 %s（最高相似度 %.3f，片段 %.2fs）",
            profile.speaker_id,
            best_similarity,
            duration_sec,
        )
        return SpeakerAssignment(profile.speaker_id, confidence=1.0, is_new=True)

    def _create_profile(self, centroid: np.ndarray) -> SpeakerProfile:
        profile = SpeakerProfile(
            speaker_id=f"{self.session_prefix}_{len(self._profiles) + 1:02d}",
            centroid=normalize_embedding(centroid),
            segment_count=1,
        )
        self._profiles.append(profile)
        self._last_speaker_id = profile.speaker_id
        return profile

    def _match_enrolled(self, embedding: np.ndarray) -> SpeakerAssignment | None:
        if not self._enrolled_profiles:
            return None
        similarities = np.asarray(
            [
                float(np.dot(embedding, profile.centroid))
                for profile in self._enrolled_profiles
            ],
            dtype=np.float32,
        )
        best_index = int(np.argmax(similarities))
        best_similarity = float(similarities[best_index])
        profile = self._enrolled_profiles[best_index]
        if best_similarity >= self.enrolled_match_threshold:
            return SpeakerAssignment(
                profile.speaker_id,
                confidence=best_similarity,
                speaker_name=profile.name,
                is_enrolled=True,
            )
        return None

    def _update_profile(
        self,
        profile: SpeakerProfile,
        embedding: np.ndarray,
        *,
        weight: float | None = None,
    ) -> None:
        alpha = self.centroid_update_alpha if weight is None else weight
        updated = (
            (1.0 - alpha) * profile.centroid
            + alpha * embedding
        )
        profile.centroid = normalize_embedding(updated)
        profile.segment_count += 1

    def _find_profile(self, speaker_id: str | None) -> SpeakerProfile | None:
        if speaker_id is None:
            return None
        for profile in self._profiles:
            if profile.speaker_id == speaker_id:
                return profile
        return None
