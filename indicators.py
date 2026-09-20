"""
Indicatori tecnici in Python puro: nessuna dipendenza esterna (niente pandas/numpy).
Scelta voluta, cosi' gira ovunque: VPS ARM, Raspberry, Termux su Android.

Convenzione: ogni funzione restituisce una lista lunga quanto l'input,
con None nelle posizioni iniziali di "riscaldamento". Cosi' serie[-1] e'
sempre il valore piu' recente e gli indici restano allineati alle candele.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

Series = List[Optional[float]]


# --------------------------------------------------------------------------
# Medie
# --------------------------------------------------------------------------

def sma(values: Sequence[float], period: int) -> Series:
    """Media mobile semplice."""
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    """Media mobile esponenziale, inizializzata con la SMA del primo periodo."""
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (values[i] - prev) * k + prev
        out[i] = prev
    return out


def stdev(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / n) ** 0.5


# --------------------------------------------------------------------------
# Momentum
# --------------------------------------------------------------------------

def rsi(closes: Sequence[float], period: int = 14) -> Series:
    """RSI con smoothing di Wilder (quello usato da MT4/TradingView)."""
    out: Series = [None] * len(closes)
    if len(closes) <= period:
        return out

    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change

    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    return out


def macd(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[Series, Series, Series]:
    """Restituisce (linea MACD, linea segnale, istogramma)."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    line: Series = [None] * len(closes)
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            line[i] = ema_fast[i] - ema_slow[i]

    valid_idx = [i for i, v in enumerate(line) if v is not None]
    sig: Series = [None] * len(closes)
    hist: Series = [None] * len(closes)

    if valid_idx:
        compact = [line[i] for i in valid_idx]
        compact_signal = ema(compact, signal)
        for pos, i in enumerate(valid_idx):
            sig[i] = compact_signal[pos]
            if compact_signal[pos] is not None:
                hist[i] = line[i] - compact_signal[pos]

    return line, sig, hist


# --------------------------------------------------------------------------
# Volatilita'
# --------------------------------------------------------------------------

def true_range(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> Series:
    out: Series = [None] * len(closes)
    if not closes:
        return out
    out[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        out[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return out


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> Series:
    """Average True Range, smoothing di Wilder."""
    tr = true_range(highs, lows, closes)
    out: Series = [None] * len(closes)
    if len(closes) < period + 1:
        return out

    prev = sum(tr[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, len(closes)):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def bollinger(
    closes: Sequence[float], period: int = 20, mult: float = 2.0
) -> Tuple[Series, Series, Series, Series]:
    """Restituisce (banda centrale, superiore, inferiore, ampiezza normalizzata)."""
    mid = sma(closes, period)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    width: Series = [None] * len(closes)

    for i in range(len(closes)):
        if mid[i] is None:
            continue
        window = list(closes[i - period + 1 : i + 1])
        sd = stdev(window)
        upper[i] = mid[i] + mult * sd
        lower[i] = mid[i] - mult * sd
        width[i] = (upper[i] - lower[i]) / mid[i] if mid[i] else None

    return mid, upper, lower, width


# --------------------------------------------------------------------------
# Forza del trend
# --------------------------------------------------------------------------

def adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> Tuple[Series, Series, Series]:
    """Restituisce (+DI, -DI, ADX) con il metodo originale di Wilder."""
    n = len(closes)
    plus_di: Series = [None] * n
    minus_di: Series = [None] * n
    adx_out: Series = [None] * n
    if n < 2 * period + 1:
        return plus_di, minus_di, adx_out

    tr = true_range(highs, lows, closes)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

    tr_sum = sum(tr[1 : period + 1])
    plus_sum = sum(plus_dm[1 : period + 1])
    minus_sum = sum(minus_dm[1 : period + 1])

    dx_values: List[Tuple[int, float]] = []
    for i in range(period, n):
        if i > period:
            tr_sum = tr_sum - tr_sum / period + tr[i]
            plus_sum = plus_sum - plus_sum / period + plus_dm[i]
            minus_sum = minus_sum - minus_sum / period + minus_dm[i]

        if tr_sum > 0:
            pdi = 100.0 * plus_sum / tr_sum
            mdi = 100.0 * minus_sum / tr_sum
        else:
            pdi = mdi = 0.0

        plus_di[i] = pdi
        minus_di[i] = mdi
        denom = pdi + mdi
        dx_values.append((i, 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0))

    if len(dx_values) >= period:
        prev = sum(d for _, d in dx_values[:period]) / period
        adx_out[dx_values[period - 1][0]] = prev
        for i, dx in dx_values[period:]:
            prev = (prev * (period - 1) + dx) / period
            adx_out[i] = prev

    return plus_di, minus_di, adx_out


# --------------------------------------------------------------------------
# Struttura di prezzo
# --------------------------------------------------------------------------

def swing_points(
    highs: Sequence[float], lows: Sequence[float], left: int = 2, right: int = 2
) -> Tuple[List[int], List[int]]:
    """
    Indici dei massimi e minimi di oscillazione (pivot).
    Un pivot high e' un massimo strettamente maggiore delle `left` candele
    precedenti e maggiore o uguale alle `right` successive.
    """
    pivot_highs: List[int] = []
    pivot_lows: List[int] = []

    for i in range(left, len(highs) - right):
        if all(highs[i] > highs[j] for j in range(i - left, i)) and all(
            highs[i] >= highs[j] for j in range(i + 1, i + right + 1)
        ):
            pivot_highs.append(i)
        if all(lows[i] < lows[j] for j in range(i - left, i)) and all(
            lows[i] <= lows[j] for j in range(i + 1, i + right + 1)
        ):
            pivot_lows.append(i)

    return pivot_highs, pivot_lows


def percentile_rank(values: Sequence[Optional[float]], value: Optional[float]) -> Optional[float]:
    """Percentuale di valori della finestra che sono sotto `value` (0-100)."""
    clean = [v for v in values if v is not None]
    if value is None or len(clean) < 5:
        return None
    below = sum(1 for v in clean if v < value)
    return 100.0 * below / len(clean)


def last(series: Series, offset: int = 0) -> Optional[float]:
    """Valore piu' recente (o a `offset` candele di distanza), None se assente."""
    idx = len(series) - 1 - offset
    if idx < 0 or idx >= len(series):
        return None
    return series[idx]
