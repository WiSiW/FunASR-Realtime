"""麦克风实时采集 + 基于能量的 VAD 自动分段。

工作流程：
  1. 通过 sounddevice.InputStream 持续采集 16kHz 单声道音频块。
  2. 计算每个块的 RMS 能量，超过阈值视为「说话中」。
  3. 说话开始后累积音频；连续静音超过 hangover 秒则判定一句话结束，
     把累积的音频作为一段语音 yield 出去。
"""

from __future__ import annotations

import logging
import queue
from collections.abc import Generator
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

# 960 个采样点 = 60ms（16kHz），是一个方便的处理粒度
_DEFAULT_BLOCK = 960
# 队列拉取轮询间隔（秒）。带超时才能让 Windows 上的 Ctrl+C 及时打断。
_POLL = 0.1


@dataclass
class RecorderConfig:
    samplerate: int = 16000
    channels: int = 1
    blocksize: int = _DEFAULT_BLOCK
    dtype: str = "float32"
    # 音频输入设备号（None = 系统默认）。用 `python main.py --list-devices` 查看。
    device: int | None = None

    # --- 能量 VAD 参数 ---
    energy_threshold: float = 0.012  # RMS 阈值，环境噪声越低可调越小
    hangover_sec: float = 0.6  # 说话中连续静音多久判为句子结束
    min_speech_sec: float = 0.25  # 过短的脉冲（如咳嗽）忽略
    max_speech_sec: float = 30.0  # 单段最长时长，到上限强制保存，防止一次录太久

    # --- push 模式固定录制时长（秒），到点自动结束；也可 Ctrl+C 提前结束 ---
    push_duration: float = 6.0

    # --- 诊断 ---
    show_energy: bool = False  # 打印实时音量条，排查麦克风是否采集到声音
    auto_calibrate: bool = True  # 启动时录 ~1s 环境噪声自动定阈值


