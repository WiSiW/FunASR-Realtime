# WebSocket 识别协议

端点：`ws(s)://<host>/api/v1/asr/stream`

一个连接可依次启动多个识别会话。模型加载完成后服务端发送 `ready`，客户端收到后再发送二进制音频。

## 客户端消息

启动会话：

```json
{
  "type": "start",
  "request_id": "uuid",
  "data": {
    "mode": "auto",
    "sample_rate": 16000,
    "channels": 1,
    "audio_format": "pcm_s16le",
    "vad": {
      "energy_threshold": 0.012,
      "hangover_sec": 0.6,
      "min_speech_sec": 0.25,
      "max_speech_sec": 30,
      "pre_roll_sec": 0.4
    },
    "speaker": {
      "enabled": true,
      "similarity_threshold": 0.70,
      "new_speaker_threshold": 0.45,
      "switch_margin": 0.08,
      "min_segment_sec": 0.8,
      "max_speakers": 8,
      "embedding_window_sec": 1.5,
      "embedding_interval_sec": 0.8,
      "centroid_update_alpha": 0.1
    }
  }
}
```

音频数据直接发送二进制帧：16kHz、单声道、little-endian signed 16-bit PCM。

停止会话：

```json
{"type": "stop", "request_id": "uuid"}
```

心跳：

```json
{"type": "ping", "request_id": "uuid"}
```

客户端每 15 秒发送一次心跳，10 秒内未收到 `pong` 会主动切换连接。服务端使用独立接收任务处理心跳，因此模型加载和识别不会阻塞 `pong`。

## 服务端消息

- `connected`：WebSocket 已建立，并返回建议的心跳间隔。
- `ready`：模型已加载，可以发送音频；包含 `session_audio_id`，可用于播放整段会话原音。
- `status`：`listening`、`speech`、`processing`、`stopped` 状态变化。
- `level`：麦克风 PCM 的 RMS 能量，约 10Hz。
- `partial`：流式模式的临时累计文本，已做纠错和保守口语清洗；启用说话人识别时包含 `speaker_id`、
  `speaker_name`、`speaker_enrolled`、`speaker_pending`。
- `final`：一句识别完成，包含 `segment_id`、`text`、可选 `latency_ms`，以及
  `speaker_id`、`speaker_name`、`speaker_enrolled`、`speaker_confidence`、
  `speaker_pending`、`speaker_is_new`、`audio_id`。一段 VAD 语音经过断句后可能产生多个
  `final` 事件，使用 `sentence_index` 和 `sentence_count` 表示句子序号。
- `speaker`：流式模式检测到说话人新增或切换时发送，包含 `speaker_id` 和 `status`。
- `stopped`：会话已结束，可以再次发送 `start`。
- `error`：参数或服务端错误，包含 `code` 与 `message`。
- `pong`：心跳响应。

所有 JSON 消息均使用：

```json
{"type": "event_name", "request_id": "optional", "data": {}}
```

## 声纹库 REST API

- `GET /api/v1/speakers`
- `POST /api/v1/speakers/enroll?name=张三&sample_rate=16000`
  - Body：16kHz 单声道 `pcm_s16le` 原始音频
- `DELETE /api/v1/speakers/{speaker_id}`

## 原音播放 API

- `GET /api/v1/asr/audio/{audio_id}`
  - 返回 16kHz 单声道 WAV
  - `audio_id` 可来自 `ready.session_audio_id` 或 `final.audio_id`
