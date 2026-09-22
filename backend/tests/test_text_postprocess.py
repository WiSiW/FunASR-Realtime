from __future__ import annotations

import json

from backend.app.services.text_postprocess import TextPostProcessor


def test_text_postprocess_corrects_common_asr_errors() -> None:
    processor = TextPostProcessor()

    assert processor.correct("我因该以经按装好帐号了") == "我应该已经安装好账号了"


def test_text_postprocess_cleans_fillers_but_keeps_meaningful_words() -> None:
    processor = TextPostProcessor()

    cleaned = processor.clean("嗯，我我我觉得这个方案可以，然后明天继续。")

    assert cleaned == "我觉得这个方案可以，然后明天继续。"


def test_text_postprocess_segments_sentences() -> None:
    processor = TextPostProcessor()

    sentences = processor.process("今天我们讨论方案。然后明天继续，我觉得可以。")

    assert sentences == ["今天我们讨论方案。", "然后明天继续，我觉得可以。"]


def test_text_postprocess_splits_long_sentence() -> None:
    processor = TextPostProcessor(max_sentence_chars=12, min_sentence_chars=2)
    text = "这是一个很长的句子" * 4

    sentences = processor.segment(text)

    assert len(sentences) > 1
    assert all(len(sentence) <= 12 for sentence in sentences)


def test_text_postprocess_loads_custom_correction_file(tmp_path) -> None:
    path = tmp_path / "corrections.json"
    path.write_text(json.dumps({"测是": "测试"}), encoding="utf-8")
    processor = TextPostProcessor(correction_file=str(path))

    assert processor.correct("这是测是文本") == "这是测试文本"
