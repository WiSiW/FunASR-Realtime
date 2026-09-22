import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  buildStartOptions,
  normalizeWebSocketUrl,
} from '../shared/config.js'
import { StreamingPcm16Resampler } from '../shared/resampler.js'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const manifestPath = join(root, 'manifest.json')
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const workletPath = join(root, 'audio/pcm-capture-worklet.js')
const extensionCsp = manifest.content_security_policy?.extension_pages || ''

if (!extensionCsp.includes('ws://*:*') || !extensionCsp.includes('wss://*:*')) {
  throw new Error('Extension CSP must allow WebSocket connections on any port')
}
if (
  !manifest.host_permissions?.includes('http://127.0.0.1/*') ||
  !manifest.host_permissions?.includes('http://localhost/*')
) {
  throw new Error('Extension must be allowed to connect to the local backend')
}
if (manifest.permissions?.includes('localNetworkAccess')) {
  throw new Error(
    'localNetworkAccess is not supported by Chrome manifest parsing',
  )
}
if (Number(manifest.minimum_chrome_version) > 116) {
  throw new Error('Extension should remain compatible with Chrome 116+')
}

for (const path of [
  manifest.background.service_worker,
  manifest.action.default_popup,
  manifest.options_page,
  'audio/pcm-capture-worklet.js',
  'src/offscreen.html',
  'src/offscreen.js',
]) {
  if (!existsSync(join(root, path))) {
    throw new Error(`Manifest or runtime file is missing: ${path}`)
  }
}

execFileSync(process.execPath, ['--check', workletPath], { stdio: 'inherit' })

for (const file of walk(join(root, 'src'))
  .concat(walk(join(root, 'shared')))
  .concat(walk(join(root, 'popup')))
  .concat(walk(join(root, 'options')))) {
  if (file.endsWith('.js')) {
    execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' })
  }
}

const normalized = normalizeWebSocketUrl('127.0.0.1:8000')
if (normalized !== 'ws://127.0.0.1:8000/api/v1/asr/stream') {
  throw new Error(`Unexpected normalized URL: ${normalized}`)
}
if (buildStartOptions({ mode: 'stream' }).mode !== 'stream') {
  throw new Error('Start options builder failed')
}
const resampled = new StreamingPcm16Resampler(48000, 16000).process(
  new Float32Array(480),
)
if (resampled.length !== 160) {
  throw new Error(`Unexpected resampler output length: ${resampled.length}`)
}
validateQuerySelectorIds(
  join(root, 'popup/popup.js'),
  join(root, 'popup/popup.html'),
)
validateQuerySelectorIds(
  join(root, 'options/options.js'),
  join(root, 'options/options.html'),
)

console.log('Chrome extension files and shared modules are valid.')

function walk(directory) {
  const result = []
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry)
    if (statSync(path).isDirectory()) {
      result.push(...walk(path))
    } else {
      result.push(path)
    }
  }
  return result
}

function validateQuerySelectorIds(scriptPath, htmlPath) {
  const script = readFileSync(scriptPath, 'utf8')
  const html = readFileSync(htmlPath, 'utf8')
  const htmlIds = new Set(
    Array.from(html.matchAll(/\bid="([^"]+)"/g), (match) => match[1]),
  )
  for (const match of script.matchAll(/querySelector\(['"]#([^'"]+)['"]\)/g)) {
    if (!htmlIds.has(match[1])) {
      throw new Error(
        `${scriptPath} references missing HTML id "#${match[1]}"`,
      )
    }
  }
}
