"""
Assistente di analisi per BTCUSD e XAUUSD.

Uso tipico:
    python main.py --demo          prova tutto con dati finti, senza chiavi
    python main.py --dry-run       dati veri, messaggi stampati a schermo
    python main.py --once          un singolo ciclo (per cron o GitHub Actions)
    python main.py                 servizio continuo

Cosa fa a ogni ciclo:
    1. scarica le candele chiuse su 15m, 1h e 4h;
    2. costruisce lo stato tecnico e cerca condizioni notevoli;
    3. manda un messaggio solo se qualcosa supera la soglia e non e' gia' stato
       segnalato di recente;
    4. ogni tot ore manda comunque un riepilogo.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Dict, List

import analysis
import datafeed
import notifier
import journal
import oracle
from datafeed import Candle

DEFAULT_CONFIG = "config.json"
DEFAULT_STATE = "state.json"


# --------------------------------------------------------------------------
# Configurazione e stato
# --------------------------------------------------------------------------


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        sys.exit(f"Configurazione non trovata: {path}\nCopia config.example.json in config.json e compilalo.")
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    # Le variabili d'ambiente hanno la precedenza: comodo su server e CI,
    # ed evita di tenere token dentro un file che potresti versionare per sbaglio.
    env_map = {
        "TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
        "TELEGRAM_CHAT_ID": ("telegram", "chat_id"),
        "TWELVEDATA_API_KEY": ("api_keys", "twelvedata"),
    }
    for env_name, (section, key) in env_map.items():
        value = os.environ.get(env_name)
        if value:
            cfg.setdefault(section, {})[key] = value

    return cfg


def load_state(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            raise RuntimeError(
                f"Stato NEXUS non leggibile ({path}): {exc}. "
                "Non sovrascrivo il Journal con uno stato vuoto."
            ) from exc
    return {"last_alerts": {}, "last_summary": 0, "trade_journal": [], "signal_audit": [], "pending_notices": []}


def save_state(path: str, state: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Filtri
# --------------------------------------------------------------------------


def in_quiet_hours(cfg: dict) -> bool:
    quiet = cfg.get("quiet_hours_utc") or []
    return datetime.now(timezone.utc).hour in quiet


def should_alert(state: dict, symbol: str, signal: analysis.Signal, cooldown_minutes: int) -> bool:
    key = f"{symbol}:{signal.key}"
    last = state["last_alerts"].get(key, 0)
    return (time.time() - last) >= cooldown_minutes * 60


def mark_alert(state: dict, symbol: str, signal: analysis.Signal) -> None:
    state["last_alerts"][f"{symbol}:{signal.key}"] = time.time()


# --------------------------------------------------------------------------
# Ciclo
# --------------------------------------------------------------------------


def handle_commands(cfg: dict, state: dict, bot: notifier.Telegram) -> set:
    """
    Legge i comandi Telegram arrivati dall'ultimo ciclo.
    /aiuto e /report rispondono subito; /stato e /controlla chiedono il riepilogo,
    che parte a fine ciclo con i dati appena calcolati.
    """
    commands, new_offset = bot.poll_commands(state.get("update_offset", 0))
    state["update_offset"] = new_offset
    if not commands:
        return set()

    log(f"comandi ricevuti: {commands}")
    requests = set()
    for command in commands:
        parts = command.split()
        name = parts[0]
        if name in ("/stato", "/controlla"):
            requests.add("stato")
        elif name in ("/oracolo", "/oracle"):
            requests.add("oracolo")
        elif name == "/report":
            days = None
            if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > 0:
                days = int(parts[1])
            bot.send(notifier.format_report(state.get("trade_journal", []), days))
        elif name in ("/aiuto", "/help", "/start"):
            bot.send(notifier.HELP_TEXT)
        else:
            bot.send(f"Comando {notifier.html.escape(name)} non riconosciuto. Scrivi /aiuto per l'elenco.")
    return requests


def queue_or_send(state: dict, bot: notifier.Telegram, text: str, quiet: bool) -> None:
    """Gli aggiornamenti sui trade non si perdono: in orario di silenzio aspettano."""
    if quiet:
        state.setdefault("pending_notices", []).append(text)
        return
    if not bot.send(text):
        state.setdefault("pending_notices", []).append(text)


def flush_pending(state: dict, bot: notifier.Telegram) -> None:
    pending = state.get("pending_notices") or []
    if not pending:
        return
    pending = pending[-20:]  # oltre non ha senso: sono notizie vecchie
    text = "🕘 <b>Aggiornamenti arrivati durante il silenzio</b>\n\n" + "\n\n".join(pending)
    if bot.send(text):
        state["pending_notices"] = []
    else:
        state["pending_notices"] = pending


def _record_signal_audit(state: dict, symbol: str, signal: analysis.Signal, candle_ts: int,
                         snapshot: analysis.Snapshot, decision: str, detail: str = "") -> None:
    """Registra segnali inviati e scartati, senza modificare il punteggio."""
    atr15 = snapshot.views["15m"].atr
    plan = signal.plan
    event = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "candle_ts": int(candle_ts), "symbol": symbol, "key": signal.key,
        "title": signal.title, "direction": signal.direction, "score": int(signal.score),
        "decision": decision, "detail": detail, "price": snapshot.price,
        "atr_15m": atr15, "ema20_15m": snapshot.views["15m"].ema20,
        "candle_range_atr": state.get("_current_candle_range_atr"),
        "data_age_minutes": state.get("_current_data_age_minutes"),
        "reasons": list(signal.reasons),
        "plan": ({"entry": plan.reference, "sl": plan.invalidation,
                  "tp1": plan.target_1r, "tp2": plan.target_2r,
                  "structural_target": plan.structural_target, "warning": plan.warning}
                 if plan else None),
    }
    audit = state.setdefault("signal_audit", [])
    audit.append(event)
    if len(audit) > 1000:
        del audit[:-1000]
    log(f"{symbol}: audit {signal.key} score={signal.score} decision={decision}" + (f" ({detail})" if detail else ""))


def run_cycle(cfg: dict, state: dict, bot: notifier.Telegram, demo: bool = False) -> None:
    snapshots: List[analysis.Snapshot] = []
    closed_markets: List[str] = []
    health = state.setdefault("health", {})
    health["last_cycle_started_at"] = datetime.now(timezone.utc).isoformat()
    health["symbols"] = health.get("symbols", {})
    account = cfg.get("account", {})
    currency = account.get("currency", "USD")
    command_requests = set() if demo else handle_commands(cfg, state, bot)
    if not demo and not in_quiet_hours(cfg):
        flush_pending(state, bot)
    oracle_cfg = cfg.get("oracle", {})
    oracle_threshold = float(oracle_cfg.get("lean_threshold", 0.03))
    oracle_model = oracle.load_model() if oracle_cfg.get("enabled", True) else None
    forecasts: Dict[str, dict] = {}
    min_score = cfg.get("min_score", 60)  # soglia operativa invariata
    min_info_score = cfg.get("min_info_score", 45)  # warning informativi separati
    max_data_age = cfg.get("max_data_age_minutes", 30)
    cooldown = cfg.get("cooldown_minutes", 90)

    for symbol_cfg in cfg["symbols"]:
        name = symbol_cfg["name"]

        if symbol_cfg.get("market") == "forex" and not demo and not datafeed.forex_is_open():
            closed_markets.append(name)
            health["symbols"][name] = {"status": "market_closed", "checked_at": datetime.now(timezone.utc).isoformat()}
            log(f"{name}: mercato chiuso, salto")
            continue

        try:
            frames = demo_frames(symbol_cfg) if demo else datafeed.load_frames(symbol_cfg, cfg.get("api_keys", {}))
        except datafeed.DataError as exc:
            health["symbols"][name] = {"status": "data_error", "checked_at": datetime.now(timezone.utc).isoformat(), "error": str(exc)}
            log(f"{name}: dati non disponibili — {exc}")
            continue

        snapshot = analysis.analyze(symbol_cfg, frames, account)
        snapshots.append(snapshot)
        last_candle = frames["15m"][-1]
        atr15 = snapshot.views["15m"].atr
        state["_current_candle_range_atr"] = ((last_candle.high - last_candle.low) / atr15) if atr15 else None
        data_age = max(0.0, (time.time() * 1000 - (last_candle.ts + 15 * 60 * 1000)) / 60000)
        state["_current_data_age_minutes"] = round(data_age, 1)
        stale_data = data_age > max_data_age
        health["symbols"][name] = {
            "status": "stale_data" if stale_data else "ok", "checked_at": datetime.now(timezone.utc).isoformat(),
            "last_candle_ts": int(last_candle.ts), "data_age_minutes": round(data_age, 1),
            "price": snapshot.price, "signals_detected": len(snapshot.signals),
        }
        if stale_data:
            log(f"{name}: dati obsoleti ({data_age:.1f} min), nuovi avvisi sospesi")
        log(f"{name}: prezzo {snapshot.price:.2f}, {len(snapshot.signals)} condizioni rilevate")

        # Prima di cercare nuovi segnali, aggiorna gli eventuali trade aperti
        # usando solo candele chiuse successive all'ingresso.
        if oracle_model and symbol_cfg.get("oracle"):
            try:
                closed = oracle.resolve(state, name, frames["15m"])
                forecast = None if stale_data else oracle.predict(frames["15m"], oracle_model)
                if forecast:
                    forecasts[name] = forecast
                    oracle.record(state, name, forecast, oracle_threshold)
                    h1, h4 = forecast["horizons"]["1h"], forecast["horizons"]["4h"]
                    log(f"{name}: oracolo 1h {h1['p_up']:.3f} 4h {h4['p_up']:.3f}" + (f", {closed} verificate" if closed else ""))
            except Exception:
                log(f"{name}: errore oracolo (il resto continua)\n" + traceback.format_exc())

        quiet = in_quiet_hours(cfg)
        for item in journal.update_from_candles(state, name, frames["15m"]):
            trade, event = item["trade"], item["event"]
            log(f"{name}: journal {trade['signal_key']} -> {event}" + (" (filtrato)" if trade.get("shadow") else ""))
            if not trade.get("shadow") and not demo:
                queue_or_send(state, bot, notifier.format_trade_event(trade, event, snapshot.digits), quiet)

        sent_kinds = {"operational": False, "informational": False}
        candle_ts = int(frames["15m"][-1].ts)
        for signal in sorted(snapshot.signals, key=lambda item: (0 if item.key == "overextension" else 1, -item.score)):
            kind = "operational" if signal.direction in ("long", "short") else "informational"
            if stale_data:
                decision, detail = "stale_data", f"dati piu' vecchi di {max_data_age} minuti"
            elif quiet:
                decision, detail = "quiet_hours", "orario di silenzio UTC"
            elif kind == "operational" and signal.plan is None:
                decision, detail = "no_plan", "segnale direzionale senza piano valido"
            elif kind == "operational" and signal.score < min_score:
                decision, detail = "below_min_score", f"soglia operativa {min_score}"
            elif (kind == "operational" and symbol_cfg.get("require_trend_alignment")
                  and not analysis.trend_aligned(snapshot, signal.direction)):
                decision, detail = "trend_filter", "1h e 4h non entrambi nella direzione del segnale"
                # Non inviato, ma seguito in silenzio per verificare il filtro.
                if should_alert(state, name, signal, cooldown):
                    mark_alert(state, name, signal)
                    journal.register_signal(state, name, signal, candle_ts, shadow=True)
            elif kind == "informational" and signal.score < min_info_score:
                decision, detail = "below_min_info_score", f"soglia informativa {min_info_score}"
            elif not should_alert(state, name, signal, cooldown):
                decision, detail = "cooldown", f"cooldown {cooldown} minuti"
            elif sent_kinds[kind]:
                decision, detail = "one_alert_per_kind", "avviso della stessa categoria gia' inviato nel ciclo"
            else:
                oracle_line = (notifier.format_oracle_line(forecasts[name], signal.direction, oracle_threshold)
                               if kind == "operational" and name in forecasts else None)
                text = notifier.format_alert(snapshot, signal, symbol_cfg, currency, oracle_line)
                if bot.send(text):
                    mark_alert(state, name, signal)
                    sent_kinds[kind] = True
                    if kind == "operational" and journal.register_signal(state, name, signal, candle_ts):
                        log(f"{name}: registrato trade journal '{signal.key}' ({signal.score}/100)")
                    decision, detail = "sent", "invio Telegram confermato"
                else:
                    decision, detail = "send_failed", "Telegram non ha confermato l'invio"
            _record_signal_audit(state, name, signal, candle_ts, snapshot, decision, detail)
        state.pop("_current_candle_range_atr", None)
        state.pop("_current_data_age_minutes", None)

    summary_every = cfg.get("summary_every_minutes", 240)
    due = (time.time() - state.get("last_summary", 0)) >= summary_every * 60
    score = state.get("oracle", {}).get("score", {})
    if "oracolo" in command_requests:
        if not oracle_model:
            bot.send("🔮 Oracolo non disponibile: manca il file oracle_model.json.")
        elif not forecasts:
            bot.send("🔮 Nessuna previsione in questo momento (dati non disponibili o troppo vecchi). Riprova tra poco.")
        for sym, forecast in forecasts.items():
            digits = next((s_["digits"] for s_ in cfg["symbols"] if s_["name"] == sym), 2)
            bot.send(notifier.format_oracle(sym, forecast, oracle_model, score, digits, oracle_threshold))

    if snapshots and ("stato" in command_requests or (due and not in_quiet_hours(cfg))):
        blocks = {sym: notifier.format_oracle(sym, fc, oracle_model, score, snap.digits, oracle_threshold, compact=True)
                  for sym, fc in forecasts.items() for snap in snapshots if snap.symbol == sym}
        if bot.send(notifier.format_summary(snapshots, closed_markets, blocks)):
            state["last_summary"] = time.time()
            log("riepilogo inviato")
    health["last_cycle_completed_at"] = datetime.now(timezone.utc).isoformat()
    health["last_cycle_status"] = "partial" if any(item.get("status") in ("data_error", "stale_data") for item in health["symbols"].values()) else "ok"


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


# --------------------------------------------------------------------------
# Dati finti per la modalita' demo
# --------------------------------------------------------------------------


def demo_frames(symbol_cfg: dict) -> Dict[str, List[Candle]]:
    """Genera una serie plausibile (random walk con trend e volatilita' variabile)."""
    random.seed(hash(symbol_cfg["name"]) & 0xFFFF)
    price = 64000.0 if "BTC" in symbol_cfg["name"] else 2400.0
    vol = price * 0.0015
    now = int(time.time() * 1000)
    step = 15 * 60 * 1000

    candles: List[Candle] = []
    drift = 0.0
    for i in range(1200):
        drift += random.gauss(0, vol * 0.05)
        drift *= 0.97
        open_price = price
        price = max(price + drift + random.gauss(0, vol), price * 0.5)
        high = max(open_price, price) + abs(random.gauss(0, vol * 0.6))
        low = min(open_price, price) - abs(random.gauss(0, vol * 0.6))
        candles.append(
            Candle(now - (1200 - i) * step, open_price, high, low, price, abs(random.gauss(100, 30)))
        )

    return {
        "15m": candles,
        "1h": datafeed.resample(candles, "1h"),
        "4h": datafeed.resample(candles, "4h"),
    }


# --------------------------------------------------------------------------
# Avvio
# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Assistente di analisi BTCUSD / XAUUSD")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--once", action="store_true", help="esegue un solo ciclo ed esce")
    parser.add_argument("--dry-run", action="store_true", help="stampa i messaggi invece di inviarli")
    parser.add_argument("--demo", action="store_true", help="usa dati finti, nessuna chiave richiesta")
    parser.add_argument("--chat-id", action="store_true", help="stampa il chat_id Telegram e esce")
    args = parser.parse_args()

    if args.demo and not os.path.exists(args.config):
        cfg = json.loads(EXAMPLE_CONFIG)
    else:
        cfg = load_config(args.config)

    if args.chat_id:
        notifier.resolve_chat_id(cfg["telegram"]["bot_token"])
        return

    bot = notifier.Telegram(
        cfg.get("telegram", {}).get("bot_token", ""),
        cfg.get("telegram", {}).get("chat_id", ""),
        dry_run=args.dry_run or args.demo,
    )
    state = load_state(args.state)
    state.setdefault("trade_journal", [])
    migrated = journal.migrate_legacy_metrics(state)
    if migrated:
        log(f"Journal: metriche MFE/MAE in R normalizzate su {migrated} record")

    if args.once or args.demo:
        try:
            run_cycle(cfg, state, bot, demo=args.demo)
        finally:
            if not args.demo:
                save_state(args.state, state)
        return

    interval = cfg.get("poll_seconds", 300)
    log(f"avvio servizio, ciclo ogni {interval}s")
    while True:
        try:
            run_cycle(cfg, state, bot)
        except KeyboardInterrupt:
            log("interrotto")
            return
        except Exception:
            log("errore nel ciclo:\n" + traceback.format_exc())
        finally:
            try:
                save_state(args.state, state)
            except Exception:
                log("errore nel salvataggio dello stato:\n" + traceback.format_exc())
        time.sleep(interval)

EXAMPLE_CONFIG = """
{
  "telegram": {"bot_token": "", "chat_id": ""},
  "api_keys": {"twelvedata": ""},
  "poll_seconds": 300,
  "summary_every_minutes": 240,
  "quiet_hours_utc": [],
  "min_score": 60,
  "min_info_score": 45,
  "cooldown_minutes": 90,
  "oracle": {"enabled": true, "lean_threshold": 0.03},
  "account": {"equity": 1000, "risk_pct": 1.0, "currency": "USD"},
  "symbols": [
    {"name": "BTCUSD", "source": "coinbase", "api_symbol": "BTC-USD",
     "digits": 1, "contract_size": 1, "unit": "BTC", "market": "24/7", "oracle": true},
    {"name": "XAUUSD", "source": "twelvedata", "api_symbol": "XAU/USD",
     "digits": 2, "contract_size": 100, "unit": "oncia", "market": "forex",
     "require_trend_alignment": true, "stop_atr_buffer": 0.5}
  ]
}
"""


if __name__ == "__main__":
    main()
