import React, { useEffect, useState } from 'react'

/**
 * Status Page
 *
 * This page is intended to display verbose reasoning and status logs from the
 * backend trading bot. Currently it's a simple placeholder that will
 * eventually subscribe to a stream of status updates once that API is
 * implemented. Keeping a dedicated page for the bot's thinking process
 * separates long-form status text from the main dashboard, preventing it from
 * cluttering the UI. In a future iteration this component could poll an
 * endpoint or listen to a WebSocket and render a list of log messages or
 * notifications explaining the bot's decisions.
 */
export default function Status() {
  const [logs, setLogs] = useState<string[]>([])

  // Placeholder effect: this could be replaced with real polling or SSE once
  // implemented on the server. For now it simply sets an empty array.
  useEffect(() => {
    setLogs([])
  }, [])

  return (
    <div className="glass" style={{ padding: 12 }}>
      <h2 style={{ fontWeight: 600, marginBottom: 8 }}>Bot Status Feed</h2>
      {logs.length === 0 ? (
        <p style={{ opacity: 0.8, fontSize: 14 }}>
          Status feed (explanations) will appear here when the backend exposes
          detailed reasoning. Check back later.
        </p>
      ) : (
        <ul style={{ listStyleType: 'none', paddingLeft: 0 }}>
          {logs.map((line, idx) => (
            <li key={idx} style={{ marginBottom: 4, fontSize: 14 }}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  )
}