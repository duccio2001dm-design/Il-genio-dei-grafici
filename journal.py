"""
Registro minimale dei segnali operativi e dei loro esiti osservabili.

Non decide se un trade e' buono o cattivo: conserva entry, livelli, contesto
e cio' che le candele successive permettono di verificare senza inventare
l'ordine intrabar quando una singola candela tocca piu' livelli.
"""

from __future__ import annotations

import time
from typing import Dict, List


def _ensure(state: dict) -> List[dict]:
    return state.setdefault("trade_journal", [])


def register_signal(state: dict, symbol: str, signal, candle_ts: int) -> bool:
    """Registra un alert operativo una sola volta per stessa entry/strategia."""
    if signal.plan is None:
        return False

    journal = _ensure(state)
    plan = signal.plan

    # L'alert viene generato alla chiusura della candela corrente.
    entry_key = f"{symbol}|{signal.key}|{candle_ts}|{plan.reference:.8f}"
    if any(t.get("id") == entry_key for t in journal):
        return False

    journal.append({
        "id": entry_key,
        "symbol": symbol,
        "signal_key": signal.key,
        "title": signal.title,
        "direction": signal.direction,
        "score": signal.score,
        "entry": plan.reference,
        "sl": plan.invalidation,
        "tp1": plan.target_1r,
        "tp2": plan.target_2r,
        "structural_target": plan.structural_target,
        "size_units": plan.size_units,
        "money_at_risk": plan.money_at_risk,
        "entry_ts": candle_ts,
        "created_at": time.time(),
        "status": "open",
        "tp1_hit": False,
        "tp2_hit": False,
        "mfe_units": 0.0,
        "mae_units": 0.0,
        "last_checked_ts": candle_ts,
        "outcome_ts": None,
        "outcome_note": None,
    })
    return True


def _excursion(trade: dict, high: float, low: float) -> tuple[float, float]:
    entry = float(trade["entry"])
    if trade["direction"] == "long":
        return high - entry, entry - low
    return entry - low, high - entry


def update_from_candles(state: dict, symbol: str, candles: list) -> List[dict]:
    """Aggiorna i trade aperti usando solo candele chiuse successive all'entry."""
    journal = _ensure(state)
    changed = []

    for trade in journal:
        if trade.get("symbol") != symbol or trade.get("status") != "open":
            continue

        entry_ts = int(trade["entry_ts"])
        direction = trade["direction"]
        sl = float(trade["sl"])
        tp1 = float(trade["tp1"])
        tp2 = float(trade["tp2"])

        for candle in candles:
            if candle.ts <= entry_ts or candle.ts <= int(trade.get("last_checked_ts", entry_ts)):
                continue

            mfe, mae = _excursion(trade, candle.high, candle.low)
            trade["mfe_units"] = max(float(trade.get("mfe_units", 0.0)), mfe)
            trade["mae_units"] = max(float(trade.get("mae_units", 0.0)), mae)

            if direction == "long":
                sl_hit = candle.low <= sl
                tp1_hit = candle.high >= tp1
                tp2_hit = candle.high >= tp2
            else:
                sl_hit = candle.high >= sl
                tp1_hit = candle.low <= tp1
                tp2_hit = candle.low <= tp2

            if tp1_hit:
                trade["tp1_hit"] = True

            if sl_hit and (tp2_hit or tp1_hit):
                trade["status"] = "ambiguous_ohlc"
                trade["outcome_ts"] = candle.ts
                trade["outcome_note"] = "La stessa candela 15m ha toccato stop e target: ordine intrabar non determinabile."
                changed.append(trade)
                break

            if sl_hit:
                trade["status"] = "sl"
                trade["outcome_ts"] = candle.ts
                trade["outcome_note"] = "Stop raggiunto sulla candela 15m."
                changed.append(trade)
                break

            if tp2_hit:
                trade["tp2_hit"] = True
                trade["status"] = "tp2"
                trade["outcome_ts"] = candle.ts
                trade["outcome_note"] = "Target 2 raggiunto sulla candela 15m."
                changed.append(trade)
                break

            trade["last_checked_ts"] = candle.ts

        if trade.get("status") == "open":
            trade["last_checked_ts"] = max(
                int(trade.get("last_checked_ts", entry_ts)),
                max((c.ts for c in candles if c.ts > entry_ts), default=entry_ts),
            )

    return changed


def open_trades(state: dict) -> List[dict]:
    return [t for t in _ensure(state) if t.get("status") == "open"]


def recent_trades(state: dict, limit: int = 20) -> List[dict]:
    return _ensure(state)[-limit:]
