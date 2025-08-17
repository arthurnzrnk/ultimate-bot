import React, { useEffect, useRef, useState } from 'react'
import { getStatus, postSettings } from '../api'

interface Pos {
  side: 'long' | 'short'
  qty: number
  entry: number
  stop: number
  take: number
  stop_dist: number
  open_time: number
  fee_rate: number
  hi: number
  lo: number
  be: boolean
}

interface Trade {
  side: 'long' | 'short'
  entry: number
  close: number
  pnl: number
  open_time: number
  close_time: number
}

interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export default function Dashboard() {
  const [data, setData] = useState<any>({ history: [], candles: [], scalpMode: true, autoTrade: true, strategy: 'Level King — Regime' })
  const [dir, setDir] = useState<'up' | 'down' | null>(null)
  const lastShown = useRef<number | undefined>(undefined)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      const s = await getStatus()
      setData(s)
      const shown = s.price ?? (s.bid && s.ask ? (s.bid + s.ask) / 2 : null)
      if (typeof shown === 'number') {
        const prev = lastShown.current
        setDir(prev == null ? null : Math.round(shown * 100) > Math.round(prev * 100) ? 'up' : Math.round(shown * 100) < Math.round(prev * 100) ? 'down' : null)
        lastShown.current = shown
      }
    }
    tick()
    const h = setInterval(() => alive && tick(), 1000)
    return () => {
      alive = false
      clearInterval(h)
    }
  }, [])

  const fmt = (n: any, d = 2) => (n == null || isNaN(n) ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }))
  const px = data?.price ?? null
  const headerClass = dir === 'up' ? 'price-up' : dir === 'down' ? 'price-down' : ''

  async function toggleScalp() {
    await postSettings({ scalpMode: !data.scalpMode })
    setData((d: any) => ({ ...d, scalpMode: !d.scalpMode }))
  }
  async function toggleAuto() {
    await postSettings({ autoTrade: !data.autoTrade })
    setData((d: any) => ({ ...d, autoTrade: !d.autoTrade }))
  }

  return (
    <div>
      <div className="glass" style={{ padding: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 12, opacity: 0.8 }}>BTC/USD</div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>
            <span className={headerClass}>${fmt(px, 2)}</span> <span style={{ fontSize: 12, opacity: 0.7 }}>(COINBASE)</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <span className={`chip ${data.scalpMode ? 'green' : 'neutral'}`}>{data.scalpMode ? '1m' : '1h'}</span>
          <span className={`chip ${data.price ? 'green' : 'red'}`}>{data.price ? 'NET: LIVE' : 'NET: STALE'}</span>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1.8fr', gap: 12, marginTop: 12 }}>
        <div className="glass" style={{ padding: 12 }}>
          <div style={{ fontWeight: 600 }}>STATUS: {data.status}</div>
          <div style={{ marginTop: 8, fontWeight: 600 }}>CONDITIONS: {/* future: show reason text */}</div>
          <div style={{ opacity: 0.8, fontSize: 12, marginTop: 4 }}>P&amp;L today: {fmt(data.pnlToday, 2)} / cap ±$500; fills {data.fillsToday}/60</div>
          <div style={{ marginTop: 12, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div className="glass" style={{ padding: 12 }}>
              <div style={{ opacity: 0.8, fontSize: 12 }}>Equity</div>
              <div style={{ fontWeight: 700, fontSize: 18 }}>${fmt(data.equity, 2)}</div>
            </div>
            <div className="glass" style={{ padding: 12 }}>
              <div style={{ opacity: 0.8, fontSize: 12 }}>Unrealized (net)</div>
              <div className={(data.unrealNet ?? 0) >= 0 ? 'price-up' : 'price-down'} style={{ fontWeight: 700, fontSize: 18 }}>
                {(data.unrealNet ?? 0) >= 0 ? '+' : ''}{fmt(data.unrealNet, 2)}
              </div>
            </div>
          </div>
          <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
            <button className="btn" onClick={toggleScalp}>Mode: {data.scalpMode ? 'Scalper (1m)' : 'High-Hit (1h)'}</button>
            <button className="btn" onClick={toggleAuto}>Auto Trading is {data.autoTrade ? 'ON' : 'OFF'}</button>
          </div>
        </div>
        <div className="glass" style={{ padding: 12, maxHeight: 320, overflow: 'auto' }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>Trade History</div>
          <table className="table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Side</th>
                <th style={{ textAlign: 'right' }}>Entry</th>
                <th style={{ textAlign: 'right' }}>Exit</th>
                <th style={{ textAlign: 'right' }}>PNL</th>
              </tr>
            </thead>
            <tbody>
              {[...(data.history || [])].reverse().map((t: any, i: number) => (
                <tr key={i}>
                  <td>{new Date(((t.close_time ?? t.open_time) || 0) * 1000).toLocaleString()}</td>
                  <td className="capitalize">{t.side}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(t.entry, 2)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(t.close, 2)}</td>
                  <td style={{ textAlign: 'right' }} className={(t.pnl ?? 0) >= 0 ? 'price-up' : 'price-down'}>
                    {(t.pnl ?? 0) >= 0 ? '+' : ''}{fmt(t.pnl, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="glass" style={{ padding: 12, marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Position</div>
        {data.pos ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 6, fontSize: 13 }}>
            <div><div>Side</div><div style={{ fontWeight: 600, textTransform: 'capitalize' }}>{data.pos.side}</div></div>
            <div><div>Qty</div><div style={{ fontWeight: 600 }}>{fmt(data.pos.qty, 6)}</div></div>
            <div><div>Entry</div><div style={{ fontWeight: 600 }}>${fmt(data.pos.entry, 2)}</div></div>
            <div><div>Stop</div><div style={{ fontWeight: 600 }}>${fmt(data.pos.stop, 2)}</div></div>
            <div><div>Take</div><div style={{ fontWeight: 600 }}>${fmt(data.pos.take, 2)}</div></div>
          </div>
        ) : <div style={{ opacity: 0.8, fontSize: 13 }}>No open position.</div>}
      </div>
    </div>
  )
}