class MicRecorder:
    """麦克风采集器：以生成器方式产出每段语音。"""

    def __init__(self, cfg: RecorderConfig | None = None):
        self.cfg = cfg or RecorderConfig()
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None

    # --- sounddevice 回调：把每个音频块丢进队列 ---
    def _callback(self, indata: np.ndarray, frames: int, time, status):
        if status:
            logger.debug("sounddevice 状态: %s", status)
        self._q.put(indata.copy())

    def _poll(self) -> np.ndarray | None:
        """带超时地从队列取一个块，取不到返回 None（让 Ctrl+C 有机会介入）。"""
        try:
            return self._q.get(timeout=_POLL)
        except queue.Empty:
            return None

    def _check_input_device(self) -> None:
        """确认存在可用的输入设备，否则给出明确提示。"""
        try:
            dev = sd.query_devices(device=self.cfg.device, kind="input")
            logger.info("使用输入设备: %s", dev.get("name"))
        except Exception as e:  # 找不到输入设备
            raise RuntimeError(
                "未找到可用的音频输入设备（麦克风）。"
                "请检查麦克风是否连接/被禁用，或用 `python main.py --list-devices` 查看。"
            ) from e

    def list_devices(self) -> None:
        """打印可用音频输入设备，便于排查没有麦克风的问题。"""
        print(sd.query_devices())
        print("默认输入设备:", sd.default.device[0])

    def _rms(self, block: np.ndarray) -> float:
        return float(np.sqrt(np.mean(block**2) + 1e-12))

    def calibrate(self, seconds: float = 1.0) -> float:
        """录 seconds 秒环境噪声，自动设定能量阈值（=噪声 RMS × 3，下限 0.005）。

        返回设定后的阈值。若麦克风完全没声音（RMS≈0），阈值会取下限 0.005，
        此时说话也几乎不会触发 —— 用 --show-energy 可立刻看到音量条确认。
        """
        cfg = self.cfg
        sr = cfg.samplerate
        target = int(seconds * sr)
        self._check_input_device()
        print(f"正在测量环境噪声 {seconds:.1f}s，请保持安静、不要说话…")
        self._q.queue.clear()
        collected = 0
        chunks = []
        with sd.InputStream(
            samplerate=sr,
            blocksize=cfg.blocksize,
            channels=cfg.channels,
            dtype=cfg.dtype,
            device=cfg.device,
            callback=self._callback,
        ):
            while collected < target:
                b = self._poll()
                if b is None:
                    continue
                chunks.append(b)
                collected += len(b)
        if not chunks:
            print("⚠ 未能采集到任何音频用于标定。请检查麦克风。")
            return cfg.energy_threshold
        noise = self._rms(np.concatenate(chunks).reshape(-1))
        thr = max(noise * 3.0, 0.005)
        cfg.energy_threshold = thr
        print(f"环境噪声 RMS ≈ {noise:.5f}，能量阈值自动设为 {thr:.5f}")
        if noise < 1e-4:
            print(
                "⚠ 噪声几乎为 0，麦克风可能没采集到声音。"
                "建议加 --show-energy 实时查看音量条，或 --list-devices 检查设备。"
            )
        return thr

    @staticmethod
    def _meter(rms: float, threshold: float) -> str:
        """生成一个 20 格的音量条，标记阈值位置。"""
        peak = max(rms, threshold) * 1.2 + 1e-6
        width = 20
        filled = int(min(rms / peak, 1.0) * width)
        thr_pos = int(min(threshold / peak, 1.0) * width)
        bar = [" "] * width
        for i in range(filled):
            bar[i] = "█"
        if 0 <= thr_pos < width:
            bar[thr_pos] = "|"
        return "".join(bar)

    def blocks(self) -> Generator[tuple[np.ndarray, float], None, None]:
        """生成器：持续 yield (block, rms) 原始音频块与能量，供流式识别消费。

        按 Ctrl+C 可停止。不做 VAD 切分，调用方自行判定。
        若 cfg.show_energy=True，在 stderr 打印实时音量条。
        """
        cfg = self.cfg
        sr = cfg.samplerate
        self._check_input_device()
        print(
            f"麦克风采集已启动 (sr={sr}, 块={cfg.blocksize}, 能量阈值={cfg.energy_threshold:.5f})。"
        )
        print("请开始说话，停顿后自动识别。按 Ctrl+C 退出。\n")
        with sd.InputStream(
            samplerate=sr,
            blocksize=cfg.blocksize,
            channels=cfg.channels,
            dtype=cfg.dtype,
            device=cfg.device,
            callback=self._callback,
        ):
            while True:
                block = self._poll()
                if block is None:
                    continue  # 超时轮询点，让 KeyboardInterrupt 有机会触发
                rms = self._rms(block)
                if cfg.show_energy:
                    import sys

                    tag = "说话" if rms > cfg.energy_threshold else "静音"
                    sys.stderr.write(
                        f"\r[{self._meter(rms, cfg.energy_threshold)}] {rms:.4f} {tag}   "
                    )
                    sys.stderr.flush()
                yield block, rms

    def segments(self) -> Generator[np.ndarray, None, None]:
        """生成器：每次 yield 一段完整语音（1D float32 ndarray）。

        按 Ctrl+C 可停止。
        """
        cfg = self.cfg
        sr = cfg.samplerate
        hangover_samples = int(cfg.hangover_sec * sr)
        min_speech_samples = int(cfg.min_speech_sec * sr)
        max_speech_samples = int(cfg.max_speech_sec * sr)

        in_speech = False
        buffer: list[np.ndarray] = []
        silence_count = 0

        print(
            f"VAD: 能量阈值={cfg.energy_threshold}, 静音判定={cfg.hangover_sec}s, "
            f"最短={cfg.min_speech_sec}s, 最长={cfg.max_speech_sec}s。"
        )

        try:
            for block, energy in self.blocks():
                if not in_speech:
                    if energy > cfg.energy_threshold:
                        in_speech = True
                        buffer = [block]
                        silence_count = 0
                else:
                    buffer.append(block)
                    if energy < cfg.energy_threshold:
                        silence_count += cfg.blocksize
                        if silence_count >= hangover_samples:
                            # 句子结束
                            audio = np.concatenate(buffer).reshape(-1)
                            if len(audio) >= min_speech_samples:
                                yield audio
                            in_speech = False
                            buffer = []
                            silence_count = 0
                    else:
                        silence_count = 0

                # 超长保护：到上限强制截断送出
                if in_speech and sum(len(b) for b in buffer) >= max_speech_samples:
                    audio = np.concatenate(buffer).reshape(-1)
                    yield audio
                    in_speech = False
                    buffer = []
                    silence_count = 0
        except KeyboardInterrupt:
            # 若退出时仍有未送出的语音，送最后一段
            if in_speech and buffer:
                audio = np.concatenate(buffer).reshape(-1)
                if len(audio) >= min_speech_samples:
                    yield audio
            return

    def record_once(self) -> np.ndarray:
        """按键说话模式：Enter 开始录音，录制 push_duration 秒后自动结束
        （也可 Ctrl+C 提前结束），返回这段音频。
        """
        cfg = self.cfg
        sr = cfg.samplerate
        duration = cfg.push_duration
        target_samples = int(duration * sr)
        buffer: list[np.ndarray] = []

        self._check_input_device()
        input(f"按 Enter 开始录音（录制 {duration:.0f} 秒，或 Ctrl+C 提前结束）：")
        print("录音中… ●", end="", flush=True)
        self._q.queue.clear()

        try:
            with sd.InputStream(
                samplerate=sr,
                blocksize=cfg.blocksize,
                channels=cfg.channels,
                dtype=cfg.dtype,
                device=cfg.device,
                callback=self._callback,
            ):
                collected = 0
                while collected < target_samples:
                    block = self._poll()
                    if block is None:
                        continue  # 超时轮询点，让 Ctrl+C 有机会触发
                    buffer.append(block)
                    collected += len(block)
                    print("\r录音中… 已采集 %.1fs" % (collected / sr), end="", flush=True)
        except KeyboardInterrupt:
            print("\n（提前结束录音）", end="")

        print()
        if not buffer:
            print(
                "（未采集到音频：可能是麦克风被占用或静音，"
                "可用 `python main.py --list-devices` 排查）"
            )
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(buffer).reshape(-1)
        print(f"采集完成，共 {len(audio) / sr:.2f}s。")
        return audio
