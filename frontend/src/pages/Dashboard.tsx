import React, { useEffect, useMemo, useRef, useState } from 'react'
import { getStatus, postSettings, getLogs } from '../api'

type ProfileMode = 'LIGHT' | 'HEAVY' | 'AUTO'
type LogLine = { ts: number; text: string }

type Candle = {
  time: number; open: number; high: number; low: number; close: number; volume?: number
}

type Overlay = {
  data?: Array<number | null>
  color?: string
  dashed?: boolean
  fillBetween?: { a: Array<number | null>; b: Array<number | null> }
  fillColor?: string
}

export default function Dashboard() {
  const [data, setData] = useState<any>({
    history: [],
    candles: [],
    profileMode: 'AUTO',
    profileModeActive: 'LIGHT',
    strategy: 'Adaptive Router',
    autoTrade: true,
    scalpMode: true,
  })
  const [dir, setDir] = useState<'up' | 'down' | null>(null)
  const lastShown = useRef<number | undefined>(undefined)

  // Logs (feed at bottom)
  const [logs, setLogs] = useState<LogLine[]>([])
  const [loadingLogs, setLoadingLogs] = useState(true)
  const logBoxRef = useRef<HTMLDivElement | null>(null)

  // Chart
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [barCount, setBarCount] = useState<number>(150)

  // Poll /status
  useEffect(() => {
    let alive = true
    const tick = async () => {
      const s = await getStatus()
      if (!alive) return
      setData(s)
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
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // Poll logs every 2s
  useEffect(() => {
    let alive = true
    const fetchLogs = async () => {
      try {
        const r = await getLogs(300)
        if (!alive) return
        setLogs(r.logs || [])
      } catch (e) {
        console.error(e)
      } finally {
        if (alive) setLoadingLogs(false)
      }
    }
    fetchLogs()
    const id = setInterval(fetchLogs, 2000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // Auto-scroll logs
  useEffect(() => {
    const el = logBoxRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [logs])

  const fmt = (n: any, d = 2) =>
    n == null || isNaN(n) ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d })

  // ---------- VWAP + Chart helpers ----------
  function buildSessionVWAPArray(cs: Candle[]): Array<number | null> {
    const out: Array<number | null> = []
    let day: string | null = null, pv = 0, vv = 0
    for (let i = 0; i < cs.length; i++) {
      const c = cs[i]
      const d = new Date(c.time * 1000).toISOString().slice(0, 10)
      if (day !== d) { day = d; pv = 0; vv = 0 }
      const tp = (c.high + c.low + c.close) / 3
      const v = Math.max(1e-8, c.volume ?? 0)
      pv += tp * v
      vv += v
      out.push(pv / Math.max(1e-8, vv))
    }
    return out
  }

  const vwap = useMemo(() => buildSessionVWAPArray((data.candles || []) as Candle[]), [data.candles])

  function drawChart(
    canvas: HTMLCanvasElement,
    candles: Candle[],
    overlays: Overlay[] | undefined,
    highlight: 'BUY' | 'SELL' | null,
    limit = 300
  ) {
    const ctx = canvas.getContext('2d')
    if (!ctx || !candles || candles.length === 0) return

    const W = canvas.clientWidth
    const H = canvas.clientHeight
    const dpr = Math.max(1, window.devicePixelRatio || 1)
    if (canvas.width !== Math.floor(W * dpr)) canvas.width = Math.floor(W * dpr)
    if (canvas.height !== Math.floor(H * dpr)) canvas.height = Math.floor(H * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, W, H)

    const n = candles.length
    const padL = 40, padR = 10, padT = 10, padB = 20
    const chartW = W - padL - padR
    const chartH = H - padT - padB
    const start = Math.max(0, n - limit)

    let lo = Infinity, hi = -Infinity
    for (let i = start; i < n; i++) {
      lo = Math.min(lo, candles[i].low)
      hi = Math.max(hi, candles[i].high)
    }
    overlays?.forEach(o => {
      const sets: Array<Array<number | null> | undefined> = [o.data, o.fillBetween?.a, o.fillBetween?.b]
      sets.forEach(s => {
        if (!s) return
        for (let i = start; i < Math.min(n, s.length); i++) {
          const v = s[i]
          if (v == null) continue
          lo = Math.min(lo, v)
          hi = Math.max(hi, v)
        }
      })
    })
    const xPer = chartW / Math.max(1, Math.min(limit, n - start))
    const y = (p: number) => padT + (hi - p) * (chartH / Math.max(1e-8, (hi - lo)))

    // grid
    ctx.strokeStyle = 'rgba(255,255,255,0.06)'
    ctx.lineWidth = 1
    ctx.beginPath()
    for (let i = 0; i <= 5; i++) {
      const yy = padT + (chartH * i) / 5
      ctx.moveTo(padL, yy)
      ctx.lineTo(W - padR, yy)
    }
    ctx.stroke()

    // candles
    for (let i = start; i < n; i++) {
      const c = candles[i]
      const idx = i - start
      const x = padL + idx * xPer + xPer * 0.1
      const cw = xPer * 0.8
      const up = c.close >= c.open
      ctx.strokeStyle = up ? '#22d3ee' : '#fb7185'
      ctx.fillStyle = up ? '#22d3ee' : '#fb7185'
      // wick
      ctx.beginPath()
      ctx.moveTo(x + cw / 2, y(c.high))
      ctx.lineTo(x + cw / 2, y(c.low))
      ctx.stroke()
      // body
      const bh = Math.max(1, Math.abs(y(c.open) - y(c.close)))
      ctx.fillRect(x, Math.min(y(c.open), y(c.close)), cw, bh)
    }

    // overlays
    overlays?.forEach(o => {
      if (!o.data) return
      ctx.strokeStyle = o.color || '#fff'
      ctx.lineWidth = 1.6
      ctx.setLineDash(o.dashed ? [4, 3] : [])
      ctx.beginPath()
      let started = false
      let j = 0
      for (let i = start; i < n && i < o.data.length; i++, j++) {
        const v = o.data[i]
        if (v == null) continue
        const xx = padL + j * xPer + xPer / 2
        const yy = y(v)
        if (!started) { ctx.moveTo(xx, yy); started = true } else { ctx.lineTo(xx, yy) }
      }
      ctx.stroke()
      ctx.setLineDash([])
    })

    // highlight border if pos open
    if (highlight === 'BUY' || highlight === 'SELL') {
      ctx.strokeStyle = highlight === 'BUY' ? '#16a34a' : '#f43f5e'
      ctx.lineWidth = 3
      ctx.strokeRect(2, 2, W - 4, H - 4)
    }

    // right‑axis labels
    ctx.fillStyle = '#9fb2c8'
    ctx.font = '10px ui-sans-serif, system-ui'
    ctx.textAlign = 'right'
    for (let i = 0; i <= 5; i++) {
      const p = lo + (i * (hi - lo)) / 5
      const yy = padT + (chartH * (5 - i)) / 5
      ctx.fillText(fmt(p, 2), W - 4, yy + 3)
    }
  }

  // draw on every candles/pos change
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const overlays: Overlay[] = []
    // VWAP on 1m (matches old look)
    overlays.push({ data: vwap, color: '#60a5fa', dashed: true })
    const pos = data?.pos
    const hl = pos ? (pos.side === 'long' ? 'BUY' : 'SELL') : null
    drawChart(canvas, (data.candles || []) as Candle[], overlays, hl, barCount)
  }, [data.candles, data.pos, vwap, barCount])

  // ---------- UI actions ----------
  async function toggleScalp() {
    const next = !data.scalpMode
    await postSettings({ scalpMode: next })
    setData((d: any) => ({ ...d, scalpMode: next }))
  }
  async function toggleAuto() {
    const next = !data.autoTrade
    await postSettings({ autoTrade: next })
    setData((d: any) => ({ ...d, autoTrade: next }))
  }
  async function setStrategyLabel(label: string) {
    await postSettings({ strategy: label })
    setData((d: any) => ({ ...d, strategy: label }))
  }

  const px = data?.price ?? null
  const headerClass = dir === 'up' ? 'price-up' : dir === 'down' ? 'price-down' : ''
  const netLive = data?.price != null
  const fills = data?.fillsToday ?? 0
  const pnlToday = data?.pnlToday ?? 0

  // Friendly CONDITIONS line close to the old UI
  const conditionsText = [
    data.activeStrategy ?? data.strategy ?? '—',
    data.regime ? `Regime: ${data.regime}` : null,
    data.bias ? `Bias: ${data.bias}` : null,
    data.adx != null ? `ADX: ${fmt(data.adx, 0)}` : null,
    data.atrPct != null ? `ATR%: ${fmt((data.atrPct || 0) * 100, 2)}%` : null,
  ].filter(Boolean).join(' • ')

  return (
    <div>
      {/* HEADER */}
      <div className="glass header-card">
        <div>
          <div className="pair-sub">BTC/USD</div>
          <div className="pair-main">
            <span className={headerClass}>${fmt(px, 2)}</span>
            <span className="pair-exch">(COINBASE)</span>
          </div>
        </div>
        <div className="header-chips">
          <span className={`chip ${data.scalpMode ? 'green' : ''}`}>{data.scalpMode ? '1m' : '1h'}</span>
          <span className={`chip ${netLive ? 'green' : 'red'}`}>{netLive ? 'NET: LIVE' : 'NET: STALE'}</span>
        </div>
      </div>

      {/* STATUS + CONDITIONS */}
      <div className="status-row">
        <div className={`glass status-pill ${data.pos ? (data.pos.side === 'long' ? 'status-green' : 'status-red') : ''}`}>
          <b>STATUS:</b>&nbsp;&nbsp;{data.status ?? '—'}
        </div>
        <div className="glass status-pill">
          <b>CONDITIONS:</b>&nbsp;&nbsp;{conditionsText || '—'}
          <span className="cond-meta"> (P&amp;L today: {fmt(pnlToday, 2)} / cap ±$500; fills {fills}/60)</span>
        </div>
      </div>

      {/* SIGNAL banner intentionally omitted (engine doesn’t emit it). */}

      {/* MAIN GRID */}
      <div className="main-grid">
        {/* Left: Chart + History */}
        <div className="left-col">
          <div className="glass chart-wrap">
            <canvas ref={canvasRef} className="chart" />
          </div>

          <section className="glass history-card">
            <h2 className="card-title">Order History</h2>
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
                    <td style={{ textTransform: 'capitalize' }}>{t.side}</td>
                    <td style={{ textAlign: 'right' }}>{fmt(t.entry, 2)}</td>
                    <td style={{ textAlign: 'right' }}>{fmt(t.close, 2)}</td>
                    <td style={{ textAlign: 'right' }} className={(t.pnl ?? 0) >= 0 ? 'price-up' : 'price-down'}>
                      {(t.pnl ?? 0) >= 0 ? '+' : ''}{fmt(t.pnl, 2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>

        {/* Right: Controls + Account */}
        <div className="right-col">
          <section className="glass pcard">
            <h2 className="card-title">Toggles</h2>
            <button className={`btn-pill ${data.scalpMode ? 'on' : ''}`} onClick={toggleScalp}>
              Mode: {data.scalpMode ? 'Scalper (1m)' : 'High‑Hit (1h)'}
            </button>
            <button className={`btn-pill ${data.autoTrade ? 'on' : ''}`} onClick={toggleAuto}>
              Auto Trading is {data.autoTrade ? 'ON' : 'OFF'}
            </button>
          </section>

          <section className="glass pcard">
            <h2 className="card-title">Strategy</h2>
            <select
              className="select"
              value={data.strategy ?? 'Adaptive Router'}
              onChange={e => setStrategyLabel(e.target.value)}
            >
              <option>Adaptive Router</option>
              <option>Level King — Profiled</option>
              <option>Mean Reversion (H1)</option>
              <option>Breakout</option>
              <option>Trend‑Following</option>
            </select>
            <div className="muted">
              Router decides dynamically • UI label only
            </div>
          </section>

          <section className="glass pcard">
            <h2 className="card-title">Paper Account</h2>
            <div className="acct-grid">
              <div className="glass mini">
                <div className="muted-xs">Equity</div>
                <div className="num-lg">${fmt(data.equity, 2)}</div>
              </div>
              <div className="glass mini">
                <div className="muted-xs">Unrealized (net)</div>
                <div className={`num-lg ${(data.unrealNet ?? 0) >= 0 ? 'price-up' : 'price-down'}`}>
                  {(data.unrealNet ?? 0) >= 0 ? '+' : ''}{fmt(data.unrealNet, 2)}
                </div>
              </div>
            </div>
            {data.pos ? (
              <div className="pos-grid">
                <div><span>Side</span><b className="cap">{data.pos.side}</b></div>
                <div><span>TF</span><b>{data.pos.tf}</b></div>
                <div><span>Qty</span><b>{fmt(data.pos.qty, 6)}</b></div>
                <div><span>Entry</span><b>${fmt(data.pos.entry, 2)}</b></div>
                <div><span>Stop</span><b>${fmt(data.pos.stop, 2)}</b></div>
                <div><span>Take</span><b>${fmt(data.pos.take, 2)}</b></div>
              </div>
            ) : <div className="muted-xs" style={{ marginTop: 8 }}>No open position.</div>}
          </section>
        </div>
      </div>

      {/* Logs */}
      <div className="glass" style={{ padding: 12, marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Bot Status Feed</div>
        {loadingLogs && logs.length === 0 ? (
          <p className="muted">Loading logs…</p>
        ) : logs.length === 0 ? (
          <p className="muted">No logs yet. Engine events will show here.</p>
        ) : (
          <div
            ref={logBoxRef}
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
    </div>
  )
}
