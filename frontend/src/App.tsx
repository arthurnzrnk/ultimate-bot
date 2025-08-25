import React from 'react'
import { Link, useLocation } from 'react-router-dom'

interface Props {
  children: React.ReactNode
}

export default function App({ children }: Props) {
  const loc = useLocation()
  return (
    <div style={{ minHeight: '100vh', color: 'var(--text)' }}>
      {/* Ambient background layers (liquid glass look) */}
      <div className="bg" />
      <div className="glow" />

      {/* Header */}
      <header className="app-header">
        <div className="brand">Ultimate Bot</div>
        <nav className="nav">
          <Link to="/" className="chip" style={{ borderColor: loc.pathname === '/' ? 'rgba(56,189,248,0.50)' : undefined }}>Dashboard</Link>
          <Link to="/apikeys" className="chip" style={{ borderColor: loc.pathname === '/apikeys' ? 'rgba(56,189,248,0.50)' : undefined }}>API</Link>
          <Link to="/learning" className="chip" style={{ borderColor: loc.pathname === '/learning' ? 'rgba(56,189,248,0.50)' : undefined }}>Learning</Link>
        </nav>
      </header>

      <main style={{ padding: 16 }}>{children}</main>
    </div>
  )
}
