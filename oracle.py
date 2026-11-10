"""
Oracolo probabilistico per BTCUSD a 1 e 4 ore.

Cosa fa: per l'ultima candela chiusa a 15 minuti stima
  - la probabilita' che il prezzo sia piu' alto tra 1 ora e tra 4 ore;
  - la forbice entro cui il prezzo restera' con l'80% e il 50% di probabilita'.

Cosa NON fa: non sa il futuro. I pesi in oracle_model.json sono stati stimati su
anni di dati e verificati su anni che il modello non aveva mai visto
(vedi la sezione "validation" del file). Il risultato onesto di quella verifica:
  - le forbici sono affidabili (l'80% contiene il prezzo circa 8 volte su 10);
  - sulla direzione il vantaggio e' piccolo (circa 52-53% di previsioni giuste)
    e negli ultimi mesi si e' quasi azzerato.
Per questo l'oracolo dice "nessun vantaggio" ogni volta che la probabilita' e'
vicina al 50%, e tiene una pagella dal vivo di ogni previsione.

Le feature sono calcolate qui, in Python puro, sia per l'addestramento sia per
l'uso dal vivo: un solo codice, nessuna differenza tra test e realta'.
"""

from __future__ import annotations

import json
import math
import os
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oracle_model.json")
BAR_MS = 15 * 60 * 1000
HORIZONS = {"1h": 4, "4h": 16}   # in candele da 15 minuti
WARMUP = 700                      # candele necessarie prima della prima feature valida

DIRECTION_FEATURES = [
    "ret_1", "ret_4", "ret_16", "ret_48", "ret_96", "ret_288", "ret_672",
    "vol_ratio_16_96", "vol_ratio_96_672", "vol_level",
    "dist_ema20", "dist_ema50", "dist_ema200",
    "rsi14", "rsi56", "pos_24h", "pos_7d", "close_pos", "range_atr", "vol_z",
    "hour_sin", "hour_cos", "weekend",
]
SIGMA_FEATURES = ["lv16", "lv96", "lv672", "lrange4"] + [f"h{h:02d}" for h in range(0, 24, 2)] + ["weekend"]


# --------------------------------------------------------------------------
# Strumenti rolling in O(n)
# --------------------------------------------------------------------------


class _RollingStd:
    def __init__(self, window: int):
        self.w, self.q, self.s, self.ss = window, deque(), 0.0, 0.0

    def push(self, x: float) -> Optional[float]:
        self.q.append(x)
        self.s += x
        self.ss += x * x
        if len(self.q) > self.w:
            old = self.q.popleft()
            self.s -= old
            self.ss -= old * old
        if len(self.q) < self.w:
            return None
        n = self.w
        var = (self.ss - self.s * self.s / n) / (n - 1)
        return math.sqrt(var) if var > 0 else 0.0

    def mean(self) -> float:
        return self.s / len(self.q)


class _RollingExtreme:
    """Massimo (sign=1) o minimo (sign=-1) su finestra mobile con deque monotona."""

    def __init__(self, window: int, sign: int):
        self.w, self.sign, self.q, self.i = window, sign, deque(), -1

    def push(self, x: float) -> Optional[float]:
        self.i += 1
        v = x * self.sign
        while self.q and self.q[-1][1] <= v:
            self.q.pop()
        self.q.append((self.i, v))
        while self.q[0][0] <= self.i - self.w:
            self.q.popleft()
        if self.i + 1 < self.w:
            return None
        return self.q[0][1] * self.sign


class _RollingMean:
    def __init__(self, window: int):
        self.w, self.q, self.s = window, deque(), 0.0

    def push(self, x: float) -> Optional[float]:
        self.q.append(x)
        self.s += x
        if len(self.q) > self.w:
            self.s -= self.q.popleft()
        return self.s / self.w if len(self.q) == self.w else None


# --------------------------------------------------------------------------
# Feature
# --------------------------------------------------------------------------


