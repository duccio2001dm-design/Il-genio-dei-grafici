"""
Scaricamento candele da fonti pubbliche + ricampionamento locale.

Fonti supportate:
  - binance     -> BTCUSDT e altre crypto. Gratis, senza chiave, 24/7.
  - coinbase    -> BTC-USD. Alternativa se Binance non e' raggiungibile.
  - twelvedata  -> XAU/USD e forex. Richiede una API key (piano free disponibile).

Nota sul risparmio di chiamate: per l'oro scarichiamo SOLO il timeframe base
(15 minuti) e ricostruiamo 1h e 4h in locale. Una chiamata per ciclo invece di tre,
cosi' il piano gratuito di Twelve Data regge tranquillamente.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, NamedTuple, Optional

USER_AGENT = "trading-assistant/1.0"
TIMEOUT = 20


class Candle(NamedTuple):
    ts: int  # apertura candela, millisecondi UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


class DataError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _get_json(url: str, params: Dict[str, object]) -> object:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    full = f"{url}?{query}" if query else url
    req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DataError(f"HTTP {exc.code} da {url}: {exc.read()[:200]!r}") from exc
    except Exception as exc:  # rete assente, timeout, JSON rotto...
        raise DataError(f"Errore di rete su {url}: {exc}") from exc


# --------------------------------------------------------------------------
# Fonti
# --------------------------------------------------------------------------

def fetch_binance(symbol: str = "BTCUSDT", interval: str = "15m", limit: int = 500) -> List[Candle]:
    raw = _get_json(
        "https://api.binance.com/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)},
    )
    if not isinstance(raw, list):
        raise DataError(f"Risposta Binance inattesa: {str(raw)[:200]}")
    return [
        Candle(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
        for k in raw
    ]


_COINBASE_GRANULARITY = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}


def fetch_coinbase(symbol: str = "BTC-USD", interval: str = "15m", limit: int = 300) -> List[Candle]:
    gran = _COINBASE_GRANULARITY.get(interval)
    if gran is None:
        raise DataError(f"Coinbase non supporta il timeframe {interval}")
    raw = _get_json(
        f"https://api.exchange.coinbase.com/products/{symbol}/candles",
        {"granularity": gran},
    )
    if not isinstance(raw, list):
        raise DataError(f"Risposta Coinbase inattesa: {str(raw)[:200]}")
    # formato: [time, low, high, open, close, volume], ordine decrescente
    candles = [
        Candle(int(r[0]) * 1000, float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5]))
        for r in raw
    ]
    candles.sort(key=lambda c: c.ts)
    return candles[-limit:]


_TWELVE_INTERVAL = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1day"}


def fetch_twelvedata(
    symbol: str = "XAU/USD",
    interval: str = "15m",
    limit: int = 1000,
    api_key: str = "",
) -> List[Candle]:
    if not api_key:
        raise DataError("Manca la API key di Twelve Data (serve per XAUUSD).")

    td_interval = _TWELVE_INTERVAL.get(interval)
    if td_interval is None:
        raise DataError(f"Twelve Data non supporta il timeframe {interval}")

    raw = _get_json(
        "https://api.twelvedata.com/time_series",
        {
            "symbol": symbol,
            "interval": td_interval,
            "outputsize": min(limit, 5000),
            "order": "ASC",
            "timezone": "UTC",
            "format": "JSON",
            "apikey": api_key,
        },
    )

    if not isinstance(raw, dict):
        raise DataError(f"Risposta Twelve Data inattesa: {str(raw)[:200]}")
    if raw.get("status") == "error" or "values" not in raw:
        raise DataError(f"Twelve Data: {raw.get('message', raw)}")

    candles: List[Candle] = []
    for row in raw["values"]:
        dt_text = row["datetime"]
        fmt = "%Y-%m-%d %H:%M:%S" if len(dt_text) > 10 else "%Y-%m-%d"
        dt = datetime.strptime(dt_text, fmt).replace(tzinfo=timezone.utc)
        candles.append(
            Candle(
                int(dt.timestamp() * 1000),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row.get("volume") or 0.0),
            )
        )
    candles.sort(key=lambda c: c.ts)
    return candles


# --------------------------------------------------------------------------
# Ricampionamento
# --------------------------------------------------------------------------

TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def resample(candles: List[Candle], target_tf: str) -> List[Candle]:
    """Aggrega candele piccole in candele piu' grandi, allineate all'orologio UTC."""
    minutes = TF_MINUTES[target_tf]
    bucket_ms = minutes * 60 * 1000

    buckets: Dict[int, List[Candle]] = {}
    for c in candles:
        key = (c.ts // bucket_ms) * bucket_ms
        buckets.setdefault(key, []).append(c)

    out: List[Candle] = []
    for key in sorted(buckets):
        group = buckets[key]
        out.append(
            Candle(
                ts=key,
                open=group[0].open,
                high=max(g.high for g in group),
                low=min(g.low for g in group),
                close=group[-1].close,
                volume=sum(g.volume for g in group),
            )
        )
    return out


def drop_incomplete(candles: List[Candle], tf: str, now_ms: Optional[int] = None) -> List[Candle]:
    """
    Toglie l'ultima candela se non e' ancora chiusa.
    Analizzare una candela in formazione e' la fonte numero uno di falsi segnali:
    un incrocio a meta' candela puo' sparire prima della chiusura.
    """
    if not candles:
        return candles
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    bucket_ms = TF_MINUTES[tf] * 60 * 1000
    if candles[-1].ts + bucket_ms > now_ms:
        return candles[:-1]
    return candles


# --------------------------------------------------------------------------
# Orari di mercato
# --------------------------------------------------------------------------

def forex_is_open(now: Optional[datetime] = None) -> bool:
    """
    Approssimazione degli orari dello spot su oro/forex (tutto in UTC):
    apre domenica 22:00, chiude venerdi' 21:00, pausa giornaliera 21:00-22:00.
    I broker variano di un'ora, quindi tienilo per quello che e': una stima.
    """
    now = now or datetime.now(timezone.utc)
    weekday = now.weekday()  # 0 = lunedi'
    hour = now.hour

    if weekday == 5:  # sabato
        return False
    if weekday == 6:  # domenica
        return hour >= 22
    if weekday == 4 and hour >= 21:  # venerdi' sera
        return False
    return not (21 <= hour < 22)  # pausa serale infrasettimanale


# --------------------------------------------------------------------------
# Punto di ingresso unico
# --------------------------------------------------------------------------

def load_frames(symbol_cfg: dict, api_keys: dict, timeframes=("15m", "1h", "4h")) -> Dict[str, List[Candle]]:
    """
    Restituisce {timeframe: [candele chiuse]} per uno strumento.
    Per le fonti senza timeframe multipli scarica il base e ricampiona.
    """
    source = symbol_cfg.get("source", "binance")
    api_symbol = symbol_cfg["api_symbol"]
    frames: Dict[str, List[Candle]] = {}

    if source == "binance":
        for tf in timeframes:
            limit = 500 if tf != "4h" else 400
            frames[tf] = drop_incomplete(fetch_binance(api_symbol, tf, limit), tf)

    elif source == "coinbase":
        base = fetch_coinbase(api_symbol, "15m", 300)
        frames["15m"] = drop_incomplete(base, "15m")
        for tf in timeframes:
            if tf != "15m":
                frames[tf] = drop_incomplete(resample(base, tf), tf)

    elif source == "twelvedata":
        base = fetch_twelvedata(api_symbol, "15m", 1000, api_keys.get("twelvedata", ""))
        frames["15m"] = drop_incomplete(base, "15m")
        for tf in timeframes:
            if tf != "15m":
                frames[tf] = drop_incomplete(resample(base, tf), tf)

    else:
        raise DataError(f"Fonte dati sconosciuta: {source}")

    for tf, candles in frames.items():
        if len(candles) < 60:
            raise DataError(f"{api_symbol} {tf}: solo {len(candles)} candele, troppo poche per gli indicatori")

    return frames
