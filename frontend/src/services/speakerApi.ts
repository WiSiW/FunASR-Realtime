import type { SpeakerProfile } from '../types/asr'

const API_BASE = '/api/v1/speakers'

async function readError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string }
    return payload.detail || `请求失败 (${response.status})`
  } catch {
    return `请求失败 (${response.status})`
  }
}

export async function listSpeakerProfiles(): Promise<SpeakerProfile[]> {
  const response = await fetch(API_BASE)
  if (!response.ok) {
    throw new Error(await readError(response))
  }
  const payload = (await response.json()) as { items?: SpeakerProfile[] }
  return payload.items || []
}

export async function enrollSpeakerProfile(
  name: string,
  pcm: Int16Array,
): Promise<SpeakerProfile> {
  const payload = pcm.buffer.slice(
    pcm.byteOffset,
    pcm.byteOffset + pcm.byteLength,
  ) as ArrayBuffer
  const query = new URLSearchParams({
    name,
    sample_rate: '16000',
  })
  const response = await fetch(`${API_BASE}/enroll?${query}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: payload,
  })
  if (!response.ok) {
    throw new Error(await readError(response))
  }
  return (await response.json()) as SpeakerProfile
}

export async function deleteSpeakerProfile(speakerId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(speakerId)}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    throw new Error(await readError(response))
  }
}
