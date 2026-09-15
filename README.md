# FunASR Realtime · 浏览器实时语音识别

基于 FunASR 的全栈语音识别项目。浏览器负责麦克风采集和 16kHz PCM 转换，通过 WebSocket 与 FastAPI 后端保持长连接；后端按所选模式执行自动分段、实时流式或按键说话识别。

## 功能

- Vue 3 + TypeScript + Vite 前端
- AudioWorklet 浏览器麦克风采集，实时重采样为 16kHz 单声道 PCM
- FastAPI WebSocket 长连接，支持一个连接多次启动/停止识别
- 15 秒应用层心跳、10 秒超时检测和指数退避自动重连
- 服务端独立心跳接收任务，模型推理不会阻塞 `ping/pong`
- 三种识别模式：
  - `auto`：能量 VAD 自动断句 + Paraformer-zh 离线识别 + 中文标点
  - `stream`：`paraformer-zh-streaming` 边说边出临时结果
  - `push`：持续采集到手动停止，再统一识别并恢复标点
- 实时音量、连接状态、分段编号、识别延迟和识别文本展示
- 文本复制、清空、TXT 导出
- 保留原命令行麦克风识别入口
- 后端启动阶段预加载模型，不把首次加载延迟到识别请求
- 多客户端流式状态隔离、健康检查
- 前后端独立 Dockerfile 和 Docker Compose 一键部署

## 项目结构

```text
FunASR-test/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── protocol.py        # WebSocket 消息与 PCM 编解码
│   │   │   └── routes/
│   │   │       ├── asr.py         # /api/v1/asr/stream
│   │   │       └── system.py      # 健康检查、模式列表
│   │   ├── core/                  # 配置与日志
│   │   ├── services/
│   │   │   ├── asr.py             # 离线模型
│   │   │   ├── asr_streaming.py   # 流式模型和独立解码会话
│   │   │   ├── asr_session.py     # WebSocket 会话状态机
│   │   │   ├── audio_vad.py       # 浏览器 PCM 能量 VAD
│   │   │   └── model_registry.py  # 模型懒加载
│   │   ├── cli.py                 # 原 CLI
│   │   └── main.py                # FastAPI 入口
│   ├── scripts/preload_models.py # 构建镜像时预热模型缓存
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── public/pcm-capture-worklet.js
│   ├── src/
│   │   ├── components/
│   │   ├── composables/
│   │   ├── services/
│   │   └── types/
│   ├── package.json
│   ├── Dockerfile
│   ├── nginx.conf
│   └── vite.config.ts
├── docker-compose.yml
├── docs/protocol.md
├── main.py                        # CLI 兼容入口
├── pyproject.toml
├── Makefile
└── .env.example
```

## 环境要求

- Python 3.9–3.12
- Node.js 20.19+
- 浏览器：最新版 Chrome、Edge 或 Firefox
- 麦克风输入设备
- 首次识别会从 ModelScope 下载模型，离线模型约数百 MB

麦克风 API 只允许安全上下文使用。`localhost` 可直接开发；部署到其他域名时必须配置 HTTPS/WSS。

## 安装

```bash
# 创建 Python 虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装后端、CLI 和开发依赖
pip install -e ".[dev,cli]"

# 安装前端依赖
npm --prefix frontend install
```

也可以通过 Makefile：

```bash
make install
```

