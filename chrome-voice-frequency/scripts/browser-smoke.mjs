import { spawn } from 'node:child_process'
import { existsSync, rmSync } from 'node:fs'
import { createServer } from 'node:net'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

const sourceExtensionPath = new URL('..', import.meta.url).pathname.replace(
  /^\/([A-Za-z]:)/,
  '$1',
)
const extensionPath = sourceExtensionPath
const backendUrl =
  process.env.FUNASR_WS_URL || 'ws://127.0.0.1:8000/api/v1/asr/stream'
const edgePath = findEdge()
const debugPort = await findFreePort()
const userDataDir = join(tmpdir(), `funasr-extension-smoke-${process.pid}`)

async function main() {
  if (!globalThis.WebSocket) {
    throw new Error('Node.js 22+ is required for the browser smoke test')
  }

  const edge = spawn(
    edgePath,
    [
      '--headless=new',
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`,
      `--remote-debugging-port=${debugPort}`,
      `--user-data-dir=${userDataDir}`,
      'about:blank',
    ],
    {
      stdio: 'ignore',
      windowsHide: true,
    },
  )

  try {
    const version = await pollJson(
      `http://127.0.0.1:${debugPort}/json/version`,
      15_000,
    )
    const client = await CdpClient.connect(version.webSocketDebuggerUrl)
    try {
      let page
      let extensionTarget = null
      if (process.env.SMOKE_PAGE) {
        page = await client.send('Target.createTarget', {
          url: 'data:text/html,<title>LNA probe</title>',
        })
      } else {
        extensionTarget = await waitForExtensionTarget(client)
        const extensionId = extensionTarget.extensionId
        page =
          process.env.SMOKE_TARGET === 'background'
            ? { targetId: extensionTarget.targetId }
            : await client.send('Target.createTarget', {
                url: `chrome-extension://${extensionId}/src/offscreen.html`,
              })
      }
      const attached = await client.send('Target.attachToTarget', {
        targetId: page.targetId,
        flatten: true,
      })
      const browserLogs = []
      client.onEvent((message) => {
        if (message.sessionId !== attached.sessionId) {
          return
        }
        if (message.method === 'Log.entryAdded') {
          browserLogs.push(message.params.entry.text)
        }
        if (message.method === 'Runtime.exceptionThrown') {
          browserLogs.push(
            message.params.exceptionDetails?.exception?.description ||
              message.params.exceptionDetails?.text ||
              'Runtime exception',
          )
        }
      })
      await client.send('Log.enable', {}, attached.sessionId)
      await client.send('Runtime.enable', {}, attached.sessionId)
      const result = await client.send(
        'Runtime.evaluate',
        {
          expression: buildProbeExpression(backendUrl),
          awaitPromise: true,
          returnByValue: true,
          userGesture: true,
        },
        attached.sessionId,
      )
      const value = result.result?.value
      if (!value?.ok) {
        const details = browserLogs.length
          ? `\nBrowser logs:\n${browserLogs.join('\n')}`
          : ''
        throw new Error(
          `Extension WebSocket probe failed: ${value?.error}${details}\nEvaluation result: ${JSON.stringify(result)}`,
        )
      }
      console.log(`Extension WebSocket connected: ${value.data}`)
    } finally {
      client.close()
    }
} finally {
  await stopProcess(edge)
  removeDirectory(userDataDir)
  }
}

function findEdge() {
  const candidates = [
    process.env.BROWSER_PATH,
    process.env.EDGE_PATH,
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ].filter(Boolean)
  const match = candidates.find((candidate) => existsSync(candidate))
  if (!match) {
    throw new Error(
      'Chromium browser was not found; set BROWSER_PATH or EDGE_PATH to run this test',
    )
  }
  return match
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      server.close(() => resolve(address.port))
    })
  })
}

async function pollJson(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) {
        return response.json()
      }
    } catch {
      // Edge is still starting.
    }
    await delay(100)
  }
  throw new Error(`Timed out waiting for ${url}`)
}

async function waitForExtensionTarget(client) {
  const deadline = Date.now() + 15_000
  while (Date.now() < deadline) {
    const targets = await client.send('Target.getTargets')
    for (const target of targets.targetInfos) {
      const match = String(target.url || '').match(
        /^chrome-extension:\/\/([a-p]{32})\/src\/background\.js(?:[?#].*)?$/,
      )
      if (match) {
        return {
          extensionId: match[1],
          targetId: target.targetId,
        }
      }
    }
    await delay(100)
  }
  throw new Error('Loaded extension target was not found')
}

function buildProbeExpression(url) {
  const serializedUrl = JSON.stringify(url)
  return `new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (timer !== null && typeof globalThis.clearTimeout === 'function') {
        globalThis.clearTimeout(timer);
      }
      resolve(value);
    };
    const timer = typeof globalThis.setTimeout === 'function'
      ? globalThis.setTimeout(() => finish({ ok: false, error: 'timeout' }), 5000)
      : null;
    const socket = new WebSocket(${serializedUrl});
    socket.onmessage = (event) => {
      const data = String(event.data);
      socket.close(1000, 'smoke test complete');
      finish({ ok: data.includes('"type":"connected"'), data, error: data });
    };
    socket.onerror = () => finish({ ok: false, error: 'WebSocket error event' });
    socket.onclose = (event) => {
      if (!settled) {
        finish({ ok: false, error: 'WebSocket closed ' + event.code });
      }
    };
    });`
}

class CdpClient {
  static async connect(url) {
    const client = new CdpClient(url)
    await client.open()
    return client
  }

  constructor(url) {
    this.url = url
    this.socket = null
    this.nextId = 1
    this.pending = new Map()
    this.eventHandlers = new Set()
  }

  open() {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(this.url)
      this.socket = socket
      socket.onopen = () => resolve()
      socket.onerror = () => reject(new Error('CDP WebSocket connection failed'))
      socket.onmessage = (event) => this.handleMessage(event.data)
    })
  }

  send(method, params = {}, sessionId) {
    const id = this.nextId++
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.socket.send(
        JSON.stringify({
          id,
          method,
          params,
          ...(sessionId ? { sessionId } : {}),
        }),
      )
    })
  }

  handleMessage(raw) {
    const message = JSON.parse(String(raw))
    if (!message.id) {
      for (const handler of this.eventHandlers) {
        handler(message)
      }
      return
    }
    const pending = this.pending.get(message.id)
    if (!pending) {
      return
    }
    this.pending.delete(message.id)
    if (message.error) {
      pending.reject(new Error(message.error.message))
    } else {
      pending.resolve(message.result)
    }
  }

  close() {
    this.socket?.close()
  }

  onEvent(handler) {
    this.eventHandlers.add(handler)
    return () => this.eventHandlers.delete(handler)
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function stopProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return
  }
  child.kill()
  await Promise.race([
    new Promise((resolve) => child.once('exit', resolve)),
    delay(3000),
  ])
}

function removeDirectory(path) {
  try {
    rmSync(path, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 })
  } catch {
    // Chromium can keep a lock briefly after process exit.
  }
}

await main()
