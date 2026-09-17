"""SQLite-backed enrolled speaker voiceprint storage."""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backend.app.services.speaker import EnrolledSpeaker, normalize_embedding

_SPEAKER_ID_RE = re.compile(r"^speaker_(\d+)$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS speaker_profiles (
    speaker_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    embedding BLOB NOT NULL,
    dimension INTEGER NOT NULL,
    sample_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class SpeakerProfileStore:
    """Persist and update enrolled speaker centroids in a local SQLite DB."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connection() as connection:
            connection.execute(_SCHEMA)

    def list_profiles(self) -> list[EnrolledSpeaker]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT speaker_id, name, embedding, dimension, sample_count,
                       created_at, updated_at
                FROM speaker_profiles
                ORDER BY speaker_id
                """
            ).fetchall()
        return [self._row_to_profile(row) for row in rows]

    def upsert(self, name: str, embedding: np.ndarray) -> EnrolledSpeaker:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("说话人姓名不能为空")
        vector = normalize_embedding(embedding)
        if vector.size == 0:
            raise ValueError("说话人 embedding 不能为空")

        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT speaker_id, name, embedding, dimension, sample_count,
                       created_at, updated_at
                FROM speaker_profiles
                WHERE name = ?
                """,
                (normalized_name,),
            ).fetchone()
            if row is not None:
                existing = self._row_to_profile(row)
                new_count = existing.sample_count + 1
                centroid = normalize_embedding(
                    (
                        existing.centroid * existing.sample_count
                        + vector
                    )
                    / new_count
                )
                connection.execute(
                    """
                    UPDATE speaker_profiles
                    SET embedding = ?, dimension = ?, sample_count = ?,
                        updated_at = ?
                    WHERE speaker_id = ?
                    """,
                    (
                        centroid.astype(np.float32).tobytes(),
                        int(centroid.size),
                        new_count,
                        now,
                        existing.speaker_id,
                    ),
                )
                connection.commit()
                return EnrolledSpeaker(
                    speaker_id=existing.speaker_id,
                    name=existing.name,
                    centroid=centroid,
                    sample_count=new_count,
                    created_at=existing.created_at,
                    updated_at=now,
                )

            speaker_id = self._next_speaker_id(connection)
            connection.execute(
                """
                INSERT INTO speaker_profiles (
                    speaker_id, name, embedding, dimension, sample_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    speaker_id,
                    normalized_name,
                    vector.astype(np.float32).tobytes(),
                    int(vector.size),
                    1,
                    now,
                    now,
                ),
            )
            connection.commit()
            return EnrolledSpeaker(
                speaker_id=speaker_id,
                name=normalized_name,
                centroid=vector,
                sample_count=1,
                created_at=now,
                updated_at=now,
            )

    def delete(self, speaker_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM speaker_profiles WHERE speaker_id = ?",
                (speaker_id,),
            )
            connection.commit()
            return cursor.rowcount > 0

    def get(self, speaker_id: str) -> EnrolledSpeaker | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT speaker_id, name, embedding, dimension, sample_count,
                       created_at, updated_at
                FROM speaker_profiles
                WHERE speaker_id = ?
                """,
                (speaker_id,),
            ).fetchone()
        return self._row_to_profile(row) if row is not None else None

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=10.0)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _next_speaker_id(connection: sqlite3.Connection) -> str:
        rows = connection.execute("SELECT speaker_id FROM speaker_profiles").fetchall()
        max_index = 0
        for (speaker_id,) in rows:
            match = _SPEAKER_ID_RE.match(speaker_id)
            if match:
                max_index = max(max_index, int(match.group(1)))
        return f"speaker_{max_index + 1:02d}"

    @staticmethod
    def _row_to_profile(row: tuple) -> EnrolledSpeaker:
        (
            speaker_id,
            name,
            embedding_bytes,
            _dimension,
            sample_count,
            created_at,
            updated_at,
        ) = row
        centroid = np.frombuffer(embedding_bytes, dtype=np.float32).copy()
        return EnrolledSpeaker(
            speaker_id=str(speaker_id),
            name=str(name),
            centroid=normalize_embedding(centroid),
            sample_count=int(sample_count),
            created_at=str(created_at),
            updated_at=str(updated_at),
        )