PyTorch 如需指定 CUDA/CPU 版本，请先按 [PyTorch 官方说明](https://pytorch.org/get-started/locally/) 安装，再执行项目安装命令。

## 开发运行

打开两个终端：

```bash
# 终端 1：后端，http://127.0.0.1:8000
make dev-backend

# 终端 2：前端，http://127.0.0.1:5173
make dev-frontend
```

浏览器打开 <http://localhost:5173>，选择识别模式，点击“开始识别”，然后允许麦克风权限。

Vite 会把 `/api`（包含 WebSocket）代理到 `http://127.0.0.1:8000`。需要修改后端地址时，复制 `.env.example` 为 `frontend/.env.local` 并配置 `VITE_API_TARGET`。

## 识别模式

| 模式 | 断句方式 | 模型 | 适用场景 |
|---|---|---|---|
| 自动分段 | 服务端能量 VAD 在静音后结束一句 | Paraformer-zh + FSMN-VAD + CT-Punc | 连续发言、会议记录 |
| 实时流式 | VAD 分句，句内 600ms 增量解码 | paraformer-zh-streaming | 实时字幕、低延迟交互 |
| 按键说话 | 点击停止后识别全部音频 | Paraformer-zh + CT-Punc | 短句、命令、录音转写 |

前端展开“VAD 灵敏度”可调整 `auto` 和 `stream` 模式的能量阈值。环境安静时调低，噪声较大时调高。

## 模型预加载

默认配置会在 FastAPI 生命周期启动、开始接收请求之前，依次加载：

- `offline`：Paraformer-zh、FSMN-VAD 和 CT-Punc
- `streaming`：paraformer-zh-streaming

```bash
FUNASR_PRELOAD_MODELS=offline,streaming
FUNASR_PRELOAD_STRICT=true
```

- `FUNASR_PRELOAD_STRICT=true` 时，任一模型加载失败都会阻止后端启动。
- 设置为空字符串可关闭预加载，但识别模式会退回首次请求懒加载。
- 模型文件缓存默认由 ModelScope 管理；后续重启直接读取本地缓存，不会重新下载模型。
- 模型参数仍需要在每次后端启动时载入内存，这是 FunASR 运行模型所必需的。

## Docker 打包部署

后端和前端分别使用独立 Dockerfile：

- `backend/Dockerfile`：Python/FunASR 运行环境，可选在构建镜像时下载并固化模型缓存。
- `frontend/Dockerfile`：Node 多阶段构建 Vue 静态文件，由 Nginx 托管并反向代理 WebSocket。
- `docker-compose.yml`：编排前后端、模型持久卷和健康检查。

一键构建和启动：

```bash
cp .env.example .env
docker compose build
docker compose up -d
```

访问 <http://localhost:8080>。

默认 `PRELOAD_MODELS=true`，构建后端镜像时会执行模型下载和初始化。首次构建需要下载 PyTorch、FunASR 和数百 MB 到数 GB 的模型文件，耗时取决于网络。

模型缓存同时保存在 Docker 命名卷 `funasr-models`：

- 容器重启不会重新下载模型。
- `docker compose down` 不会删除模型。
- `docker compose down -v` 会删除模型卷，下次启动需要重新下载。
- 如果镜像构建时选择 `PRELOAD_MODELS=false`，首次容器启动时会下载模型到持久卷。

使用国内镜像源构建：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple NPM_REGISTRY=https://registry.npmmirror.com docker compose build
```

不把模型写入镜像、加快镜像构建：

```bash
PRELOAD_MODELS=false docker compose build
docker compose up -d
```

常用的 Compose 命令：

```bash
make docker-build
make docker-up
make docker-logs
make docker-down
```

## 生产构建（非 Docker）

```bash
source .venv/bin/activate
make build
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

`frontend/dist` 存在时，FastAPI 会直接托管静态前端，因此生产环境只需要访问 <http://127.0.0.1:8000>。HTTPS 可由 Nginx、Caddy 或云负载均衡器终止，此时浏览器会自动使用 WSS。

## API

- `GET /api/v1/health`：服务及模型加载状态
- `GET /api/v1/modes`：可用识别模式
- `GET /docs`：OpenAPI 文档
- `WS /api/v1/asr/stream`：实时音频识别

WebSocket 消息格式和事件说明见 [docs/protocol.md](docs/protocol.md)。

## 命令行模式

原 CLI 功能仍然可用：

```bash
python main.py
python main.py --mode stream
python main.py --mode push --push-duration 8
python main.py --list-devices
python main.py --mode auto --save-text ./out.txt
```

查看完整参数：

```bash
python main.py --help
```

## 测试与检查

```bash
source .venv/bin/activate
make test       # pytest + Vue TypeScript 类型检查
make lint       # Ruff
make build      # 前端生产构建
```

后端测试不加载真实模型，因此不会下载模型文件。

## 配置

后端读取以下环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FUNASR_APP_NAME` | `FunASR Realtime API` | 服务名称 |
| `FUNASR_MODEL_REVISION` | `v2.0.4` | 固定模型版本 |
| `FUNASR_CORS_ORIGINS` | 本地 Vite 地址 | 逗号分隔的前端来源 |
| `FUNASR_VAD_ENERGY` | `0.012` | 默认 VAD RMS 阈值 |
| `FUNASR_VAD_HANGOVER_SEC` | `0.6` | 静音多久结束一句 |
| `FUNASR_VAD_MIN_SPEECH_SEC` | `0.25` | 最短有效语音 |
| `FUNASR_VAD_MAX_SPEECH_SEC` | `30` | 单句最长语音 |
| `FUNASR_MAX_PUSH_SEC` | `120` | 按键模式单次最长录音 |
| `FUNASR_PRELOAD_MODELS` | `offline,streaming` | 后端启动前预加载的模型 |
| `FUNASR_PRELOAD_STRICT` | `true` | 预加载失败时是否阻止服务启动 |

## 排查

- 无法打开麦克风：确认使用 `localhost` 或 HTTPS，并检查浏览器站点权限。
- 一直停留在“加载模型”：首次运行正在下载模型，查看后端终端日志。
- 有音量但没有结果：调低 VAD 阈值，或换用“按键说话”模式验证识别链路。
- WebSocket 连接失败：确认后端运行在 `8000` 端口，且代理的 WebSocket 转发已开启。
- WebSocket 经常断开：检查 Nginx/网关的 WebSocket 空闲超时，连接路径至少设置
  `proxy_read_timeout 3600s`、`proxy_send_timeout 3600s`，并关闭代理缓冲。
- 短暂断线：识别过程中前端会自动重连；重连期间麦克风保持采集，但网络中断窗口内的音频会丢失。
- 没有声音输入：在系统设置中确认默认输入设备，并关闭占用麦克风的其他应用。

### Nginx WebSocket 配置

```nginx
location /api/v1/asr/stream {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_buffering off;
}
```
