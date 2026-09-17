"""Energy based voice activity detection for browser PCM streams."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


def rms_energy(audio: np.ndarray) -> float:
    """Return a stable RMS value for a float32 audio block."""
    if audio.size == 0:
        return 0.0
    samples = audio.astype(np.float32, copy=False)
    return float(np.sqrt(np.mean(samples * samples) + 1e-12))


@dataclass(slots=True)
class VADUpdate:
    energy: float
    speaking: bool
    speech_started: bool = False
    speech_ended: bool = False
    utterance: np.ndarray | None = None


class EnergyVAD:
    """Split an arbitrary PCM stream into utterances using energy and silence."""

    def __init__(
        self,
        sample_rate: int,
        energy_threshold: float,
        hangover_sec: float,
        min_speech_sec: float,
        max_speech_sec: float,
        pre_roll_sec: float = 0.4,
    ) -> None:
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.hangover_samples = max(1, int(hangover_sec * sample_rate))
        self.min_speech_samples = max(1, int(min_speech_sec * sample_rate))
        self.max_speech_samples = max(1, int(max_speech_sec * sample_rate))
        self.pre_roll_samples = max(0, int(pre_roll_sec * sample_rate))

        self._in_speech = False
        self._blocks: list[np.ndarray] = []
        self._pre_roll_blocks: deque[np.ndarray] = deque()
        self._pre_roll_count = 0
        self._speech_samples = 0
        self._sample_count = 0
        self._silence_samples = 0

    @property
    def speaking(self) -> bool:
        return self._in_speech

    def accept(self, block: np.ndarray) -> VADUpdate:
        """Consume one PCM block and return the resulting state transition."""
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return VADUpdate(energy=0.0, speaking=self._in_speech)

        energy = rms_energy(block)
        speech_started = False
        speech_ended = False
        utterance: np.ndarray | None = None

        if not self._in_speech:
            if energy >= self.energy_threshold:
                self._in_speech = True
                self._blocks = [*self._pre_roll_blocks, block.copy()]
                self._sample_count = sum(item.size for item in self._blocks)
                self._speech_samples = block.size
                self._silence_samples = 0
                speech_started = True
                self._clear_pre_roll()
            else:
                self._append_pre_roll(block)
            return VADUpdate(
                energy=energy,
                speaking=self._in_speech,
                speech_started=speech_started,
            )

        self._blocks.append(block.copy())
        self._sample_count += block.size
        if energy < self.energy_threshold:
            self._silence_samples += block.size
        else:
            self._silence_samples = 0
            self._speech_samples += block.size

        if self._silence_samples >= self.hangover_samples:
            utterance = self._finish_utterance()
            speech_ended = True
        elif self._sample_count >= self.max_speech_samples:
            utterance = self._finish_utterance()
            speech_ended = True

        return VADUpdate(
            energy=energy,
            speaking=self._in_speech,
            speech_started=speech_started,
            speech_ended=speech_ended,
            utterance=utterance,
        )

    def flush(self) -> np.ndarray | None:
        """Finish an active utterance, used when the client stops recording."""
        if not self._in_speech:
            return None
        return self._finish_utterance(force=True)

    def _finish_utterance(self, force: bool = False) -> np.ndarray | None:
        if not self._blocks:
            self._reset()
            return None

        audio = np.concatenate(self._blocks).reshape(-1)
        has_enough_speech = self._speech_samples >= self.min_speech_samples
        self._reset()
        if force or has_enough_speech:
            return audio
        return None

    def _reset(self) -> None:
        self._in_speech = False
        self._blocks = []
        self._clear_pre_roll()
        self._speech_samples = 0
        self._sample_count = 0
        self._silence_samples = 0

    def _append_pre_roll(self, block: np.ndarray) -> None:
        if self.pre_roll_samples <= 0:
            return
        self._pre_roll_blocks.append(block.copy())
        self._pre_roll_count += block.size
        while (
            self._pre_roll_blocks
            and self._pre_roll_count > self.pre_roll_samples
        ):
            removed = self._pre_roll_blocks.popleft()
            self._pre_roll_count -= removed.size

    def _clear_pre_roll(self) -> None:
        self._pre_roll_blocks.clear()
        self._pre_roll_count = 0