def compute_features(candles: Sequence, only_last: bool = False) -> List[Optional[Dict[str, float]]]:
    """
    Una riga di feature per candela (None finche' non ci sono abbastanza dati).
    candles: oggetti con ts (ms UTC, apertura), open, high, low, close, volume.
    """
    n = len(candles)
    out: List[Optional[Dict[str, float]]] = [None] * n
    lc: List[float] = []
    std16, std96, std672 = _RollingStd(16), _RollingStd(96), _RollingStd(672)
    vz = _RollingStd(96)
    hi96, lo96 = _RollingExtreme(96, 1), _RollingExtreme(96, -1)
    hi672, lo672 = _RollingExtreme(672, 1), _RollingExtreme(672, -1)
    rng4 = _RollingMean(4)
    ema = {20: None, 50: None, 200: None}
    atr = None
    up14 = dn14 = up56 = dn56 = None
    prev_close = None

    for i, c in enumerate(candles):
        close, high, low = float(c.close), float(c.high), float(c.low)
        lc.append(math.log(close))
        r1 = lc[-1] - lc[-2] if i > 0 else 0.0

        s16 = std16.push(r1) if i > 0 else None
        s96 = std96.push(r1) if i > 0 else None
        s672 = std672.push(r1) if i > 0 else None

        tr = high - low if prev_close is None else max(high - low, abs(high - prev_close), abs(low - prev_close))
        atr = tr if atr is None else atr + (tr - atr) / 14.0
        for span in ema:
            ema[span] = close if ema[span] is None else ema[span] + (close - ema[span]) * 2.0 / (span + 1.0)

        if prev_close is not None:
            d = close - prev_close
            g, l_ = max(d, 0.0), max(-d, 0.0)
            up14 = g if up14 is None else up14 + (g - up14) / 14.0
            dn14 = l_ if dn14 is None else dn14 + (l_ - dn14) / 14.0
            up56 = g if up56 is None else up56 + (g - up56) / 56.0
            dn56 = l_ if dn56 is None else dn56 + (l_ - dn56) / 56.0

        h96, l96 = hi96.push(high), lo96.push(low)
        h672, l672 = hi672.push(high), lo672.push(low)
        lvol = math.log1p(max(float(c.volume), 0.0))
        vstd = vz.push(lvol)
        mr = rng4.push((high - low) / close)
        prev_close = close

        if i < WARMUP or (only_last and i != n - 1):
            continue
        if not (s16 and s96 and s672 and atr and h672 is not None and vstd is not None and mr):
            continue

        f: Dict[str, float] = {}
        for k in (1, 4, 16, 48, 96, 288, 672):
            f[f"ret_{k}"] = (lc[-1] - lc[-1 - k]) / (s96 * math.sqrt(k))
        f["vol_ratio_16_96"] = math.log(s16 / s96)
        f["vol_ratio_96_672"] = math.log(s96 / s672)
        f["vol_level"] = math.log(s96)
        for span in (20, 50, 200):
            f[f"dist_ema{span}"] = (close - ema[span]) / atr
        f["rsi14"] = (_rsi(up14, dn14) - 50.0) / 50.0
        f["rsi56"] = (_rsi(up56, dn56) - 50.0) / 50.0
        f["pos_24h"] = ((close - l96) / (h96 - l96) - 0.5) if h96 > l96 else 0.0
        f["pos_7d"] = ((close - l672) / (h672 - l672) - 0.5) if h672 > l672 else 0.0
        f["close_pos"] = ((close - low) / (high - low) - 0.5) if high > low else 0.0
        f["range_atr"] = (high - low) / atr
        f["vol_z"] = (lvol - vz.mean()) / vstd if vstd > 0 else 0.0

        dt = datetime.fromtimestamp(int(c.ts) / 1000, timezone.utc)
        hour = dt.hour + dt.minute / 60.0
        f["hour_sin"] = math.sin(2 * math.pi * hour / 24)
        f["hour_cos"] = math.cos(2 * math.pi * hour / 24)
        f["weekend"] = 1.0 if dt.weekday() >= 5 else 0.0

        f["lv16"], f["lv96"], f["lv672"] = math.log(s16), math.log(s96), math.log(s672)
        f["lrange4"] = math.log(mr) if mr > 0 else math.log(1e-6)
        for h in range(0, 24, 2):
            f[f"h{h:02d}"] = 1.0 if (dt.hour // 2) * 2 == h else 0.0
        out[i] = f

    return out


def _rsi(up: Optional[float], dn: Optional[float]) -> float:
    if up is None or dn is None:
        return 50.0
    if dn == 0:
        return 100.0 if up > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + up / dn)


# --------------------------------------------------------------------------
# Modello
# --------------------------------------------------------------------------


def load_model(path: str = MODEL_PATH) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _logistic(model: dict, f: Dict[str, float]) -> float:
    z = model["intercept"]
    for name, mean, std, w in zip(model["features"], model["mean"], model["std"], model["coef"]):
        z += w * ((f[name] - mean) / std if std else 0.0)
    cal = model.get("calibration")
    if cal:
        z = cal["a"] * z + cal["b"]
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def _sigma(model: dict, f: Dict[str, float]) -> float:
    z = model["intercept"]
    for name, w in zip(model["features"], model["coef"]):
        z += w * f[name]
    return math.exp(z / 2.0)   # il modello stima log(rendimento^2)


