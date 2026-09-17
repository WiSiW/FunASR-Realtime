from __future__ import annotations

import numpy as np

from backend.app.services.audio_vad import EnergyVAD


def block(value: float, samples: int = 1600) -> np.ndarray:
    return np.full(samples, value, dtype=np.float32)


def test_silence_does_not_start_utterance() -> None:
    vad = EnergyVAD(16000, 0.02, 0.2, 0.1, 2.0)

    update = vad.accept(block(0.0))

    assert update.speaking is False
    assert update.speech_started is False
    assert update.utterance is None


def test_energy_and_hangover_create_utterance() -> None:
    vad = EnergyVAD(16000, 0.02, 0.2, 0.1, 2.0)

    started = vad.accept(block(0.1))
    first_silence = vad.accept(block(0.0))
    ended = vad.accept(block(0.0))

    assert started.speech_started is True
    assert first_silence.utterance is None
    assert ended.speech_ended is True
    assert ended.speaking is False
    assert ended.utterance is not None
    assert ended.utterance.size == 4800


def test_short_impulse_is_discarded_after_hangover() -> None:
    vad = EnergyVAD(16000, 0.02, 0.2, 0.5, 2.0)

    vad.accept(block(0.1))
    vad.accept(block(0.0))
    ended = vad.accept(block(0.0))

    assert ended.speech_ended is True
    assert ended.utterance is None


def test_max_speech_forces_utterance() -> None:
    vad = EnergyVAD(16000, 0.02, 0.2, 0.1, 0.2)

    vad.accept(block(0.1))
    ended = vad.accept(block(0.1))

    assert ended.speech_ended is True
    assert ended.utterance is not None
    assert ended.utterance.size == 3200


def test_flush_forces_active_short_utterance() -> None:
    vad = EnergyVAD(16000, 0.02, 1.0, 0.5, 2.0)
    vad.accept(block(0.1))

    utterance = vad.flush()

    assert utterance is not None
    assert utterance.size == 1600
    assert vad.flush() is None


def test_pre_roll_keeps_audio_before_threshold_crossing() -> None:
    vad = EnergyVAD(
        16000,
        0.02,
        0.2,
        0.1,
        2.0,
        pre_roll_sec=0.2,
    )

    vad.accept(block(0.0))
    started = vad.accept(block(0.1))
    vad.accept(block(0.0))
    ended = vad.accept(block(0.0))

    assert started.speech_started is True
    assert ended.utterance is not None
    assert ended.utterance.size == 6400
