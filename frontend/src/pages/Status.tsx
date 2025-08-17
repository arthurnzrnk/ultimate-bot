import React, { useEffect, useRef, useState } from 'react'
import { getLogs } from '../api'

type LogLine = { ts: number; text: string }

export default function Status() {
  const [logs, setLogs] = useState<LogLine[]>([])
  const [loading, setLoading] = useState(true)
  const boxRef = useRef<HTMLDivElement | null>(null)

  // Auto-scroll to bottom when new logs arrive (if already near bottom)
  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (nearBottom) {
      el.scrollTop = el.scrollHeight
    }
  }, [logs])

  useEffect(() => {
    let alive = true

    const fetchLogs = async () => {
      try {
        const data = await getLogs(300)
        if (!alive) return
        setLogs(data.logs || [])
      } catch (err) {
        console.error(err)
      } finally {
        if (alive) setLoading(false)
      }
    }

    fetchLogs()
    const id = setInterval(fetchLogs, 2000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  return (
    <div className="glass" style={{ padding: 12 }}>
      <h2 style={{ fontWeight: 600, marginBottom: 8 }}>Bot Status Feed</h2>

      {loading && logs.length === 0 ? (
        <p style={{ opacity: 0.8, fontSize: 14 }}>Loading logs…</p>
      ) : logs.length === 0 ? (
        <p style={{ opacity: 0.8, fontSize: 14 }}>
          No logs yet. Once the engine opens/closes trades or enters cool‑off,
          messages will show up here.
        </p>
      ) : (
        <div
          ref={boxRef}
          style={{
            maxHeight: 420,
            overflow: 'auto',
            padding: 8,
            borderRadius: 12,
            background: 'rgba(0,0,0,0.25)',
            border: '1px solid rgba(255,255,255,0.12)',
          }}
        >
          <ul style={{ listStyleType: 'none', paddingLeft: 0, margin: 0 }}>
            {logs.map((l, idx) => (
              <li key={idx} style={{ marginBottom: 6, fontSize: 14 }}>
                <span style={{ opacity: 0.7, marginRight: 8 }}>
                  {new Date(l.ts * 1000).toLocaleTimeString()}
                </span>
                <span>{l.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
