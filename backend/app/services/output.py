"""录音与识别结果的落盘工具。

- 每段语音的音频保存为 wav（16bit PCM）
- 每条识别结果追加写入文本文件（带时间戳与序号）
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Output:
    """统一管理 wav 与文本输出。两者均可选。"""

    def __init__(
        self,
        wav_dir: str | None = None,
        text_file: str | None = None,
        sr: int = 16000,
    ):
        self.sr = sr
        self.wav_dir = Path(wav_dir) if wav_dir else None
        self.text_file = Path(text_file) if text_file else None
        if self.wav_dir:
            self.wav_dir.mkdir(parents=True, exist_ok=True)
        if self.text_file:
            self.text_file.parent.mkdir(parents=True, exist_ok=True)
            # 立即创建文件并写表头，便于确认已生效
            if not self.text_file.exists():
                self.text_file.write_text(
                    f"=== FunASR 识别结果  开始 {_now()} ===\n",
                    encoding="utf-8",
                )
            logger.info("识别结果将写入: %s", self.text_file.resolve())
        self.idx = 0

    def save(self, audio: np.ndarray, text: str) -> None:
        """保存一段音频与其识别结果。序号自增。"""
        self.idx += 1
        ts = _now()

        if self.wav_dir and audio.size:
            wav_path = self.wav_dir / f"utter_{self.idx:04d}.wav"
            # float32 [-1,1] -> int16 PCM
            sf.write(str(wav_path), audio, self.sr, subtype="PCM_16")
            logger.info("已保存音频: %s", wav_path)

        if self.text_file:
            with self.text_file.open("a", encoding="utf-8") as f:
                f.write(f"[{ts}] #{self.idx:04d} {text}\n")
            logger.info("已写入结果到: %s", self.text_file)
