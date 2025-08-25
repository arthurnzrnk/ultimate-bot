import React, { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { getStatus, postSettings } from './api'

interface Props { children: React.ReactNode }

function fmt(n: any, d = 2) {
  if (n == null || isNaN(n)) return '—'
  return Number(n).toLocaleString(undefined, {
    maximumFractionDigits: d,
    minimumFractionDigits: d,
  })
}

export default function App({ children }: Props) {
  const loc = useLocation()

  // Header state (global so it applies to all pages)
  const [auto, setAuto] = useState(false)
  const [posSide, setPosSide] = useState<'long' | 'short' | null>(null)
  const [price, setPrice] = useState<number | null>(null)
  const [dir, setDir] = useState<'up' | 'down' | null>(null)
  const lastPrice = useRef<number | undefined>(undefined)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const s = await getStatus()
        if (!alive) return
        setAuto(Boolean(s.autoTrade))
        setPosSide(s?.pos?.side ?? null)
        const shown = s?.price ?? (s?.bid != null && s?.ask != null ? (s.bid + s.ask) / 2 : null)
        if (typeof shown === 'number') {
          setPrice(shown)
          const prev = lastPrice.current
          setDir(
            prev == null
              ? null
              : Math.round(shown * 100) > Math.round(prev * 100)
              ? 'up'
              : Math.round(shown * 100) < Math.round(prev * 100)
              ? 'down'
              : null
          )
          lastPrice.current = shown
        }
      } catch {
        // ignore polling hiccups
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const tone = !auto ? 'gray' : posSide ? (posSide === 'long' ? 'green' : 'red') : 'orange'

  async function toggleAuto() {
    const next = !auto
    await postSettings({ autoTrade: next })
    setAuto(next)
  }

  return (
    <div className="app-shell">
      {/* Ambient gradient that tints all glass */}
      <div className={`glow-dynamic tone-${tone}`} />

      {/* LOCKED CENTER CONTAINER */}
      <header className="header container">
        <div className="left">
          <div className="brand">COINSELF</div>
          <button
            className={`switch ${auto ? 'on' : 'off'}`}
            onClick={toggleAuto}
            aria-label="Toggle bot"
            title={`Auto trading is ${auto ? 'ON' : 'OFF'}`}
          >
            <span className="dot" />
            <span className="switch-label">{auto ? 'ON' : 'OFF'}</span>
          </button>
        </div>

        <div className="right">
          <div className="pair">BTC/USD</div>
          <div className={`price ${dir === 'up' ? 'up' : dir === 'down' ? 'down' : ''}`}>
            ${fmt(price, 2)}
          </div>
        </div>
      </header>

      <main className="container">
        {children}
      </main>

      {/* Minor route hint for API page; no top nav per spec */}
      {loc.pathname !== '/apikeys' ? (
        <div className="api-hint container">
          {/* empty on purpose to keep spacing consistent */}
        </div>
      ) : null}
    </div>
  )
}
