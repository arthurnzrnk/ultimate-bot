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
  const [data, setData] = useState<any>({ history: [], candles: [], autoTrade: false })
  const [logs, setLogs] = useState<LogLine[]>([])
  const [loadingLogs, setLoadingLogs] = useState(true)
  const logProcessedTs = useRef<number>(0)

  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  // ---------- Poll /status ----------
  useEffect(() => {
    let alive = true
    const tick = async () => {
      const s = await getStatus()
      if (!alive) return
      setData(s)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // ---------- Poll logs (for footer + sound) ----------
  useEffect(() => {
    let alive = true
    const pull = async () => {
      try {
        const r = await getLogs(250)
        if (!alive) return
        setLogs(r.logs || [])
      } catch (e) {
        console.error(e)
      } finally {
        if (alive) setLoadingLogs(false)
      }
    }
    pull()
    const id = setInterval(pull, 1500)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // ---------- Sound FX (open/close) ----------
  useEffect(() => {
    if (!logs.length) return
    const fresh = logs.filter(l => l.ts > (logProcessedTs.current || 0))
    if (fresh.length) logProcessedTs.current = fresh[fresh.length - 1].ts

    fresh.forEach(l => {
      const t = l.text || ''
      if (t.includes('Open BUY')) beep('BUY')
      else if (t.includes('Open SELL')) beep('SELL')
      else if (t.includes('Close TAKE')) beep('TAKE')
      else if (t.includes('Close STOP')) beep('STOP')
    })
  }, [logs])

  function beep(kind: 'BUY' | 'SELL' | 'TAKE' | 'STOP') {
    try {
      const ctx = new (window.AudioContext || (window as any).webkitAudioContext)()
      const now = ctx.currentTime
      const o = ctx.createOscillator()
      const g = ctx.createGain()
      o.type = 'sine'
      o.connect(g); g.connect(ctx.destination)

      const env = (t: number, v: number) => g.gain.linearRampToValueAtTime(v, now + t)
      g.gain.setValueAtTime(0, now)

      const seq: Array<[number, number]> = ((): Array<[number, number]> => {
        switch (kind) {
          case 'BUY':  return [[660, 0.00], [880, 0.08], [990, 0.14]]
          case 'SELL': return [[440, 0.00], [330, 0.08], [262, 0.14]]
          case 'TAKE': return [[880, 0.00], [660, 0.08]]
          case 'STOP': return [[220, 0.00], [196, 0.10]]
        }
      })()

      seq.forEach(([freq, t], i) => {
        o.frequency.setValueAtTime(freq, now + t)
        env(t, 0.0); env(t + 0.005, 0.18); env(t + 0.07, 0.0)
      })
      o.start(now)
      o.stop(now + 0.25)
    } catch { /* autoplay may require a user gesture first */ }
  }

  // ---------- Helpers ----------
  const fmt = (n: any, d = 2) =>
    n == null || isNaN(n) ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d })

  // Liquid-glass friendly overlays (only when bot is ON)
  const overlays: Overlay[] = useMemo(() => {
    const cs: Candle[] = (data?.candles || []) as Candle[]
    if (!data?.autoTrade || !Array.isArray(cs) || cs.length < 6) return []

    const tp = cs.map(c => (c.high + c.low + c.close) / 3)
    const ema = (arr: number[], period: number) => {
      const out: Array<number | null> = new Array(arr.length).fill(null)
      if (!arr.length) return out
      const k = 2 / (period + 1)
      let e = arr[0]
      out[0] = e
      for (let i = 1; i < arr.length; i++) {
        e = arr[i] * k + e * (1 - k)
        out[i] = e
      }
      return out
    }
    const donchMid = (period = 20) => {
      const out: Array<number | null> = new Array(cs.length).fill(null)
      for (let i = 0; i < cs.length; i++) {
        const s = Math.max(0, i - period + 1)
        let H = -Infinity, L = Infinity
        for (let j = s; j <= i; j++) { H = Math.max(H, cs[j].high); L = Math.min(L, cs[j].low) }
        out[i] = (H + L) / 2
      }
      return out
    }

    // VWAP by session
    const vwap: Array<number | null> = []
    let day: string | null = null, pv = 0, vv = 0
    cs.forEach(c => {
      const d = new Date(c.time * 1000).toISOString().slice(0, 10)
      if (day !== d) { day = d; pv = 0; vv = 0 }
      const typical = (c.high + c.low + c.close) / 3
      const v = Math.max(1e-8, c?.volume ?? 0)
      pv += typical * v; vv += v
      vwap.push(pv / Math.max(1e-8, vv))
    })

    return [
      { data: vwap, color: '#7dd3fc', dashed: true },       // VWAP (dashed)
      { data: ema(tp, 10), color: '#60a5fa' },               // EMA-10 (typical)
      { data: ema(tp, 30), color: '#fbbf24' },               // EMA-30 (typical)
      { data: donchMid(20), color: '#cbd5e1' },              // Donchian midline
    ]
  }, [data.autoTrade, data.candles])

  // ---------- Chart drawing ----------
  function drawChart(canvas: HTMLCanvasElement, candles: Candle[], overlays: Overlay[], highlight: 'BUY' | 'SELL' | null, limit = 90) {
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
    const padL = 36, padR = 8, padT = 10, padB = 18
    const chartW = W - padL - padR
    const chartH = H - padT - padB
    const start = Math.max(0, n - limit)

    let lo = Infinity, hi = -Infinity
    for (let i = start; i < n; i++) { lo = Math.min(lo, candles[i].low); hi = Math.max(hi, candles[i].high) }
    overlays?.forEach(o => {
      if (!o?.data) return
      for (let i = start; i < Math.min(n, o.data.length); i++) {
        const v = o.data[i]; if (v == null) continue
        lo = Math.min(lo, v); hi = Math.max(hi, v)
      }
    })
    if (!isFinite(lo) || !isFinite(hi) || hi <= lo) return

    const xPer = chartW / Math.max(1, Math.min(limit, n - start))
    const y = (p: number) => padT + (hi - p) * (chartH / Math.max(1e-8, hi - lo))

    // faint grid
    ctx.strokeStyle = 'rgba(255,255,255,0.06)'
    ctx.lineWidth = 1
    ctx.beginPath()
    for (let i = 0; i <= 5; i++) {
      const yy = padT + (chartH * i) / 5
      ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy)
    }
    ctx.stroke()

    // candles (GREEN up, RED down; bigger bodies)
    for (let i = start; i < n; i++) {
      const c = candles[i]
      const idx = i - start
      const x = padL + idx * xPer + xPer * 0.05
      const cw = xPer * 0.90
      const up = c.close >= c.open
      ctx.strokeStyle = up ? '#22c55e' : '#f43f5e'
      ctx.fillStyle = up ? '#22c55e' : '#f43f5e'
      // wick
      ctx.beginPath()
      ctx.moveTo(x + cw / 2, y(c.high))
      ctx.lineTo(x + cw / 2, y(c.low))
      ctx.stroke()
      // body
      const bh = Math.max(1.5, Math.abs(y(c.open) - y(c.close)))
      ctx.fillRect(x, Math.min(y(c.open), y(c.close)), cw, bh)
    }

    // overlays (only if bot ON)
    overlays?.forEach(o => {
      if (!o?.data) return
      const n2 = o.data.length
      ctx.strokeStyle = o.color || '#fff'
      ctx.lineWidth = 1.7
      ctx.setLineDash(o.dashed ? [4, 3] : [])
      ctx.beginPath()
      let started = false; let j = 0
      for (let i = start; i < n && i < n2; i++, j++) {
        const v = o.data[i]; if (v == null) continue
        const xx = padL + j * xPer + xPer / 2
        const yy = y(v)
        if (!started) { ctx.moveTo(xx, yy); started = true } else { ctx.lineTo(xx, yy) }
      }
      ctx.stroke()
      ctx.setLineDash([])
    })

    // subtle highlight border when a position is open
    if (highlight) {
      ctx.strokeStyle = highlight === 'BUY' ? 'rgba(34,197,94,0.8)' : 'rgba(244,63,94,0.8)'
      ctx.lineWidth = 3
      ctx.strokeRect(2, 2, W - 4, H - 4)
    }
  }

  // render chart
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
    const pos = data?.pos
    const hl = pos ? (pos.side === 'long' ? 'BUY' : 'SELL') : null
    drawChart(canvas, candles, overlays, hl, 90) // shorter window, larger candles
  }, [data.candles, data.pos, overlays])

  // Actions (kept only for parity; main switch moved to header)
  async function forceStartStop(next: boolean) {
    await postSettings({ autoTrade: next })
    setData((d: any) => ({ ...d, autoTrade: next }))
  }

  // UI text
  const pnlToday = data?.pnlToday ?? 0
  const fills = data?.fillsToday ?? 0
  const pos = data?.pos
  const meta = (pos?.meta || {}) as any

  const atrLine = data.atrPct == null ? null : `ATR%: ${fmt((data.atrPct || 0) * 100, 2)}%`
  const conditionsText = [
    data.regime ? `Regime: ${data.regime}` : null,
    data.bias ? `Bias: ${data.bias}` : null,
    data.adx != null ? `ADX: ${fmt(data.adx, 0)}` : null,
    atrLine,
  ].filter(Boolean).join(' • ')

  return (
    <div>
      {/* STATUS + CONDITIONS */}
      <div className="status-row">
        <div className={`glass status-pill ${pos ? (pos.side === 'long' ? 'status-green' : 'status-red') : ''}`}>
          <b>STATUS:</b>&nbsp;&nbsp;{data.status ?? '—'}
        </div>
        <div className="glass conditions-pill">
          <b>CONDITIONS:</b>&nbsp;&nbsp;{conditionsText || '—'}
        </div>
      </div>

      {/* GRID */}
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
          {/* Account card (toggle removed; API link added) */}
          <section className="glass pcard">
            <div className="card-title-row">
              <h2 className="card-title">Account</h2>
              <Link to="/apikeys" className="api-link">API</Link>
            </div>
            <div className="acct-grid">
              <div className="glass mini">
                <div className="muted-xs">Equity</div>
                <div className="num-lg">${fmt(data.equity, 2)}</div>
              </div>
              <div className="glass mini">
                <div className="muted-xs">P&amp;L Today</div>
                <div className={`num-lg ${pnlToday >= 0 ? 'price-up' : 'price-down'}`}>
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

            {!data.autoTrade ? (
              <button className="btn-pill" onClick={() => forceStartStop(true)} style={{ marginTop: 10 }}>
                Turn Bot ON
              </button>
            ) : (
              <button className="btn-pill danger" onClick={() => forceStartStop(false)} style={{ marginTop: 10 }}>
                Turn Bot OFF
              </button>
            )}
          </section>

          <section className="glass pcard">
            <h2 className="card-title">Open Position</h2>
            {!pos ? (
              <div className="muted">No open position.</div>
            ) : (
              <table className="table compact">
                <tbody>
                  <tr>
                    <td>Side</td>
                    <td style={{ textAlign: 'right', textTransform: 'capitalize' }}>{pos.side}</td>
                  </tr>
                  <tr>
                    <td>Qty</td>
                    <td style={{ textAlign: 'right' }}>{fmt(pos.qty, 6)}</td>
                  </tr>
                  <tr>
                    <td>Entry / Stop / Take</td>
                    <td style={{ textAlign: 'right' }}>
                      {fmt(pos.entry, 2)} / {fmt(pos.stop, 2)} / {fmt(pos.take, 2)}
                    </td>
                  </tr>
                  <tr>
                    <td>1R ($)</td>
                    <td style={{ textAlign: 'right' }}>{fmt(pos.stop_dist, 2)}</td>
                  </tr>
                  <tr>
                    <td>TP% / Fee→TP</td>
                    <td style={{ textAlign: 'right' }}>
                      {meta?.final_tp_pct != null ? `${fmt((meta.final_tp_pct || 0) * 100, 2)}%` : '—'}
                      {' '} / {meta?.fee_to_tp != null ? fmt(meta.fee_to_tp, 3) : '—'}
                    </td>
                  </tr>
                  <tr>
                    <td>Fast Tape</td>
                    <td style={{ textAlign: 'right' }}>
                      {meta?.fast_tape_taker ? 'TAKER' : 'MAKER'}
                      {meta?.fast_tape_disabled ? ' (disabled)' : ''}
                    </td>
                  </tr>
                </tbody>
              </table>
            )}
          </section>

          <section className="glass pcard">
            <h2 className="card-title">Tape &amp; Risk</h2>
            <div className="kv"><div>Spread (bps)</div><div>{fmt(data.spreadBps, 2)}</div></div>
            <div className="kv"><div>Fee→TP</div><div>{data.feeToTp != null ? fmt(data.feeToTp, 3) : '—'}</div></div>
            <div className="kv"><div>Slip Est ($)</div><div>{fmt(data.slipEst, 2)}</div></div>
            <div className="kv"><div>Top‑3 Depth ($)</div><div>{fmt(data.top3DepthNotional, 0)}</div></div>
            <div className="kv"><div>Red‑Day Level</div><div>{data.redDayLevel ?? 0}</div></div>
            <div className="kv"><div>Fast Tape Disabled</div><div>{data.fastTapeDisabled ? 'Yes' : 'No'}{data.takerFailCount30m ? ` (${data.takerFailCount30m})` : ''}</div></div>
          </section>
        </div>
      </div>

      {/* FOOTER LOGS — gray, no panel */}
      <div className="logbar container" role="log" aria-live="polite">
        {loadingLogs ? (
          <span className="muted">Loading logs…</span>
        ) : (
          (logs.slice(-40)).map((l, i) => (
            <span key={i} className="logchip">
              <span className="logts">{new Date(l.ts * 1000).toLocaleTimeString()}</span>
              <span className="logtxt">{l.text}</span>
            </span>
          ))
        )}
      </div>
    </div>
  )
}
// EOF
