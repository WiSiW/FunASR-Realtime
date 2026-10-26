# FunASR Realtime Chrome 扩展

Manifest V3 Chrome 扩展，使用 `tabCapture + offscreen document` 持续采集当前标签页音频，重采样为 16kHz 单声道 PCM 后，通过 WebSocket 发送到现有 FunASR Realtime 后端。

## 功能

- 采集当前标签页而不是麦克风或整个系统音频
- 自动重采样为 16kHz、单声道、`pcm_s16le`
- 支持 `auto`、`stream`、`push` 三种识别模式
- 弹窗关闭后由 offscreen document 继续采集和转写
- 实时音量、连接状态、临时文本、最终文本和说话人标签
- WebSocket 心跳、超时检测和指数退避重连
- 后端地址和识别设置保存在 `chrome.storage.local`
- 无构建步骤，直接加载目录即可

## 加载扩展

1. 启动后端：

   ```bash
   make dev-backend
   ```

2. 打开 `chrome://extensions`。
3. 开启右上角“开发者模式”。
4. 点击“加载已解压的扩展程序”。
5. 选择本仓库的 `chrome-extension` 目录。
6. 点击扩展弹窗中的“本地网络授权”，在浏览器提示中选择“允许”。

扩展要求 Chrome/Edge 116 或更高版本。已经加载过旧版本时，修改 Manifest 后必须在 `chrome://extensions` 或 `edge://extensions` 中点击“重新加载”。

默认后端地址：

```text
ws://127.0.0.1:8000/api/v1/asr/stream
```

也可以填写 `http://127.0.0.1:8000`，扩展会自动转换协议并补全 WebSocket 路径。

## 使用

1. 打开需要转写的网页，并确保页面正在播放音频。
2. 点击工具栏中的扩展图标。
3. 首次使用时点击“本地网络授权”，在扩展设置页点击“授权并检测连接”。
4. 选择识别模式并点击“开始转写”。
5. 关闭弹窗不会停止采集；再次打开弹窗可以查看文本或停止。

`chrome://`、扩展页面、开发者工具页面以及部分受保护页面不能被 `tabCapture` 采集。扩展只采集当前点击开始时的活动标签页，不会自动跟随标签页切换。

## 后端连接

- 本地使用 `ws://` 即可。
- 部署到其他主机时建议使用 `wss://`，并确保后端允许来自扩展的 WebSocket 连接。
- 后端协议与浏览器前端完全相同，接口为 `/api/v1/asr/stream`。

如果扩展提示无法连接，先确认 `127.0.0.1:8000` 返回的是当前 FunASR 服务：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

正常响应中的 `service` 应为 `FunASR Realtime API`。若返回 404、403 或其他服务名称，说明 8000 端口被其他进程占用：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8000 |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

需要结束冲突进程或改用其他端口，并同步修改扩展中的后端地址。修改扩展代码后，还需在 `chrome://extensions` 中点击“重新加载”。

Chromium 可能对扩展执行 Local Network Access 检查。后端会针对扩展来源返回 `Access-Control-Allow-Private-Network: true`，扩展设置页则会通过一次本地健康检查触发浏览器授权提示。两者结合后无需依赖 Chrome 尚不支持的 `localNetworkAccess` Manifest 权限。

如果在设置页检测成功，但开始转写仍提示无法连接，请重新加载扩展后再次执行“授权并检测连接”。

## 结构

```text
chrome-extension/
├── manifest.json
├── audio/
│   └── pcm-capture-worklet.js
├── popup/
│   ├── popup.html
│   ├── popup.css
│   └── popup.js
├── options/
│   ├── options.html
│   ├── options.css
│   └── options.js
├── shared/
│   ├── asr-socket.js
│   ├── config.js
│   └── resampler.js
└── src/
    ├── background.js
    ├── offscreen.html
    └── offscreen.js
```

主要分工：

- `popup`：选择后端、模式和开始/停止，展示转写结果。
- `background.js`：管理会话状态、tabCapture stream ID 和 offscreen document 生命周期。
- `offscreen.js`：持有标签页 MediaStream、AudioContext 和 WebSocket，持续发送 PCM。

## 校验

扩展不依赖第三方包。执行以下命令可检查 Manifest、资源路径、JavaScript 语法和共享协议工具：

```bash
npm --prefix chrome-extension run check
```

后端已启动且本机安装了 Microsoft Edge 时，可以在真实扩展上下文中验证 WebSocket：

```bash
npm --prefix chrome-extension run smoke:browser
```
