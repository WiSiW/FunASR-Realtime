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
      "max_speech_sec": 30
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
- `ready`：模型已加载，可以发送音频。
- `status`：`listening`、`speech`、`processing`、`stopped` 状态变化。
- `level`：麦克风 PCM 的 RMS 能量，约 10Hz。
- `partial`：流式模式的临时累计文本。
- `final`：一句识别完成，包含 `segment_id`、`text`、可选 `latency_ms`。
- `stopped`：会话已结束，可以再次发送 `start`。
- `error`：参数或服务端错误，包含 `code` 与 `message`。
- `pong`：心跳响应。

所有 JSON 消息均使用：

```json
{"type": "event_name", "request_id": "optional", "data": {}}
```
