import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { getStatus, postSettings, getLogs } from '../api'

type LogLine = { ts: number; text: string }
type Candle = { time: number; open: number; high: number; low: number; close: number; volume?: number }

type Overlay = {
  data?: Array<number | null>
  color?: string
  dashed?: boolean
}

export default function Dashboard() {
  const [s, setS] = useState<any>({ candles: [], autoTrade: false, history: [] })
  const [dir, setDir] = useState<'up' | 'down' | null>(null)
  const lastShown = useRef<number | undefined>(undefined)

  // logs (thin bar at bottom)
  const [logs, setLogs] = useState<LogLine[]>([])
  const [loadingLogs, setLoadingLogs] = useState(true)

  // chart
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  // shorter visible window so candles draw bigger
  const VISIBLE_BARS = 120

  // sound
  const audioRef = useRef<AudioContext | null>(null)
  const prevPosRef = useRef<any>(null)
  const lastLogTsRef = useRef<number>(0)

  // boot audio context on first user interaction
  useEffect(() => {
    const priming = () => {
      if (!audioRef.current) audioRef.current = new (window.AudioContext || (window as any).webkitAudioContext)()
      window.removeEventListener('pointerdown', priming)
      window.removeEventListener('keydown', priming)
    }
    window.addEventListener('pointerdown', priming)
    window.addEventListener('keydown', priming)
    return () => {
      window.removeEventListener('pointerdown', priming)
      window.removeEventListener('keydown', priming)
    }
  }, [])

  function blip(kind: 'open-long' | 'open-short' | 'take' | 'stop') {
    const ctx = audioRef.current
    if (!ctx) return
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.connect(gain); gain.connect(ctx.destination)
    const t = ctx.currentTime
    const dur = kind === 'stop' ? 0.28 : kind === 'take' ? 0.25 : 0.18
    const f0 = kind === 'open-long' ? 720 : kind === 'open-short' ? 460 : kind === 'take' ? 880 : 240
    const f1 = kind === 'open-long' ? 880 : kind === 'open-short' ? 320 : kind === 'take' ? 1320 : 120
    osc.frequency.setValueAtTime(f0, t)
    osc.frequency.exponentialRampToValueAtTime(f1, t + dur * 0.9)
    gain.gain.setValueAtTime(0.0001, t)
    gain.gain.exponentialRampToValueAtTime(kind === 'stop' ? 0.3 : 0.18, t + 0.02)
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur)
    osc.type = kind === 'stop' ? 'sawtooth' : 'triangle'
    osc.start(t); osc.stop(t + dur + 0.01)
  }

  // watch status for open/close → sounds + gradient tone
  useEffect(() => {
    const prev = prevPosRef.current
    const cur = s?.pos || null
    if (prev === null && cur) {
      blip(cur.side === 'long' ? 'open-long' : 'open-short')
    } else if (prev && cur === null) {
      // try to classify by last log
      const recent = logs[logs.length - 1]?.text || ''
      if (recent.includes('Close TAKE')) blip('take')
      else if (recent.includes('Close STOP')) blip('stop')
      else blip('stop')
    }
    prevPosRef.current = cur
  }, [s?.pos, logs])

  // poll /status (1s)
  useEffect(() => {
    let alive = true
    const tick = async () => {
      const ns = await getStatus()
      if (!alive) return
      setS(ns)
      const shown = ns.price ?? (ns.bid && ns.ask ? (ns.bid + ns.ask) / 2 : null)
      if (typeof shown === 'number') {
        const prev = lastShown.current
        setDir(prev == null ? null :
          Math.round(shown * 100) > Math.round((prev || 0) * 100) ? 'up' :
          Math.round(shown * 100) < Math.round((prev || 0) * 100) ? 'down' : null)
        lastShown.current = shown
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // poll logs (2s)
  useEffect(() => {
    let alive = true
    const fetchLogs = async () => {
      try {
        const r = await getLogs(250)
        if (!alive) return
        setLogs(r.logs || [])
        const lastTs = r.logs?.[r.logs.length - 1]?.ts || 0
        lastLogTsRef.current = lastTs
      } catch (e) {
        // ignore
      } finally {
        if (alive) setLoadingLogs(false)
      }
    }
    fetchLogs()
    const id = setInterval(fetchLogs, 2000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // helpers
  const fmt = (n: any, d = 2) => (n == null || isNaN(n)) ? '—'
    : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d })

  // ---------- TA builders (client-side, lightweight) ----------
  const closes: number[] = useMemo(() => (s?.candles || []).map((c: Candle) => c.close), [s?.candles])
  function ema(src: number[], len: number): Array<number | null> {
    if (!src.length) return []
    const k = 2 / (len + 1); const out: Array<number | null> = []
    let e = src[0]; out.push(e)
    for (let i = 1; i < src.length; i++) { e = src[i] * k + e * (1 - k); out.push(e) }
    return out
  }
  function sessionVWAP(cs: Candle[]): Array<number | null> {
    const out: Array<number | null> = []; let day: string | null = null, pv = 0, vv = 0
    for (let i = 0; i < cs.length; i++) {
      const c = cs[i]; const d = new Date(c.time * 1000).toISOString().slice(0,10)
      if (day !== d) { day = d; pv = 0; vv = 0 }
      const tp = (c.high + c.low + c.close) / 3; const v = Math.max(1e-8, c.volume ?? 1)
      pv += tp * v; vv += v; out.push(pv / Math.max(1e-8, vv))
    }
    return out
  }
  function linreg(src: number[], len: number): Array<number | null> {
    const out: Array<number | null> = new Array(src.length).fill(null)
    if (src.length < len) return out
    const xs = Array.from({ length: len }, (_, i) => i)
    const sumX = xs.reduce((a,b)=>a+b,0); const sumXX = xs.reduce((a,b)=>a+b*b,0)
    for (let i = len - 1; i < src.length; i++) {
      const win = src.slice(i - len + 1, i + 1)
      const sumY = win.reduce((a,b)=>a+b,0)
      const sumXY = win.reduce((a,b,idx)=>a+idx*b,0)
      const n = len
      const denom = n * sumXX - sumX * sumX
      if (denom === 0) continue
      const m = (n * sumXY - sumX * sumY) / denom
      const b = (sumY - m * sumX) / n
      const y = m * (len - 1) + b
      out[i] = y
    }
    return out
  }

  // draw chart
  useEffect(() => {
    const canvas = canvasRef.current
    const candles: Candle[] = s?.candles || []
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const WCSS = 1024 // fixed width area for the chart canvas wrapper
    const HCSS = 420
    const dpr = Math.max(1, window.devicePixelRatio || 1)
    if (canvas.width !== WCSS * dpr) canvas.width = WCSS * dpr
    if (canvas.height !== HCSS * dpr) canvas.height = HCSS * dpr
    canvas.style.width = `${WCSS}px`
    canvas.style.height = `${HCSS}px`
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, WCSS, HCSS)

    if (!candles.length) {
      ctx.fillStyle = 'rgba(255,255,255,0.7)'
      ctx.font = '12px ui-sans-serif, system-ui'
      ctx.textAlign = 'center'
      ctx.fillText('Loading…', WCSS/2, HCSS/2)
      return
    }

    const n = candles.length
    const start = Math.max(0, n - VISIBLE_BARS)
    let lo = Infinity, hi = -Infinity
    for (let i = start; i < n; i++) { lo = Math.min(lo, candles[i].low); hi = Math.max(hi, candles[i].high) }

    // overlays (ON only)
    const overlays: Overlay[] = []
    if (s?.autoTrade) {
      const v = sessionVWAP(candles)
      const e20 = ema(closes, 20)
      const e60 = ema(closes, 60)
      const r50 = linreg(closes, 50)
      const r100 = linreg(closes, 100)
      overlays.push({ data: v, color: '#f59e0b', dashed: true })   // VWAP (amber)
      overlays.push({ data: e20, color: '#60a5fa' })               // EMA20 (blue)
      overlays.push({ data: e60, color: '#fde047' })               // EMA60 (yellow)
      overlays.push({ data: r50, color: '#38bdf8' })               // short reg (blue-cyan)
      overlays.push({ data: r100, color: '#facc15' })              // long reg (yellow)
      // extend hi/lo with overlay values
      overlays.forEach(o => (o.data || []).forEach((v,i) => { if (i >= start && v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v) } }))
    }

    if (!isFinite(lo) || !isFinite(hi) || hi <= lo) return

    const padL = 36, padR = 12, padT = 10, padB = 16
    const chartW = WCSS - padL - padR
    const chartH = HCSS - padT - padB
    const xPer = chartW / Math.max(1, n - start)
    const y = (p: number) => padT + (hi - p) * (chartH / Math.max(1e-8, (hi - lo)))

    // subtle grid
    ctx.strokeStyle = 'rgba(255,255,255,0.06)'
    ctx.lineWidth = 1
    ctx.beginPath()
    for (let i = 0; i <= 5; i++) { const yy = padT + (chartH * i) / 5; ctx.moveTo(padL, yy); ctx.lineTo(WCSS - padR, yy) }
    ctx.stroke()

    // candles (thicker, green/red)
    for (let i = start; i < n; i++) {
      const c = candles[i]
      const idx = i - start
      const x = padL + idx * xPer + xPer * 0.05
      const cw = xPer * 0.9
      const up = c.close >= c.open
      ctx.strokeStyle = up ? '#16a34a' : '#f43f5e'
      ctx.fillStyle = up ? '#16a34a' : '#f43f5e'
      // wick
      ctx.beginPath()
      ctx.moveTo(x + cw / 2, y(c.high)); ctx.lineTo(x + cw / 2, y(c.low)); ctx.stroke()
      // body
      const bh = Math.max(2, Math.abs(y(c.open) - y(c.close)))
      ctx.fillRect(x, Math.min(y(c.open), y(c.close)), cw, bh)
    }

    // overlays
    overlays.forEach(o => {
      const data = o.data || []
      ctx.strokeStyle = o.color || '#fff'
      ctx.lineWidth = 1.8
      if (o.dashed) ctx.setLineDash([5, 4])
      ctx.beginPath()
      let started = false
      for (let i = start; i < n && i < data.length; i++) {
        const v = data[i]; if (v == null) continue
        const xx = padL + (i - start) * xPer + xPer / 2
        const yy = y(v)
        if (!started) { ctx.moveTo(xx, yy); started = true } else { ctx.lineTo(xx, yy) }
      }
      ctx.stroke()
      ctx.setLineDash([])
    })
  }, [s?.candles, s?.autoTrade])

  // toggle auto trade from header switch
  async function toggleAuto() {
    const next = !s.autoTrade
    await postSettings({ autoTrade: next })
    setS((d: any) => ({ ...d, autoTrade: next }))
  }

  // dynamic tone class for the background wash
  const tone = !s.autoTrade ? 'gray' : s.pos ? (s.pos.side === 'long' ? 'green' : 'red') : 'orange'
  const px = s?.price ?? null
  const priceClass = dir === 'up' ? 'p-up' : dir === 'down' ? 'p-down' : ''

  // CONDITIONS (market only; no PnL / fills here)
  const conditionsText = [
    s.regime ? `Regime: ${s.regime}` : null,
    s.bias ? `Bias: ${s.bias}` : null,
    s.adx != null ? `ADX: ${fmt(s.adx, 0)}` : null,
    s.atrPct != null ? `ATR%: ${fmt((s.atrPct || 0) * 100, 2)}%` : null,
  ].filter(Boolean).join(' • ')

  return (
    <div className={`dashboard tone-${tone}`}>
      {/* header */}
      <div className="topbar">
        <div className="brand-row">
          <div className="brand">COINSELF</div>
          <button className={`ios-switch ${s.autoTrade ? 'on' : ''}`} onClick={toggleAuto} aria-label="Toggle bot" />
          {s.autoTrade && (
            <div className="autowarn">THE SYSTEM WILL BE TRADING AUTOMATICALLY UNTIL THE BOT IS MANUALLY TURNED OFF.</div>
          )}
        </div>
        <div className="price-right">
          <div className={`price ${priceClass}`}>${fmt(px, 2)}</div>
          <div className="pair">BTC/USD</div>
        </div>
      </div>

      {/* status row */}
      <div className="row status-line">
        <div className={`pill status-pill`}>
          <b>STATUS:</b>&nbsp;&nbsp;{s?.status ?? '—'}
        </div>
        <div className="pill cond-pill">
          <b>CONDITIONS:</b>&nbsp;&nbsp;{conditionsText || '—'}
        </div>
      </div>

      {/* chart */}
      <div className="chart-card glass">
        <canvas ref={canvasRef} />
      </div>

      {/* bottom row: history (left) + account (right) */}
      <div className="row bottom">
        <section className="glass block">
          <h2 className="card-title">Order History</h2>
          <table className="table">
            <thead>
              <tr><th>Time</th><th>Side</th><th style={{textAlign:'right'}}>Entry</th><th style={{textAlign:'right'}}>Exit</th><th style={{textAlign:'right'}}>PNL</th></tr>
            </thead>
            <tbody>
              {[...(s.history || [])].reverse().map((t: any, i: number) => (
                <tr key={i}>
                  <td>{new Date(((t.close_time ?? t.open_time) || 0) * 1000).toLocaleString()}</td>
                  <td style={{ textTransform: 'capitalize' }}>{t.side}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(t.entry, 2)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(t.close, 2)}</td>
                  <td style={{ textAlign: 'right' }} className={(t.pnl ?? 0) >= 0 ? 'p-up' : 'p-down'}>
                    {(t.pnl ?? 0) >= 0 ? '+' : ''}{fmt(t.pnl, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="glass block account">
          <div className="card-head">
            <h2 className="card-title">Account</h2>
            <Link to="/apikeys" className="chip-link">API</Link>
          </div>

          <div className="acct-grid">
            <div className="mini">
              <div className="muted-xs">Paper Account Equity</div>
              <div className="num-lg">${fmt(s.equity, 2)}</div>
            </div>
            <div className="mini">
              <div className="muted-xs">Unrealized (net)</div>
              <div className={`num-lg ${(s.unrealNet ?? 0) >= 0 ? 'p-up' : 'p-down'}`}>
                {(s.unrealNet ?? 0) >= 0 ? '+' : ''}{fmt(s.unrealNet, 2)}
              </div>
            </div>
            <div className="mini">
              <div className="muted-xs">Fills Today</div>
              <div className="num-lg">{s.fillsToday ?? 0}</div>
            </div>
            <div className="mini">
              <div className="muted-xs">P&L Today</div>
              <div className={`num-lg ${(s.pnlToday ?? 0) >= 0 ? 'p-up' : 'p-down'}`}>
                {(s.pnlToday ?? 0) >= 0 ? '+' : ''}{fmt(s.pnlToday, 2)}
              </div>
            </div>
          </div>

          <div className="pos-card">
            <div className="muted-xs" style={{marginBottom:6}}>Open Position</div>
            {!s?.pos ? (
              <div className="muted">No open position.</div>
            ) : (
              <table className="table compact">
                <tbody>
                  <tr><td>Side</td><td style={{textAlign:'right', textTransform:'capitalize'}}>{s.pos.side}</td></tr>
                  <tr><td>Qty</td><td style={{textAlign:'right'}}>{fmt(s.pos.qty, 6)}</td></tr>
                  <tr><td>Entry / Stop / Take</td><td style={{textAlign:'right'}}>{fmt(s.pos.entry,2)} / {fmt(s.pos.stop,2)} / {fmt(s.pos.take,2)}</td></tr>
                  <tr><td>1R ($)</td><td style={{textAlign:'right'}}>{fmt(s.pos.stop_dist,2)}</td></tr>
                </tbody>
              </table>
            )}
          </div>
        </section>
      </div>

      {/* thin gray logs bar (no glass) */}
      <div className="logs-bar">
        {loadingLogs ? 'Loading logs…' :
          (logs.slice(-4).map((l, i) => (
            <span key={i} className="log-item">
              <span className="logts">{new Date(l.ts * 1000).toLocaleTimeString()}</span> {l.text}
            </span>
          )))
        }
      </div>
    </div>
  )
}
