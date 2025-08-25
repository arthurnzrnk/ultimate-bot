import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { getStatus, postSettings, getLogs } from '../api'

type LogLine = { ts: number; text: string }
type Candle = { time: number; open: number; high: number; low: number; close: number; volume?: number }
type Overlay = {
  data?: Array<number | null>
  color?: string
  dashed?: boolean
  fillBetween?: { a: Array<number | null>; b: Array<number | null> }
  fillColor?: string
}

export default function Dashboard() {
  const [data, setData] = useState<any>({ history: [], candles: [], autoTrade: false })
  const [logs, setLogs] = useState<LogLine[]>([])
  const [loadingLogs, setLoadingLogs] = useState(true)

  // Price direction (for nothing here; header handles its own)
  const lastShown = useRef<number | null>(null)

  // Chart
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [barCount] = useState<number>(110) // tighter window → bigger candles

  // --- Audio (trade beeps) ---------------------------------------------------
  const audioCtxRef = useRef<AudioContext | null>(null)
  useEffect(() => {
    const unlock = () => {
      if (!audioCtxRef.current) {
        try { audioCtxRef.current = new (window.AudioContext || (window as any).webkitAudioContext)() } catch {}
      }
      window.removeEventListener('pointerdown', unlock)
    }
    window.addEventListener('pointerdown', unlock)
    return () => window.removeEventListener('pointerdown', unlock)
  }, [])
  function tone(f = 660, ms = 120, g = 0.03) {
    const ctx = audioCtxRef.current
    if (!ctx) return
    const o = ctx.createOscillator()
    const gain = ctx.createGain()
    o.frequency.value = f
    o.type = 'sine'
    gain.gain.value = g
    o.connect(gain).connect(ctx.destination)
    o.start()
    setTimeout(() => { o.stop(); o.disconnect(); gain.disconnect() }, ms)
  }
  function cue(type: 'open-long' | 'open-short' | 'close') {
    if (type === 'open-long') tone(880, 120, 0.04)
    else if (type === 'open-short') tone(420, 140, 0.04)
    else tone(660, 140, 0.035)
  }

  // Poll /status for dashboard data
  useEffect(() => {
    let alive = true
    const tick = async () => {
      const s = await getStatus()
      if (!alive) return
      setData(s)
      const shown = s.price ?? (s.bid && s.ask ? (s.bid + s.ask) / 2 : null)
      if (typeof shown === 'number') {
        const prev = lastShown.current
        lastShown.current = shown
        // (no local UI on direction here; header shows color)
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // Logs: fetch and play sound on new important lines
  const lastLogCountRef = useRef<number>(0)
  useEffect(() => {
    let alive = true
    async function fetchLogs() {
      try {
        const r = await getLogs(300)
        if (!alive) return
        const arr: LogLine[] = r.logs || []
        // sound on new opens/closes
        if (arr.length > lastLogCountRef.current) {
          const fresh = arr.slice(lastLogCountRef.current)
          fresh.forEach(l => {
            const t = (l.text || '').toUpperCase()
            if (t.startsWith('OPEN BUY')) cue('open-long')
            else if (t.startsWith('OPEN SELL')) cue('open-short')
            else if (t.startsWith('CLOSE')) cue('close')
          })
          lastLogCountRef.current = arr.length
        }
        setLogs(arr)
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

  const fmt = (n: any, d = 2) =>
    n == null || isNaN(n) ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d })

  // ---------- TA helpers (match strategy family) ----------
  function buildSessionVWAPArray(cs: Candle[]): Array<number | null> {
    const out: Array<number | null> = []
    let day: string | null = null, pv = 0, vv = 0
    cs.forEach(c => {
      const d = new Date(c.time * 1000).toISOString().slice(0, 10)
      if (day !== d) { day = d; pv = 0; vv = 0 }
      const tp = (c.high + c.low + c.close) / 3
      const v = Math.max(1e-8, c.volume ?? 0)
      pv += tp * v; vv += v
      out.push(pv / Math.max(1e-8, vv))
    })
    return out
  }
  function ema(series: number[], period: number) {
    if (!series.length) return []
    const k = 2 / (period + 1)
    const out: (number | null)[] = new Array(series.length).fill(null)
    let e = series[0]
    out[0] = e
    for (let i = 1; i < series.length; i++) {
      e = series[i] * k + e * (1 - k)
      out[i] = e
    }
    return out
  }
  function donchian(c: Candle[], period: number) {
    const hi: (number | null)[] = new Array(c.length).fill(null)
    const lo: (number | null)[] = new Array(c.length).fill(null)
    for (let i = 0; i < c.length; i++) {
      const s = Math.max(0, i - period + 1)
      let H = -Infinity, L = Infinity
      for (let j = s; j <= i; j++) { H = Math.max(H, c[j].high); L = Math.min(L, c[j].low) }
      hi[i] = H; lo[i] = L
    }
    return { hi, lo }
  }

  function drawChart(
    canvas: HTMLCanvasElement,
    candles: Candle[],
    overlays: Overlay[] | undefined,
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
    for (let i = start; i < n; i++) { lo = Math.min(lo, candles[i].low); hi = Math.max(hi, candles[i].high) }
    overlays?.forEach(o => {
      const sets: Array<Array<number | null> | undefined> = [o.data, o.fillBetween?.a, o.fillBetween?.b]
      sets.forEach(s => {
        if (!s) return
        for (let i = start; i < Math.min(n, s.length); i++) {
          const v = s[i]; if (v == null) continue
          lo = Math.min(lo, v); hi = Math.max(hi, v)
        }
      })
    })
    if (!isFinite(lo) || !isFinite(hi) || hi <= lo) return

    const xPer = chartW / Math.max(1, Math.min(limit, n - start))
    const y = (p: number) => padT + (hi - p) * (chartH / Math.max(1e-8, (hi - lo)))

    // grid
    const grid = 'rgba(8,15,35,0.08)'
    ctx.strokeStyle = grid
    ctx.lineWidth = 1
    ctx.beginPath()
    for (let i = 0; i <= 5; i++) {
      const yy = padT + (chartH * i) / 5
      ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy)
    }
    ctx.stroke()

    // candles (green up, red down; bigger bodies)
    for (let i = start; i < n; i++) {
      const c = candles[i]
      const idx = i - start
      const x = padL + idx * xPer + xPer * 0.05
      const cw = xPer * 0.90
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
    overlays?.forEach(o => {
      if (!o.data) return
      const n2 = o.data.length
      ctx.strokeStyle = o.color || '#1f2937'
      ctx.lineWidth = 1.6
      ctx.setLineDash(o.dashed ? [4, 3] : [])
      ctx.beginPath()
      let started = false
      let j = 0
      for (let i = start; i < n && i < n2; i++, j++) {
        const v = o.data[i]; if (v == null) continue
        const xx = padL + j * xPer + xPer / 2
        const yy = y(v)
        if (!started) { ctx.moveTo(xx, yy); started = true } else { ctx.lineTo(xx, yy) }
      }
      ctx.stroke()
      ctx.setLineDash([])
    })

    // right axis labels
    ctx.fillStyle = '#5b6b7e'
    ctx.font = '10px ui-sans-serif, system-ui'
    ctx.textAlign = 'right'
    for (let i = 0; i <= 5; i++) {
      const p = lo + (i * (hi - lo)) / 5
      const yy = padT + (chartH * (5 - i)) / 5
      ctx.fillText(Number(p).toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 }), W - 4, yy + 3)
    }
  }

  // draw on data changes
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const candles: Candle[] = (data.candles || []) as Candle[]
    if (!Array.isArray(candles) || candles.length < 5) {
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      const W = canvas.clientWidth, H = canvas.clientHeight
      ctx.clearRect(0, 0, W, H)
      ctx.fillStyle = 'rgba(15,23,42,0.6)'
      ctx.font = '12px ui-sans-serif, system-ui'
      ctx.textAlign = 'center'
      ctx.fillText('Loading candles…', W / 2, H / 2)
      return
    }

    const overlays: Overlay[] = []
    if (data.autoTrade) {
      const vwap = buildSessionVWAPArray(candles)
      overlays.push({ data: vwap, color: '#0ea5e9', dashed: true }) // VWAP
      // EMA‑10 of Typical (strategy uses this slope gate)
      const typical = candles.map(c => (c.high + c.low + c.close) / 3)
      const e10 = ema(typical, 10)
      overlays.push({ data: e10 as (number | null)[], color: '#1d4ed8' })
      // Donchian 20 hi/lo (strategy family)
      const dc = donchian(candles, 20)
      overlays.push({ data: dc.hi, color: '#475569', dashed: true })
      overlays.push({ data: dc.lo, color: '#475569', dashed: true })
    }
    drawChart(canvas, candles, overlays, barCount)
  }, [data.candles, data.autoTrade, barCount])

  // UI helpers
  const pnlToday = data?.pnlToday ?? 0
  const fills = data?.fillsToday ?? 0
  const atrLine = useMemo(() => (data.atrPct == null ? null : `ATR%: ${fmt((data.atrPct || 0) * 100, 2)}%`), [data.atrPct])

  // CONDITIONS: market only
  const conditionsText = [
    data.regime ? `Regime: ${data.regime}` : null,
    data.bias ? `Bias: ${data.bias}` : null,
    data.adx != null ? `ADX: ${fmt(data.adx, 0)}` : null,
    atrLine,
  ].filter(Boolean).join(' • ')

  const glowTone = !data.autoTrade ? 'gray' : data.pos ? (data.pos.side === 'long' ? 'green' : 'red') : 'orange'

  async function toggleAuto() { // keep for keyboard / alt UI if needed
    const next = !data.autoTrade
    await postSettings({ autoTrade: next })
    setData((d: any) => ({ ...d, autoTrade: next }))
  }

  return (
    <div>
      <div className={`glow-dynamic tone-${glowTone}`} />

      {/* Status + Conditions */}
      <div className="status-row">
        <div className={`glass status-pill`}>
          <b>STATUS:</b>&nbsp;&nbsp;{data.status ?? '—'}
        </div>
        <div className="glass status-pill">
          <b>CONDITIONS:</b>&nbsp;&nbsp;{conditionsText || '—'}
        </div>
      </div>

      {/* Chart */}
      <div className="glass chart-wrap locked-center">
        <canvas ref={canvasRef} className="chart" />
      </div>

      {/* Two equal panels under candles */}
      <div className="two-col">
        <section className="glass block">
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
                  <td style={{ textAlign: 'right', color: (t.pnl ?? 0) >= 0 ? '#16a34a' : '#f43f5e' }}>
                    {(t.pnl ?? 0) >= 0 ? '+' : ''}{fmt(t.pnl, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="glass block">
          <div className="card-head">
            <h2 className="card-title">Account</h2>
            <Link to="/apikeys" className="chip link">API</Link>
          </div>
          <div className="acct-grid">
            <div className="glass mini">
              <div className="muted-xs">Equity</div>
              <div className="num-lg">${fmt(data.equity, 2)}</div>
            </div>
            <div className="glass mini">
              <div className="muted-xs">P&amp;L Today</div>
              <div className="num-lg" style={{ color: pnlToday >= 0 ? '#16a34a' : '#f43f5e' }}>
                {pnlToday >= 0 ? '+' : ''}{fmt(pnlToday, 2)}
              </div>
            </div>
            <div className="glass mini">
              <div className="muted-xs">Fills Today</div>
              <div className="num-lg">{fills}</div>
            </div>
            <div className="glass mini">
              <div className="muted-xs">Day Lock</div>
              <div className="num-lg">{data.dayLockArmed ? `Armed ≥ ${fmt(data.dayLockFloorPct, 2)}%` : '—'}</div>
            </div>
          </div>
        </section>
      </div>

      {/* Bottom logs (no panel) */}
      <div className="logsbar">
        {loadingLogs
          ? <span className="muted">Loading logs…</span>
          : logs.map((l, i) => (
              <span key={i} className="logchip">
                <span className="logts">{new Date(l.ts * 1000).toLocaleTimeString()}</span>
                <span className="logtxt">{l.text}</span>
              </span>
            ))
        }
      </div>
    </div>
  )
}
