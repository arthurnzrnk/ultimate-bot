import React from 'react'

export default function Learning() {
  return (
    <div className="glass" style={{ padding: 12, maxWidth: 720 }}>
      <h2 className="card-title">Learning Center</h2>
      <p className="muted">
        This is a placeholder page so the router compiles. Plug in notes, docs, or links you want at
        <code style={{ marginLeft: 6 }}>src/pages/Learning.tsx</code>.
      </p>
      <ul style={{ marginTop: 8, lineHeight: 1.6 }}>
        <li>• Strategy spec: V3.4 (m1 Level King + H1 trio)</li>
        <li>• Risk: fee‑aware, live‑risk cap, day‑lock, giveback, red‑day throttles</li>
        <li>• Fast tape: taker gates, 0.18 fee bound, fallback to maker</li>
        <li>• Health: heartbeat stalls, latency halts, spread‑instability m1‑only block</li>
      </ul>
    </div>
  )
}
