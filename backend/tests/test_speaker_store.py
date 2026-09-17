from __future__ import annotations

import numpy as np

from backend.app.services.speaker_store import SpeakerProfileStore


def test_speaker_profile_store_upsert_list_and_delete(tmp_path) -> None:
    path = tmp_path / "speakers.sqlite3"
    store = SpeakerProfileStore(path)

    first = store.upsert("张三", np.asarray([1.0, 0.0, 0.0], dtype=np.float32))
    assert first.speaker_id == "speaker_01"
    assert first.name == "张三"
    assert first.sample_count == 1

    updated = store.upsert("张三", np.asarray([0.8, 0.2, 0.0], dtype=np.float32))
    assert updated.speaker_id == "speaker_01"
    assert updated.sample_count == 2

    second = store.upsert("李四", np.asarray([0.0, 1.0, 0.0], dtype=np.float32))
    assert second.speaker_id == "speaker_02"

    profiles = store.list_profiles()
    assert [profile.speaker_id for profile in profiles] == ["speaker_01", "speaker_02"]

    reloaded_store = SpeakerProfileStore(path)
    assert len(reloaded_store.list_profiles()) == 2
    assert reloaded_store.get("speaker_01") is not None
    assert reloaded_store.delete("speaker_01") is True
    assert reloaded_store.delete("speaker_01") is False


def test_speaker_profile_store_rejects_empty_embedding(tmp_path) -> None:
    store = SpeakerProfileStore(tmp_path / "speakers.sqlite3")

    try:
        store.upsert("张三", np.zeros(0, dtype=np.float32))
    except ValueError as exc:
        assert "embedding" in str(exc)
    else:
        raise AssertionError("empty embedding should be rejected")
