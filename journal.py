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


def register_signal(state: dict, symbol: str, signal, candle_ts: int, shadow: bool = False) -> bool:
    """
    Registra un alert operativo una sola volta per stessa entry/strategia.

    shadow=True: segnale scartato da un filtro e NON inviato. Lo seguiamo lo
    stesso, senza notifiche, per poter verificare se il filtro sta aiutando.
    """
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
        "risk_per_unit": plan.risk_per_unit,
        "mfe_units": 0.0,
        "mae_units": 0.0,
        "mfe_r": 0.0,
        "mae_r": 0.0,
        "tp1_ts": None,
        "last_checked_ts": candle_ts,
        "outcome_ts": None,
        "outcome_note": None,
        "shadow": bool(shadow),
        "be_hit": False,
        "managed_r": None,
    })
    return True


def migrate_legacy_metrics(state: dict) -> int:
    """Aggiunge metriche in R ai record storici usando entry/stop già salvati."""
    migrated = 0
    for trade in _ensure(state):
        try:
            risk = abs(float(trade["entry"]) - float(trade["sl"]))
            if risk <= 0:
                continue
            trade.setdefault("risk_per_unit", risk)
            trade.setdefault("mfe_r", max(0.0, float(trade.get("mfe_units", 0.0))) / risk)
            trade.setdefault("mae_r", max(0.0, float(trade.get("mae_units", 0.0))) / risk)
            # Il timestamp TP1 storico non e' ricostruibile senza le candele originali.
            trade.setdefault("tp1_ts", None)
            trade.setdefault("shadow", False)
            trade.setdefault("be_hit", False)
            if trade.get("managed_r") is None and trade.get("status") in ("sl", "tp2"):
                trade["managed_r"], estimated = _managed_from_status(trade)
                if estimated:
                    trade["managed_estimated"] = True
            migrated += 1
        except (KeyError, TypeError, ValueError):
            continue
    return migrated


# Gestione suggerita: meta' posizione chiusa a TP1 (1R), stop del resto a pareggio.
MANAGED_TP2 = 0.5 * 1.0 + 0.5 * 2.0   # 1,5R
MANAGED_BE = 0.5 * 1.0                # 0,5R
MANAGED_SL = -1.0


def _managed_from_status(trade: dict) -> tuple:
    """Esito 'meta' a TP1 + pareggio' ricostruito per i record senza storico candele."""
    if trade.get("status") == "sl":
        # stop preso dopo TP1: il prezzo e' per forza ripassato dal pareggio prima
        return (MANAGED_BE if trade.get("tp1_hit") else MANAGED_SL), False
    if trade.get("status") == "tp2":
        # non sappiamo se nel mezzo sia tornato al pareggio: stima ottimistica
        return MANAGED_TP2, True
    return None, False


def base_r(trade: dict):
    """Esito tenendo tutta la posizione fino a stop o TP2."""
    return {"sl": -1.0, "tp2": 2.0}.get(trade.get("status"))


def _excursion(trade: dict, high: float, low: float) -> tuple[float, float]:
    entry = float(trade["entry"])
    if trade["direction"] == "long":
        return high - entry, entry - low
    return entry - low, high - entry


