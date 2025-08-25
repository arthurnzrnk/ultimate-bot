import React, { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { getStatus, postSettings } from './api'

interface Props {
  children: React.ReactNode
}

export default function App({ children }: Props) {
  const _loc = useLocation()

  // Header price + toggle state
  const [auto, setAuto] = useState(false)
  const [price, setPrice] = useState<number | null>(null)
  const [dir, setDir] = useState<'up' | 'down' | null>(null)
  const lastShown = useRef<number | null>(null)

  // Poll just enough for header (price + auto state)
  useEffect(() => {
    let alive = true
    const tick = async () => {
      const s = await getStatus()
      if (!alive) return
      setAuto(Boolean(s.autoTrade))
      const shown = s.price ?? (s.bid && s.ask ? (s.bid + s.ask) / 2 : null)
      if (typeof shown === 'number') {
        const prev = lastShown.current
        setDir(
          prev == null
            ? null
            : Math.round(shown * 100) > Math.round(prev * 100)
            ? 'up'
            : Math.round(shown * 100) < Math.round(prev * 100)
            ? 'down'
            : null
        )
        lastShown.current = shown
        setPrice(shown)
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  async function toggleAuto() {
    const next = !auto
    await postSettings({ autoTrade: next })
    setAuto(next)
  }

  return (
    <div style={{ minHeight: '100vh', color: 'var(--text)' }}>
      {/* Ambient layers */}
      <div className="bg" />
      <div className="glow" />

      {/* Header: COINSELF + toggle + price on far right */}
      <header className="app-header locked-wrap">
        <div className="title-row">
          <div className="brand-main">COINSELF</div>
          <div
            role="switch"
            aria-checked={auto}
            className={`switch ${auto ? 'on' : 'off'}`}
            onClick={toggleAuto}
            title="Toggle bot"
          >
            <span className="label on">ON</span>
            <span className="label off">OFF</span>
            <span className="thumb" />
          </div>
          {auto && (
            <div className="run-hint">
              The system will trade automatically until you turn the bot OFF.
            </div>
          )}
        </div>

        <div className={`ticker ${dir === 'up' ? 'up' : dir === 'down' ? 'down' : ''}`}>
          <span className="ticker-pair">BTC/USD</span>
          <span className="ticker-price">
            {price == null
              ? '—'
              : `$${Number(price).toLocaleString(undefined, {
                  maximumFractionDigits: 2,
                  minimumFractionDigits: 2,
                })}`}
          </span>
        </div>
      </header>

      <main>
        <div className="locked-wrap">{children}</div>
      </main>
    </div>
  )
}
