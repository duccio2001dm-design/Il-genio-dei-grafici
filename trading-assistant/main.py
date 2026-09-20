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
        except Exception:
            pass
    return {"last_alerts": {}, "last_summary": 0}


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


def handle_commands(cfg: dict, state: dict, bot: notifier.Telegram) -> bool:
    """Legge i comandi mandati al bot. Restituisce True se hai chiesto un riepilogo."""
    commands, new_offset = bot.poll_commands(state.get("update_offset", 0))
    state["update_offset"] = new_offset
    if not commands:
        return False

    log(f"comandi ricevuti: {commands}")
    if any(c in ("/stato", "/start", "/status", "/riepilogo") for c in commands):
        return True
    if any(c == "/aiuto" or c == "/help" for c in commands):
        bot.send(
            "<b>Comandi</b>\n"
            "/stato — riepilogo di BTCUSD e XAUUSD\n\n"
            "<i>Le risposte arrivano al ciclo successivo, quindi entro un quarto d'ora.</i>"
        )
    return False


def run_cycle(cfg: dict, state: dict, bot: notifier.Telegram, demo: bool = False) -> None:
    snapshots: List[analysis.Snapshot] = []
    closed_markets: List[str] = []
    account = cfg.get("account", {})
    currency = account.get("currency", "USD")
    summary_requested = False if demo else handle_commands(cfg, state, bot)
    min_score = cfg.get("min_score", 60)
    cooldown = cfg.get("cooldown_minutes", 90)

    for symbol_cfg in cfg["symbols"]:
        name = symbol_cfg["name"]

        if symbol_cfg.get("market") == "forex" and not demo and not datafeed.forex_is_open():
            closed_markets.append(name)
            log(f"{name}: mercato chiuso, salto")
            continue

        try:
            frames = demo_frames(symbol_cfg) if demo else datafeed.load_frames(symbol_cfg, cfg.get("api_keys", {}))
        except datafeed.DataError as exc:
            log(f"{name}: dati non disponibili — {exc}")
            continue

        snapshot = analysis.analyze(symbol_cfg, frames, account)
        snapshots.append(snapshot)
        log(f"{name}: prezzo {snapshot.price:.2f}, {len(snapshot.signals)} condizioni rilevate")

        if in_quiet_hours(cfg):
            continue

        for signal in snapshot.signals:
            if signal.score < min_score:
                continue
            if not should_alert(state, name, signal, cooldown):
                continue
            text = notifier.format_alert(snapshot, signal, symbol_cfg, currency)
            if bot.send(text):
                mark_alert(state, name, signal)
                log(f"{name}: inviato avviso '{signal.key}' ({signal.score}/100)")
            break  # un solo avviso per strumento per ciclo: la sobrieta' e' una funzionalita'

    summary_every = cfg.get("summary_every_minutes", 240)
    due = (time.time() - state.get("last_summary", 0)) >= summary_every * 60
    if snapshots and (summary_requested or (due and not in_quiet_hours(cfg))):
        if bot.send(notifier.format_summary(snapshots, closed_markets)):
            state["last_summary"] = time.time()
            log("riepilogo inviato")


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

    if args.once or args.demo:
        run_cycle(cfg, state, bot, demo=args.demo)
        if not args.demo:
            save_state(args.state, state)
        return

    interval = cfg.get("poll_seconds", 300)
    log(f"avvio servizio, ciclo ogni {interval}s")
    while True:
        try:
            run_cycle(cfg, state, bot)
            save_state(args.state, state)
        except KeyboardInterrupt:
            log("interrotto")
            return
        except Exception:
            log("errore nel ciclo:\n" + traceback.format_exc())
        time.sleep(interval)


EXAMPLE_CONFIG = """
{
  "telegram": {"bot_token": "", "chat_id": ""},
  "api_keys": {"twelvedata": ""},
  "poll_seconds": 300,
  "summary_every_minutes": 240,
  "quiet_hours_utc": [],
  "min_score": 60,
  "cooldown_minutes": 90,
  "account": {"equity": 1000, "risk_pct": 1.0, "currency": "USD"},
  "symbols": [
    {"name": "BTCUSD", "source": "binance", "api_symbol": "BTCUSDT",
     "digits": 1, "contract_size": 1, "unit": "BTC", "market": "24/7"},
    {"name": "XAUUSD", "source": "twelvedata", "api_symbol": "XAU/USD",
     "digits": 2, "contract_size": 100, "unit": "oncia", "market": "forex"}
  ]
}
"""


if __name__ == "__main__":
    main()