def update_from_candles(state: dict, symbol: str, candles: list) -> List[dict]:
    """
    Aggiorna i trade aperti usando solo candele chiuse successive all'entry.

    Restituisce gli eventi nuovi: {"trade": ..., "event": "tp1"|"be"|"tp2"|"sl"|"ambiguous"}.
    """
    journal = _ensure(state)
    events: List[dict] = []

    for trade in journal:
        if trade.get("symbol") != symbol or trade.get("status") != "open":
            continue

        entry_ts = int(trade["entry_ts"])
        entry = float(trade["entry"])
        direction = trade["direction"]
        sl = float(trade["sl"])
        tp1 = float(trade["tp1"])
        tp2 = float(trade["tp2"])

        risk_per_unit = abs(float(trade.get("risk_per_unit", entry - sl)))
        if risk_per_unit <= 0:
            risk_per_unit = abs(entry - sl)

        for candle in sorted(candles, key=lambda item: item.ts):
            if candle.ts <= entry_ts or candle.ts <= int(trade.get("last_checked_ts", entry_ts)):
                continue

            mfe, mae = _excursion(trade, candle.high, candle.low)
            trade["mfe_units"] = max(float(trade.get("mfe_units", 0.0)), mfe)
            trade["mae_units"] = max(float(trade.get("mae_units", 0.0)), mae)
            trade["mfe_r"] = max(float(trade.get("mfe_r", 0.0)), max(0.0, float(trade["mfe_units"])) / risk_per_unit)
            trade["mae_r"] = max(float(trade.get("mae_r", 0.0)), max(0.0, float(trade["mae_units"])) / risk_per_unit)

            if direction == "long":
                sl_hit = candle.low <= sl
                tp1_hit = candle.high >= tp1
                tp2_hit = candle.high >= tp2
                back_to_entry = candle.low <= entry
            else:
                sl_hit = candle.high >= sl
                tp1_hit = candle.low <= tp1
                tp2_hit = candle.low <= tp2
                back_to_entry = candle.high >= entry

            tp1_before = bool(trade.get("tp1_hit"))

            # Ritorno al pareggio dopo TP1: conta solo dalla candela successiva a
            # quella di TP1, perche' dentro la stessa candela l'ordine non e' noto.
            if tp1_before and not trade.get("be_hit") and back_to_entry:
                trade["be_hit"] = True
                trade["be_ts"] = candle.ts
                if trade.get("managed_r") is None:
                    trade["managed_r"] = MANAGED_BE
                events.append({"trade": trade, "event": "be"})

            if sl_hit and (tp2_hit or (tp1_hit and not tp1_before)):
                trade["status"] = "ambiguous_ohlc"
                trade["outcome_ts"] = candle.ts
                trade["outcome_note"] = "La stessa candela 15m ha toccato stop e target: ordine intrabar non determinabile."
                if tp1_hit and not tp1_before:
                    trade["tp1_hit"] = True
                    trade["tp1_ts"] = candle.ts
                events.append({"trade": trade, "event": "ambiguous"})
                break

            if tp1_hit and not tp1_before:
                trade["tp1_hit"] = True
                trade["tp1_ts"] = candle.ts
                events.append({"trade": trade, "event": "tp1"})

            if sl_hit:
                trade["status"] = "sl"
                trade["outcome_ts"] = candle.ts
                trade["outcome_note"] = "Stop raggiunto sulla candela 15m."
                if trade.get("managed_r") is None:
                    trade["managed_r"] = MANAGED_BE if tp1_before else MANAGED_SL
                events.append({"trade": trade, "event": "sl"})
                break

            if tp2_hit:
                trade["tp2_hit"] = True
                trade["status"] = "tp2"
                trade["outcome_ts"] = candle.ts
                trade["outcome_note"] = "Target 2 raggiunto sulla candela 15m."
                if trade.get("managed_r") is None:
                    trade["managed_r"] = MANAGED_TP2
                events.append({"trade": trade, "event": "tp2"})
                break

            trade["last_checked_ts"] = candle.ts

        if trade.get("status") == "open":
            trade["last_checked_ts"] = max(
                int(trade.get("last_checked_ts", entry_ts)),
                max((c.ts for c in candles if c.ts > entry_ts), default=entry_ts),
            )

    return events


def stats(trades: List[dict]) -> dict:
    """Numeri riassuntivi di un gruppo di trade chiusi e aperti."""
    closed = [t for t in trades if t.get("status") in ("sl", "tp2")]
    base = [base_r(t) for t in closed]
    managed = [t["managed_r"] for t in closed if t.get("managed_r") is not None]
    return {
        "n": len(trades),
        "open": sum(1 for t in trades if t.get("status") == "open"),
        "closed": len(closed),
        "tp2": sum(1 for t in closed if t["status"] == "tp2"),
        "sl": sum(1 for t in closed if t["status"] == "sl"),
        "ambiguous": sum(1 for t in trades if t.get("status") == "ambiguous_ohlc"),
        "tp1_rate": (sum(1 for t in closed if t.get("tp1_hit")) / len(closed)) if closed else None,
        "base_r": sum(base),
        "managed_r": sum(managed),
    }


def open_trades(state: dict) -> List[dict]:
    return [t for t in _ensure(state) if t.get("status") == "open"]


def recent_trades(state: dict, limit: int = 20) -> List[dict]:
    return _ensure(state)[-limit:]
