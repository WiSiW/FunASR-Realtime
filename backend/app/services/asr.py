"""FunASR 语音识别封装。

使用 Paraformer-zh 离线模型，并串联：
  - fsmn-vad    : 语音端点检测（切分静音）
  - ct-punc     : 中文标点恢复
模型首次运行时会从 ModelScope 自动下载到本地缓存，之后离线可用。
"""

from __future__ import annotations

import logging
import threading

import numpy as np

logger = logging.getLogger(__name__)

# 离线中文识别 + VAD + 标点（model_revision 固定以保证可复现）
_MODEL_KWARGS = dict(
    model="paraformer-zh",
    model_revision="v2.0.4",
    vad_model="fsmn-vad",
    vad_model_revision="v2.0.4",
    punc_model="ct-punc",
    punc_model_revision="v2.0.4",
)


class ASR:
    """加载并运行 FunASR 模型。"""

    def __init__(self, model_revision: str = "v2.0.4"):
        from funasr import AutoModel  # 延迟导入，避免加载耗时阻塞模块导入

        logger.info("正在加载 FunASR 模型（首次会自动下载，请耐心等待）...")
        model_kwargs = {**_MODEL_KWARGS, "model_revision": model_revision}
        self.model = AutoModel(**model_kwargs)
        self._inference_lock = threading.Lock()
        logger.info("模型加载完成。")

    def transcribe(self, audio: np.ndarray, sr: int = 16000) -> str:
        """识别一段 16kHz 单声道 float32 音频，返回文本。

        参数
        ----
        audio : np.ndarray, shape=(T,), dtype=float32
            音频采样点，范围 [-1, 1]。
        sr : int
            采样率，默认 16000。
        """
        if audio.ndim != 1:
            audio = audio.reshape(-1)
        audio = audio.astype(np.float32)

        # FunASR 的 generate 直接接受 ndarray，也接受文件路径。
        # 这里直接传 ndarray，省去磁盘读写。
        with self._inference_lock:
            res = self.model.generate(
                input=audio,
                fs=sr,
                batch_size_s=300,
                disable_pbar=False,
            )
        # res 为 list[dict]，每个 dict 含 "text" 字段
        texts = [r.get("text", "") for r in res if r.get("text")]
        return "".join(texts).strip()
