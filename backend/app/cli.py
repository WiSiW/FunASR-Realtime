"""FunASR 麦克风实时语音识别 —— 入口。

三种模式：
  auto    （默认）能量 VAD 自动分段，说话停顿即识别输出
  push    按键说话：Enter 开始录音，固定时长后自动结束（可 Ctrl+C 提前）
  stream  流式模型，边说边出字（paraformer-zh-streaming）

可选落盘：
  --save-wav DIR    每段语音保存为 wav
  --save-text FILE  每条结果追加写入文本文件

示例：
  python main.py
  python main.py --mode stream --save-wav ./wavs --save-text ./out.txt
  python main.py --mode push --push-duration 8
  python main.py --energy 0.02 --hangover 0.8
"""

from __future__ import annotations

import argparse
import logging

import numpy as np

from backend.app.services.asr import ASR
from backend.app.services.asr_streaming import StreamingASR
from backend.app.services.mic_recorder import MicRecorder, RecorderConfig
from backend.app.services.output import Output

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FunASR 麦克风实时语音识别")
    p.add_argument(
        "--mode",
        choices=["auto", "push", "stream"],
        default="auto",
        help="auto=VAD自动分段(默认); push=按键说话; stream=流式逐句",
    )
    p.add_argument(
        "--energy", type=float, default=None, help="VAD 能量阈值 (默认 0.012，环境越安静可调小)"
    )
    p.add_argument(
        "--hangover", type=float, default=None, help="判定句子结束的连续静音秒数 (默认 0.6)"
    )
    p.add_argument(
        "--max-speech",
        type=float,
        default=None,
        help="单段最长录制秒数，到上限强制保存 (默认 30.0)",
    )
    p.add_argument(
        "--push-duration", type=float, default=None, help="push 模式固定录制秒数 (默认 6.0)"
    )
    p.add_argument(
        "--show-energy", action="store_true", help="打印实时音量条，排查麦克风是否采集到声音"
    )
    p.add_argument(
        "--device",
        type=int,
        default=None,
        help="音频输入设备号；默认用系统默认设备(可能为虚拟设备)。"
        "用 --list-devices 查看，例如蓝牙/USB 麦克风常需指定",
    )
    p.add_argument(
        "--save-wav", type=str, default=None, metavar="DIR", help="把每段语音保存为 wav 到该目录"
    )
    p.add_argument(
        "--save-text",
        type=str,
        default=None,
        metavar="FILE",
        help="把每条识别结果追加写入该文本文件",
    )
    p.add_argument("--list-devices", action="store_true", help="列出可用音频输入设备后退出")
    return p.parse_args()


def build_cfg(args: argparse.Namespace) -> RecorderConfig:
    cfg = RecorderConfig()
    cfg.show_energy = args.show_energy
    # 用户显式给 --energy 时关掉自动标定，以手动值为准
    cfg.auto_calibrate = args.energy is None
    if args.energy is not None:
        cfg.energy_threshold = args.energy
    if args.hangover is not None:
        cfg.hangover_sec = args.hangover
    if args.max_speech is not None:
        cfg.max_speech_sec = args.max_speech
    if args.push_duration is not None:
        cfg.push_duration = args.push_duration
    cfg.device = args.device
    return cfg


def run_auto(asr: ASR, recorder: MicRecorder, out: Output) -> None:
    for idx, audio in enumerate(recorder.segments(), 1):
        print("\n[识别中…]")
        text = asr.transcribe(audio)
        print(f"[{idx}] {text}\n")
        out.save(audio, text)


def run_push(asr: ASR, recorder: MicRecorder, out: Output) -> None:
    idx = 1
    while True:
        audio = recorder.record_once()
        if audio.size == 0:
            continue
        text = asr.transcribe(audio)
        print(f"[{idx}] {text}\n")
        out.save(audio, text)
        idx += 1


def run_stream(stream_asr: StreamingASR, recorder: MicRecorder, out: Output) -> None:
    """流式：能量 VAD 切句，句中按 600ms 块增量解码，边说边出字。"""
    cfg = recorder.cfg
    sr = cfg.samplerate
    chunk_stride = stream_asr.chunk_stride
    hangover_samples = int(cfg.hangover_sec * sr)
    min_speech_samples = int(cfg.min_speech_sec * sr)
    max_speech_samples = int(cfg.max_speech_sec * sr)

    in_speech = False
    asr_buf = np.zeros(0, dtype=np.float32)  # 待增量解码的缓冲
    utter = np.zeros(0, dtype=np.float32)  # 整句音频（用于存 wav）
    silence_count = 0
    last_text = ""  # feed() 累计的最新文本，落盘用

    def flush_utter() -> None:
        """句末：finalize 取最终文本，回退 last_text，落盘并重置。"""
        nonlocal in_speech, asr_buf, utter, silence_count, last_text
        final_texts = stream_asr.finalize()
        # 优先用 finalize 结果，为空则回退到增量解码累计的文本，避免落盘空内容
        text = (final_texts[-1] if final_texts and final_texts[-1] else "") or last_text
        print(f"\r>>> {text}" + " " * 10, end="", flush=True)
        print()
        if len(utter) >= min_speech_samples:
            out.save(utter, text or "(空)")
        in_speech = False
        asr_buf = np.zeros(0, dtype=np.float32)
        utter = np.zeros(0, dtype=np.float32)
        silence_count = 0
        last_text = ""

    try:
        for block, energy in recorder.blocks():
            if not in_speech:
                if energy > cfg.energy_threshold:
                    in_speech = True
                    asr_buf = block.copy()
                    utter = block.copy()
                    silence_count = 0
                    last_text = ""
                    print("\n>>> ", end="", flush=True)
                continue

            # 说话中
            asr_buf = np.concatenate([asr_buf, block])
            utter = np.concatenate([utter, block])

            if energy < cfg.energy_threshold:
                silence_count += cfg.blocksize
                if silence_count >= hangover_samples:
                    # 句子结束
                    flush_utter()
                    continue
            else:
                silence_count = 0

            # 超长保护：到上限强制截断落盘，避免长语音一直不存
            if len(utter) >= max_speech_samples:
                print("\n[达到单段上限，强制保存]", end="", flush=True)
                flush_utter()
                continue

            # 增量解码：缓冲到一块(600ms)就送一次
            while len(asr_buf) >= chunk_stride:
                chunk = asr_buf[:chunk_stride]
                asr_buf = asr_buf[chunk_stride:]
                partials = stream_asr.feed(chunk)
                if partials:
                    last_text = partials[-1]  # 累计文本，越说越长
                    print(f"\r>>> {last_text}", end="", flush=True)
    except KeyboardInterrupt:
        # 退出时若有未保存的语音，一并落盘
        if in_speech and utter.size >= min_speech_samples:
            print("\n[退出前保存最后一段]", end="", flush=True)
            flush_utter()
        print("\n已退出。")


def main() -> None:
    args = parse_args()

    recorder = MicRecorder(build_cfg(args))
    if args.list_devices:
        recorder.list_devices()
        return

    # push 模式不需要 VAD 阈值；auto/stream 模式先标定
    if args.mode != "push" and recorder.cfg.auto_calibrate:
        recorder.calibrate(1.0)

    out = Output(
        wav_dir=args.save_wav,
        text_file=args.save_text,
        sr=recorder.cfg.samplerate,
    )

    if args.mode == "stream":
        asr = StreamingASR()
    else:
        asr = ASR()

    try:
        if args.mode == "auto":
            run_auto(asr, recorder, out)
        elif args.mode == "push":
            run_push(asr, recorder, out)
        else:
            run_stream(asr, recorder, out)
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
