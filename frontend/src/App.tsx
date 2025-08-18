import React from 'react'
import { Link, useLocation } from 'react-router-dom'

interface Props {
  children: React.ReactNode
}

export default function App({ children }: Props) {
  const loc = useLocation()
  return (
    <div style={{ minHeight: '100vh', color: '#e5e7eb' }}>
      <div className="bg" /><div className="glow" />
      <header style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 16px' }}>
        <div style={{ fontWeight: 700 }}>Ultimate Bot</div>
        <nav style={{ display: 'flex', gap: 12 }}>
          <Link to="/" className="chip" style={{ borderColor: loc.pathname === '/' ? '#7dd3fc' : '' }}>UI</Link>
          {/* Logs now live on the Dashboard, so we remove the separate Status route */}
          <Link to="/apikeys" className="chip" style={{ borderColor: loc.pathname === '/apikeys' ? '#7dd3fc' : '' }}>API</Link>
          <Link to="/learning" className="chip" style={{ borderColor: loc.pathname === '/learning' ? '#7dd3fc' : '' }}>Learning</Link>
        </nav>
      </header>
      <main style={{ padding: 16 }}>{children}</main>
    </div>
  )
}
