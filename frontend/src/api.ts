const API_BASE = 'http://localhost:8000'

export async function getStatus(signal?: AbortSignal) {
  const r = await fetch(`${API_BASE}/status?t=${Date.now()}`, {
    cache: 'no-store',
    headers: { 'Cache-Control': 'no-store' },
    signal,
  })
  return r.json()
}

export async function getLogs(limit: number = 200, signal?: AbortSignal) {
  const r = await fetch(`${API_BASE}/logs?limit=${limit}&t=${Date.now()}`, {
    cache: 'no-store',
    headers: { 'Cache-Control': 'no-store' },
    signal,
  })
  return r.json()
}

export async function postSettings(body: any) {
  await fetch(`${API_BASE}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function startBot() {
  await fetch(`${API_BASE}/start`, { method: 'POST' })
}

export async function stopBot() {
  await fetch(`${API_BASE}/stop`, { method: 'POST' })
}

export async function saveKeys(k: { apiKey: string; apiSecret: string }) {
  await fetch(`${API_BASE}/apikeys`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(k),
  })
}
