import React, { useState } from 'react'
import { saveKeys } from '../api'

export default function ApiKeys() {
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [status, setStatus] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    try {
      await saveKeys({ apiKey, apiSecret })
      setStatus('Saved API keys.')
      setApiKey(''); setApiSecret('')
    } catch (err) { setStatus('Failed to save API keys.') }
  }

  return (
    <div className="locked-wrap">
      <div className="glass block" style={{ maxWidth: 520 }}>
        <div className="card-head">
          <h2 className="card-title">Connect API</h2>
        </div>
        <p className="muted" style={{ margin: '6px 0 12px' }}>
          Keys are stored server‑side and used only for trading (no withdrawals).
        </p>
        <form onSubmit={handleSubmit} style={{ display: 'grid', gap: 8 }}>
          <input
            type="text" placeholder="API Key" value={apiKey}
            onChange={e => setApiKey(e.target.value)} className="btn"
            style={{ background: 'rgba(17,17,19,0.6)' }}
          />
          <input
            type="password" placeholder="API Secret" value={apiSecret}
            onChange={e => setApiSecret(e.target.value)} className="btn"
            style={{ background: 'rgba(17,17,19,0.6)' }}
          />
          <button type="submit" className="btn">Save</button>
        </form>
        {status && <p style={{ marginTop: 8 }}>{status}</p>}
      </div>
    </div>
  )
}
