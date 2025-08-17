# backend/ta/__init__.py
from math import isnan

def ema(v, p):
    if not v: return []
    k = 2.0 / (p + 1.0)
    o = [None] * len(v)
    e = float(v[0]); o[0] = e
    for i in range(1, len(v)):
        e = float(v[i]) * k + e * (1 - k)
        o[i] = e
    return o

def rma(v, p):
    if not v or len(v) < p + 1: return [None] * len(v)
    o = [None] * len(v)
    seed = sum(v[1:p+1]) / float(p)
    o[p] = seed
    a = 1.0 / p
    e = seed
    for i in range(p+1, len(v)):
        e = a * v[i] + (1 - a) * e
        o[i] = e
    return o

def atr(ohlc, p=14):
    if not ohlc: return []
    tr = [None] * len(ohlc)
    for i, c in enumerate(ohlc):
        if i == 0:
            tr[i] = c["high"] - c["low"]
        else:
            pc = ohlc[i-1]["close"]
            hl = c["high"] - c["low"]
            hc = abs(c["high"] - pc)
            lc = abs(c["low"] - pc)
            tr[i] = max(hl, hc, lc)
    return ema(tr, p)

def adx(ohlc, p=14):
    n = len(ohlc)
    if n < p + 2: return [None] * n
    plusDM = [0.0] * n
    minusDM = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        up = ohlc[i]["high"] - ohlc[i-1]["high"]
        dn = ohlc[i-1]["low"] - ohlc[i]["low"]
        plusDM[i]  = up if (up > 0 and up > dn) else 0.0
        minusDM[i] = dn if (dn > 0 and dn > up) else 0.0
        hl = ohlc[i]["high"] - ohlc[i]["low"]
        hc = abs(ohlc[i]["high"] - ohlc[i-1]["close"])
        lc = abs(ohlc[i]["low"]  - ohlc[i-1]["close"])
        tr[i] = max(hl, hc, lc)
    atr_r = rma(tr, p)
    pdm_r = rma(plusDM, p)
    mdm_r = rma(minusDM, p)
    plusDI  = [None] * n
    minusDI = [None] * n
    dx      = [None] * n
    for i in range(n):
        if not atr_r[i]: continue
        plusDI[i]  = 100.0 * (pdm_r[i] / atr_r[i]) if atr_r[i] else None
        minusDI[i] = 100.0 * (mdm_r[i] / atr_r[i]) if atr_r[i] else None
        denom = (plusDI[i] or 0.0) + (minusDI[i] or 0.0)
        if denom > 0:
            dx[i] = 100.0 * abs((plusDI[i] or 0.0) - (minusDI[i] or 0.0)) / denom
    return rma(dx, p)

def donchian(ohlc, p=20):
    n = len(ohlc)
    hi = [None] * n; lo = [None] * n
    for i in range(n):
        s = max(0, i - p + 1)
        H = max(c["high"] for c in ohlc[s:i+1])
        L = min(c["low"]  for c in ohlc[s:i+1])
        hi[i] = H; lo[i] = L
    return {"hi": hi, "lo": lo}

def rsi(closes, p=14):
    if len(closes) < p + 1: return [None] * len(closes)
    gains = [0.0]; losses = [0.0]
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i-1]
        gains.append(max(0.0, ch))
        losses.append(max(0.0, -ch))
    avg_gain = rma(gains, p)
    avg_loss = rma(losses, p)
    out = [None] * len(closes)
    for i in range(len(closes)):
        ag = avg_gain[i]; al = avg_loss[i]
        if ag is None or al is None: continue
        rs = ag / al if al > 0 else float("inf")
        out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out
