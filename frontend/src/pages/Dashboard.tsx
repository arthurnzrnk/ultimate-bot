import React, { useEffect, useRef, useState } from 'react'
import { getStatus, postSettings, getLogs } from '../api'

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
    autoTrade: false,
  })
  const [dir, setDir] = useState<'up' | 'down' | null>(null)
  const lastShown = useRef<number | undefined>(undefined)

  // Logs
  const [logs, setLogs] = useState<LogLine[]>([])
  const [loadingLogs, setLoadingLogs] = useState(true)
  const logBoxRef = useRef<HTMLDivElement | null>(null)

  // Chart
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [barCount] = useState<number>(150)

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
    const dpr = Math.max(1, (window.devicePixelRatio || 1))
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

    if (!isFinite(lo) || !isFinite(hi) || hi <= lo) return

    const xPer = chartW / Math.max(1, Math.min(limit, n - start))
    const y = (p: number) => padT + (hi - p) * (chartH / Math.max(1e-8, (hi - lo)))

    // grid
    const grid = 'rgba(255,255,255,0.06)'
    ctx.strokeStyle = grid
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
      const n2 = o.data.length
      ctx.strokeStyle = o.color || '#fff'
      ctx.lineWidth = 1.6
      ctx.setLineDash(o.dashed ? [4, 3] : [])
      ctx.beginPath()
      let started = false
      let j = 0
      for (let i = start; i < n && i < n2; i++, j++) {
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

    const candles = (data.candles || []) as Candle[]

    if (!Array.isArray(candles) || candles.length < 5) {
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      const W = canvas.clientWidth, H = canvas.clientHeight
      ctx.clearRect(0, 0, W, H)
      ctx.fillStyle = 'rgba(255,255,255,0.6)'
      ctx.font = '12px ui-sans-serif, system-ui'
      ctx.textAlign = 'center'
      ctx.fillText('Loading candles…', W / 2, H / 2)
      return
    }

    const overlays: Overlay[] = []
    const vwap = buildSessionVWAPArray(candles)
    overlays.push({ data: vwap, color: '#60a5fa', dashed: true })
    const pos = data?.pos
    const hl = pos ? (pos.side === 'long' ? 'BUY' : 'SELL') : null
    drawChart(canvas, candles, overlays, hl, barCount)
  }, [data.candles, data.pos, barCount])

  // UI actions
  async function toggleAuto() {
    const next = !data.autoTrade
    await postSettings({ autoTrade: next })
    setData((d: any) => ({ ...d, autoTrade: next }))
  }

  const px = data?.price ?? null
  const headerClass = dir === 'up' ? 'price-up' : dir === 'down' ? 'price-down' : ''
  const netLive = data?.price != null
  const fills = data?.fillsToday ?? 0
  const pnlToday = data?.pnlToday ?? 0

  const atrLine = (() => {
    const a = data.atrPct
    if (a == null) return null
    return `ATR%: ${fmt((a || 0) * 100, 2)}%`
  })()

  // CONDITIONS: market only (no bot internals / no strategy name)
  const conditionsText = [
    data.regime ? `Regime: ${data.regime}` : null,
    data.bias ? `Bias: ${data.bias}` : null,
    data.adx != null ? `ADX: ${fmt(data.adx, 0)}` : null,
    atrLine,
  ].filter(Boolean).join(' • ')

  const glowTone = !data.autoTrade ? 'gray' : data.pos ? (data.pos.side === 'long' ? 'green' : 'red') : 'orange'

  // Safe accessors
  const pos = data?.pos
  const meta = (pos?.meta || {}) as any

  return (
    <div>
      <div className={`glow-dynamic tone-${glowTone}`} />

      <div className="glass header-card">
        <div>
          <div className="pair-sub">BTC/USD</div>
          <div className="pair-main">
            <span className={headerClass}>${fmt(px, 2)}</span>
            <span className="pair-exch">(COINBASE)</span>
          </div>
        </div>
        <div className="header-chips">
          <span className={`chip ${netLive ? 'green' : 'red'}`}>{netLive ? 'NET: LIVE' : 'NET: STALE'}</span>
        </div>
      </div>

      <div className="status-row">
        <div className={`glass status-pill ${data.pos ? (data.pos.side === 'long' ? 'status-green' : 'status-red') : ''}`}>
          <b>STATUS:</b>&nbsp;&nbsp;{data.status ?? '—'}
        </div>
        <div className="glass status-pill">
          <b>CONDITIONS:</b>&nbsp;&nbsp;{conditionsText || '—'}
          <span className="cond-meta"> (P&amp;L today: {fmt(pnlToday, 2)}; fills {fills})</span>
        </div>
      </div>

      <div className="main-grid">
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

        <div className="right-col">
          <section className="glass pcard">
            <h2 className="card-title">Trading</h2>
            <button className={`btn-pill ${data.autoTrade ? 'on' : ''}`} onClick={toggleAuto}>
              Auto Trading is {data.autoTrade ? 'ON' : 'OFF'}
            </button>
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

            {pos ? (
              <div className="pos-grid">
                <div><span>Side</span><b className="cap">{pos.side}</b></div>
                <div><span>TF</span><b className="cap">{pos.tf}</b></div>
                <div><span>Qty</span><b>{fmt(pos.qty, 6)}</b></div>
                <div><span>Entry</span><b>${fmt(pos.entry, 2)}</b></div>
                <div><span>Stop</span><b>${fmt(pos.stop, 2)}</b></div>
                <div><span>Take</span><b>${fmt(pos.take, 2)}</b></div>
                <div><span>1R ($)</span><b>{fmt(pos.stop_dist, 2)}</b></div>
                <div><span>BE</span><b className="cap">{pos.be ? 'yes' : 'no'}</b></div>
                <div><span>Opened</span><b>{new Date((pos.open_time || 0) * 1000).toLocaleTimeString()}</b></div>
              </div>
            ) : (
              <div className="muted-xs">No open position.</div>
            )}

            {/* Execution / Telemetry quick‑view */}
            <div className="meta-grid">
              <div><span>Spread (bps)</span><b>{fmt(data.spreadBps, 2)}</b></div>
              <div><span>Slip Est ($)</span><b>{fmt(data.slipEst, 2)}</b></div>
              <div><span>Fee/TP</span><b>{fmt(data.feeToTp, 3)}</b></div>
              <div><span>VS</span><b>{fmt(data.vs, 2)}</b></div>
              <div><span>PS</span><b>{fmt(data.ps, 2)}</b></div>
              <div><span>Taker Fails (30m)</span><b>{fmt(data.takerFailCount30m, 0)}</b></div>
            </div>

            {pos && meta ? (
              <details className="telemetry">
                <summary>Trade Telemetry</summary>
                <div className="telemetry-kv">
                  <div><span>Post‑only</span><b>{String(meta.post_only ?? '')}</b></div>
                  <div><span>Fast‑tape</span><b>{fmt(meta.fast_tape_taker, 0)}</b></div>
                  <div><span>TP price</span><b>${fmt(meta.tp_price, 2)}</b></div>
                  <div><span>Final TP%</span><b>{meta.final_tp_pct != null ? fmt((meta.final_tp_pct || 0) * 100, 3) + '%' : '—'}</b></div>
                  <div><span>R (stop $)</span><b>{fmt(meta.final_stop_dist_R, 2)}</b></div>
                  <div><span>A+ Gate</span><b>{fmt(meta.a_plus_gate_on, 0)}</b></div>
                </div>
              </details>
            ) : null}
          </section>

          <section className="glass pcard">
            <h2 className="card-title">Logs</h2>
            <div className="logbox" ref={logBoxRef}>
              {loadingLogs ? (
                <div className="muted-xs">Loading logs…</div>
              ) : (
                logs.map((l, i) => (
                  <div className="log-line" key={`${l.ts}-${i}`}>
                    <span className="log-ts">{new Date(l.ts * 1000).toLocaleTimeString()}</span>
                    <span className="log-text">{l.text}</span>
                  </div>
                ))
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