def predict(candles: Sequence, model: dict) -> Optional[dict]:
    """Previsione per l'ultima candela chiusa."""
    if len(candles) < WARMUP + 1:
        return None
    feats = compute_features(candles, only_last=True)[-1]
    if feats is None:
        return None
    last = candles[-1]
    price = float(last.close)
    result = {"ts": int(last.ts), "price": price, "horizons": {}}
    for label, k in HORIZONS.items():
        m = model["horizons"][label]
        p_up = _logistic(m["direction"], feats)
        sigma = _sigma(m["sigma"], feats)
        q = m["sigma"]["z_quantiles"]
        bands = {}
        for name, (lo_key, hi_key) in {"80": ("q10", "q90"), "50": ("q25", "q75")}.items():
            bands[name] = (price * math.exp(q[lo_key] * sigma), price * math.exp(q[hi_key] * sigma))
        typical = m["sigma"].get("median_sigma")
        result["horizons"][label] = {
            "k": k,
            "p_up": p_up,
            "sigma": sigma,
            "calm_ratio": sigma / typical if typical else None,
            "bands": bands,
        }
    return result


def lean(p_up: float, threshold: float) -> str:
    """'up', 'down' o 'none' (nessun vantaggio)."""
    if p_up >= 0.5 + threshold:
        return "up"
    if p_up <= 0.5 - threshold:
        return "down"
    return "none"


# --------------------------------------------------------------------------
# Pagella dal vivo
# --------------------------------------------------------------------------


def _empty_score() -> dict:
    return {"n": 0, "hits": 0, "brier": 0.0, "lean_n": 0, "lean_hits": 0, "in80": 0, "in50": 0}


def record(state: dict, symbol: str, forecast: dict, threshold: float, every_bars: int = 4) -> bool:
    """Registra una previsione ogni `every_bars` candele (di default una all'ora)."""
    ora = state.setdefault("oracle", {})
    pending = ora.setdefault("pending", [])
    ts = forecast["ts"]
    if (ts // BAR_MS) % every_bars != every_bars - 1:
        return False
    if any(p["ts"] == ts and p["symbol"] == symbol for p in pending):
        return False
    pending.append({
        "symbol": symbol, "ts": ts, "price": forecast["price"],
        "h": {label: {"p_up": round(h["p_up"], 4),
                      "lean": lean(h["p_up"], threshold),
                      "b80": [round(x, 2) for x in h["bands"]["80"]],
                      "b50": [round(x, 2) for x in h["bands"]["50"]]}
              for label, h in forecast["horizons"].items()},
        "done": [],
    })
    del pending[:-400]
    return True


def resolve(state: dict, symbol: str, candles: Sequence) -> int:
    """Confronta le previsioni scadute con il prezzo reale. Restituisce quante ne ha chiuse."""
    ora = state.setdefault("oracle", {})
    pending = ora.setdefault("pending", [])
    score = ora.setdefault("score", {})
    history = ora.setdefault("history", [])
    closes = {int(c.ts): float(c.close) for c in candles}
    resolved = 0
    for item in pending:
        if item["symbol"] != symbol:
            continue
        for label, k in HORIZONS.items():
            if label in item["done"]:
                continue
            target_ts = item["ts"] + k * BAR_MS
            if target_ts not in closes:
                continue
            actual = closes[target_ts]
            h = item["h"][label]
            up = 1 if actual > item["price"] else 0
            s = score.setdefault(f"{symbol}:{label}", _empty_score())
            s["n"] += 1
            s["hits"] += int((h["p_up"] > 0.5) == bool(up))
            s["brier"] += (h["p_up"] - up) ** 2
            if h["lean"] != "none":
                s["lean_n"] += 1
                s["lean_hits"] += int((h["lean"] == "up") == bool(up))
            s["in80"] += int(h["b80"][0] <= actual <= h["b80"][1])
            s["in50"] += int(h["b50"][0] <= actual <= h["b50"][1])
            history.append({"symbol": symbol, "ts": item["ts"], "h": label, "p_up": h["p_up"],
                            "up": up, "in80": int(h["b80"][0] <= actual <= h["b80"][1])})
            item["done"].append(label)
            resolved += 1
    ora["pending"] = [p for p in pending if len(p["done"]) < len(HORIZONS)]
    del history[:-2000]
    return resolved
