"""Conservative ASR text correction, cleanup and sentence segmentation."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CORRECTIONS: dict[str, str] = {
    "因该": "应该",
    "以经": "已经",
    "按装": "安装",
    "帐号": "账号",
    "做为": "作为",
    "既使": "即使",
    "必竟": "毕竟",
}

_FILLER = r"(?:嗯|呃|额|唔|呣)"
_SOFT_FILLER = r"(?:啊|哦|哎)"
_FILLER_PREFIX = re.compile(rf"^(?:{_FILLER})+(?=[\u4e00-\u9fff])")
_FILLER_REPEATED = re.compile(rf"({_FILLER})\1+")
_FILLER_ISOLATED = re.compile(
    rf"(^|[，,、。.!！?？；;\s])(?:{_FILLER}|{_SOFT_FILLER})+"
    rf"(?=[，,、。.!！?？；;\s]|$)"
)
_STUTTER_REPEATED = re.compile(
    r"(我|你|他|她|它|咱|这|那|就|是|在|有|不|没|很|然后|那个|这个)\1+"
)
_STUTTER_PUNCT = re.compile(r"(我|你|他|她|它|咱|这|那)[，,、]\1")
_MULTI_COMMA = re.compile(r"[，,]{2,}")
_MULTI_END = re.compile(r"([。！？!?])\1+")
_MULTI_SPACE = re.compile(r"[ \t\u3000]+")


class TextPostProcessor:
    """Correct ASR text, remove fillers conservatively and split sentences."""

    def __init__(
        self,
        *,
        corrections: Mapping[str, str] | None = None,
        correction_file: str | None = None,
        clean_fillers: bool = True,
        max_sentence_chars: int = 40,
        min_sentence_chars: int = 6,
    ) -> None:
        merged = dict(DEFAULT_CORRECTIONS)
        if corrections:
            merged.update(corrections)
        if correction_file:
            merged.update(_load_correction_file(correction_file))
        self.corrections = sorted(
            ((str(key), str(value)) for key, value in merged.items()),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        self.clean_fillers = clean_fillers
        self.max_sentence_chars = max(10, int(max_sentence_chars))
        self.min_sentence_chars = max(1, int(min_sentence_chars))

    def correct(self, text: str) -> str:
        result = str(text or "")
        for wrong, right in self.corrections:
            result = result.replace(wrong, right)
        result = _MULTI_SPACE.sub(" ", result)
        return result.strip()

    def clean(self, text: str) -> str:
        result = self.correct(text)
        if not self.clean_fillers:
            return result

        result = _FILLER_REPEATED.sub(r"\1", result)
        result = _FILLER_PREFIX.sub("", result)
        result = _FILLER_ISOLATED.sub(r"\1", result)
        result = _STUTTER_REPEATED.sub(r"\1", result)
        result = _STUTTER_PUNCT.sub(r"\1", result)
        result = _MULTI_COMMA.sub("，", result)
        result = _MULTI_END.sub(r"\1", result)
        result = _MULTI_SPACE.sub(" ", result)
        return result.strip("，,、 ")

    def polish(self, text: str) -> str:
        """Correction/cleanup for partial display without sentence splitting."""
        return self.clean(text)

    def segment(self, text: str) -> list[str]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return []

        parts = [
            part.strip()
            for part in re.split(r"(?<=[。！？!?；;])", cleaned)
            if part.strip()
        ]
        if not parts:
            return []

        merged: list[str] = []
        for part in parts:
            if merged and len(merged[-1]) < self.min_sentence_chars:
                merged[-1] = f"{merged[-1]}{part}"
            else:
                merged.append(part)

        sentences: list[str] = []
        for part in merged:
            sentences.extend(self._split_long_sentence(part))
        return sentences

    def process(self, text: str) -> list[str]:
        cleaned = self.clean(text)
        return self.segment(cleaned)

    def _split_long_sentence(self, text: str) -> list[str]:
        if len(text) <= self.max_sentence_chars:
            return [text]

        clauses = [
            clause
            for clause in re.split(r"(?<=[，,、])", text)
            if clause
        ]
        result: list[str] = []
        buffer = ""
        for clause in clauses:
            if buffer and len(buffer) + len(clause) > self.max_sentence_chars:
                result.append(buffer)
                buffer = ""
            while len(clause) > self.max_sentence_chars:
                result.append(clause[: self.max_sentence_chars])
                clause = clause[self.max_sentence_chars :]
            buffer += clause
        if buffer:
            result.append(buffer)
        return result


def _load_correction_file(path: str) -> dict[str, str]:
    correction_path = Path(path).expanduser()
    if not correction_path.is_file():
        logger.warning("ASR correction file does not exist: %s", correction_path)
        return {}
    try:
        payload = json.loads(correction_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load ASR correction file: %s", correction_path)
        return {}
    if not isinstance(payload, dict):
        logger.warning("ASR correction file must be a JSON object: %s", correction_path)
        return {}
    return {
        str(key): str(value)
        for key, value in payload.items()
        if str(key) and str(value)
    }
