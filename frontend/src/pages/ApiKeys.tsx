import React, { useState } from 'react'
import { saveKeys } from '../api'

/**
 * API Keys Page
 *
 * This page allows users to submit their exchange API key and secret to the
 * backend for secure storage. The keys are stored on the server side and
 * never exposed back to the client. Users can update their keys at any
 * time. In a production environment, consider adding additional security
 * measures such as password protection or two‑factor authentication. For
 * demonstration purposes this simple form is sufficient.
 */
export default function ApiKeys() {
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [status, setStatus] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    try {
      await saveKeys({ apiKey, apiSecret })
      setStatus('Saved API keys successfully.')
      setApiKey('')
      setApiSecret('')
    } catch (err) {
      console.error(err)
      setStatus('Failed to save API keys. Please try again.')
    }
  }

  return (
    <div className="glass" style={{ padding: 12, maxWidth: 520 }}>
      <h2 style={{ fontWeight: 600, marginBottom: 8 }}>Connect Exchange</h2>
      <p style={{ opacity: 0.8, fontSize: 14, marginBottom: 12 }}>
        Enter your API credentials below to connect your exchange account. Your
        keys will be stored securely on the server and used exclusively for
        trading. For safety, create API keys with trading permissions only and
        never include withdrawal rights.
      </p>
      <form onSubmit={handleSubmit} style={{ display: 'grid', gap: 8 }}>
        <input
          type="text"
          placeholder="API Key"
          value={apiKey}
          onChange={e => setApiKey(e.target.value)}
          className="btn"
          style={{ background: '#0b1220' }}
        />
        <input
          type="password"
          placeholder="API Secret"
          value={apiSecret}
          onChange={e => setApiSecret(e.target.value)}
          className="btn"
          style={{ background: '#0b1220' }}
        />
        <button type="submit" className="btn">
          Save
        </button>
      </form>
      {status && (
        <p style={{ marginTop: 8, fontSize: 14 }}>{status}</p>
      )}
    </div>
  )
}