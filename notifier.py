"""
Composizione dei messaggi e invio su Telegram.

I messaggi direzionali arrivano in formato operativo: COMPRA / VENDI con ingresso,
stop loss e due take profit gia' calcolati. Stop e target non sono numeri inventati:
lo stop sta oltre l'ultimo minimo (o massimo) di struttura piu' un margine di ATR,
i target sono multipli della distanza dello stop.

Le condizioni senza direzione (compressione, livelli, estensione) restano
informative: dicono "guarda qui", non "fai questo".
"""

from __future__ import annotations

import html
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from analysis import Plan, Signal, Snapshot

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000

ACTION = {"long": ("🟢", "COMPRA"), "short": ("🔴", "VENDI")}


# --------------------------------------------------------------------------
# Formattazione numeri
# --------------------------------------------------------------------------


def fmt(value: Optional[float], digits: int) -> str:
    """Numero in formato italiano: punto per le migliaia, virgola per i decimali."""
    if value is None:
        return "n/d"
    return f"{value:,.{digits}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_pct(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "n/d"
    return f"{value:.{digits}f}".replace(".", ",") + "%"


def fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "n/d"
    return f"{value:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")


# --------------------------------------------------------------------------
# Blocchi del messaggio
# --------------------------------------------------------------------------


def _operational_block(plan: Plan, digits: int, symbol_cfg: dict, currency: str) -> List[str]:
    unit = symbol_cfg.get("unit", "unita'")
    lines = [
        "<b>━━ PIANO OPERATIVO ━━</b>",
        f"Ingresso di riferimento: <b>{fmt(plan.reference, digits)}</b>",
        f"Stop loss: <b>{fmt(plan.invalidation, digits)}</b>  (distanza {fmt(plan.risk_per_unit, digits)} per {unit})",
        f"Target 1: <b>{fmt(plan.target_1r, digits)}</b>  (1R)",
        f"Target 2: <b>{fmt(plan.target_2r, digits)}</b>  (2R)",
    ]

    lines.append(
        f"Gestione: a Target 1 chiudi meta' e sposta lo stop a pareggio ({fmt(plan.reference, digits)})"
    )

    if plan.structural_target is not None:
        lines.append(f"Resistenza/ostacolo chiave: {fmt(plan.structural_target, digits)}")

    if plan.size_units is not None and plan.money_at_risk is not None:
        size_text = f"{plan.size_units:.4f} {unit}".replace(".", ",")
        if plan.size_lots is not None and symbol_cfg.get("contract_size", 1) != 1:
            size_text += f"  ≈ {plan.size_lots:.2f} lotti".replace(".", ",")
        lines.append(f"Dimensione: {size_text}")
        lines.append(f"Rischio massimo: <b>{fmt_money(plan.money_at_risk)} {currency}</b>")

    if plan.warning:
        lines.append(f"⚠️ {html.escape(plan.warning)}")

    return lines


def format_alert(snapshot: Snapshot, signal: Signal, symbol_cfg: dict, currency: str = "USD") -> str:
    digits = snapshot.digits
    lines: List[str] = []

    if signal.direction in ACTION and signal.plan:
        icon, verb = ACTION[signal.direction]
        lines += [
            f"{icon} <b>{verb} {html.escape(snapshot.symbol)}</b>  ·  confluenza <b>{signal.score}/100</b>",
            f"<i>{html.escape(signal.title)}</i>",
            "",
        ]
        lines += _operational_block(signal.plan, digits, symbol_cfg, currency)
        lines.append("")
    else:
        lines += [
            f"🔵 <b>{html.escape(snapshot.symbol)} · {html.escape(signal.title)}</b>",
            f"Prezzo {fmt(snapshot.price, digits)}  ·  da osservare, nessuna direzione chiara",
            "",
        ]

    lines.append("<b>Perché il sistema segnala questo setup</b>")
    lines += [f"• {html.escape(r)}" for r in signal.reasons]

    lines += ["", "<b>Contesto multi-timeframe</b>"]
    for tf in ("4h", "1h", "15m"):
        lines.append(f"{tf}: {html.escape(snapshot.views[tf].describe())}")

    supports = [l for l in snapshot.levels if l.kind == "supporto"]
    resistances = [l for l in snapshot.levels if l.kind == "resistenza"]
    if supports or resistances:
        lines += ["", "<b>Livelli da monitorare</b>"]
        if resistances:
            lines.append("Sopra: " + " · ".join(fmt(l.price, digits) for l in resistances))
        if supports:
            lines.append("Sotto: " + " · ".join(fmt(l.price, digits) for l in supports))

    for note in snapshot.notes:
        lines.append(f"\n⚠️ {html.escape(note)}")

    lines += [
        "",
        "<i>Livelli calcolati sulla struttura di prezzo. Una parte di questi segnali sara' sbagliata "
        "anche con confluenza alta: lo stop loss e' la parte che conta davvero. Controlla sul grafico "
        "e sul calendario macro prima di aprire.</i>",
    ]
    return "\n".join(lines)


def format_summary(snapshots: List[Snapshot], closed_markets: List[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC")
    lines = [f"📊 <b>Riepilogo mercato</b> · {now}", ""]

    for snapshot in snapshots:
        digits = snapshot.digits
        lines.append(f"<b>{html.escape(snapshot.symbol)}</b> — {fmt(snapshot.price, digits)}")
        for tf in ("4h", "1h", "15m"):
            lines.append(f"  {tf}: {html.escape(snapshot.views[tf].describe())}")

        v15 = snapshot.views["15m"]
        if v15.atr is not None:
            lines.append(f"  Volatilita' 15m: ATR {fmt(v15.atr, digits)} ({fmt_pct(v15.atr_pct)} del prezzo)")

        resistances = [l for l in snapshot.levels if l.kind == "resistenza"]
        supports = [l for l in snapshot.levels if l.kind == "supporto"]
        if resistances:
            lines.append("  Sopra: " + " · ".join(fmt(l.price, digits) for l in resistances))
        if supports:
            lines.append("  Sotto: " + " · ".join(fmt(l.price, digits) for l in supports))

        if snapshot.signals:
            top = snapshot.signals[0]
            if top.direction in ACTION:
                icon, verb = ACTION[top.direction]
                lines.append(f"  {icon} Inclinazione attuale: {verb} ({top.score}/100, {html.escape(top.title)})")
            else:
                lines.append(f"  🔵 Da osservare: {html.escape(top.title)} ({top.score}/100)")
        else:
            lines.append("  Nessuna condizione degna di nota adesso")

        for note in snapshot.notes:
            lines.append(f"  ⚠️ {html.escape(note)}")
        lines.append("")

    for market in closed_markets:
        lines.append(f"🌙 {html.escape(market)}: mercato chiuso, nessun dato aggiornato.")

    lines.append("<i>Fotografia dello stato tecnico, non una previsione.</i>")
    return "\n".join(lines)


EVENT_TEXT = {
    "tp1": ("🎯", "Target 1 raggiunto", "Chiudi meta' posizione e sposta lo stop a pareggio ({entry})."),
    "be": ("⚖️", "Tornato al pareggio dopo il Target 1", "Con la gestione suggerita la parte rimasta si chiude a zero: +0,5R in totale."),
    "tp2": ("✅", "Target 2 raggiunto", "Operazione completata."),
    "sl": ("🛑", "Stop loss raggiunto", "Operazione chiusa in perdita."),
    "ambiguous": ("❔", "Stop e target toccati nella stessa candela", "Dal grafico a 15 minuti non si capisce quale e' arrivato prima: controlla sulla tua piattaforma."),
}


def format_trade_event(trade: dict, event: str, digits: int) -> str:
    icon, title, hint = EVENT_TEXT[event]
    verb = ACTION.get(trade.get("direction"), ("", trade.get("direction", "")))[1]
    opened = datetime.fromtimestamp(int(trade["entry_ts"]) / 1000, timezone.utc).strftime("%d/%m %H:%M UTC")
    if event == "sl" and trade.get("tp1_hit"):
        hint = "Il Target 1 era gia' stato toccato: con la gestione suggerita eri a pareggio, +0,5R in totale."
    return "\n".join([
        f"{icon} <b>{html.escape(trade['symbol'])} · {title}</b>",
        f"{verb} da {fmt(float(trade['entry']), digits)} ({html.escape(trade.get('title', ''))}, {opened})",
        f"Stop {fmt(float(trade['sl']), digits)} · T1 {fmt(float(trade['tp1']), digits)} · T2 {fmt(float(trade['tp2']), digits)}",
        hint.format(entry=fmt(float(trade["entry"]), digits)),
    ])


def _fmt_r(value: float) -> str:
    return f"{value:+.1f}R".replace(".", ",")


def format_report(journal_trades: List[dict], days: Optional[int] = None) -> str:
    """Statistiche del registro per strumento e tipo di segnale."""
    import journal as jr

    trades = journal_trades
    title = "tutto lo storico"
    if days:
        cutoff = (time.time() - days * 86400) * 1000
        trades = [t for t in trades if int(t.get("entry_ts", 0)) >= cutoff]
        title = f"ultimi {days} giorni"

    sent = [t for t in trades if not t.get("shadow")]
    shadow = [t for t in trades if t.get("shadow")]
    lines = [f"📒 <b>Registro segnali</b> · {title}", ""]
    if not trades:
        lines.append("Nessun segnale registrato nel periodo.")
        return "\n".join(lines)

    def row(label: str, group: List[dict]) -> str:
        st = jr.stats(group)
        tp1 = f"{st['tp1_rate'] * 100:.0f}%" if st["tp1_rate"] is not None else "n/d"
        extra = f", {st['open']} aperti" if st["open"] else ""
        return (f"{label}: {st['closed']} chiusi ({st['tp2']}✅ {st['sl']}🛑{extra}) · "
                f"T1 {tp1} · intera {_fmt_r(st['base_r'])} · gestita {_fmt_r(st['managed_r'])}")

    lines.append("<b>" + html.escape(row("Totale", sent)) + "</b>")
    for symbol in sorted({t["symbol"] for t in sent}):
        group = [t for t in sent if t["symbol"] == symbol]
        lines += ["", "<b>" + html.escape(row(symbol, group)) + "</b>"]
        for key in sorted({t["signal_key"] for t in group}):
            lines.append("  " + html.escape(row(key, [t for t in group if t["signal_key"] == key])))

    if shadow:
        lines += ["", "<b>Scartati dal filtro di trend (non inviati)</b>",
                  html.escape(row("Filtrati", shadow)),
                  "<i>Se i filtrati vanno meglio degli inviati, il filtro sta facendo danni.</i>"]

    lines += ["",
              "<i>Intera = tutta la posizione fino a stop o T2. Gestita = meta' a T1 e stop a pareggio. "
              "Ingresso alla chiusura della candela, senza spread ne' commissioni: sul conto reale "
              "i numeri sono un po' peggiori. Servono molte decine di operazioni per ogni riga prima "
              "che dicano qualcosa.</i>"]
    return "\n".join(lines)


HELP_TEXT = "\n".join([
    "🤖 <b>Comandi</b>",
    "/stato — riepilogo di entrambi gli strumenti",
    "/controlla — come /stato",
    "/report — risultati di tutti i segnali registrati",
    "/report 30 — solo gli ultimi 30 giorni (o il numero che vuoi)",
    "/aiuto — questo elenco",
    "",
    "<i>La risposta arriva al ciclo successivo, di solito entro 5-10 minuti.</i>",
])


# --------------------------------------------------------------------------
# Invio e ricezione
# --------------------------------------------------------------------------


class Telegram:
    def __init__(self, token: str, chat_id: str, dry_run: bool = False):
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run

    def send(self, text: str) -> bool:
        if self.dry_run or not self.token or not self.chat_id:
            print("\n" + "=" * 62)
            print(_strip_html(text))
            print("=" * 62 + "\n")
            return True

        ok = True
        for chunk in _split(text, MAX_LEN):
            ok = self._send_chunk(chunk) and ok
        return ok

    def _send_chunk(self, text: str, attempts: int = 3) -> bool:
        url = TELEGRAM_API.format(token=self.token, method="sendMessage")
        payload = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode()

        for attempt in range(attempts):
            try:
                req = urllib.request.Request(url, data=payload)
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return json.loads(resp.read().decode()).get("ok", False)
            except urllib.error.HTTPError as exc:
                print(f"[telegram] HTTP {exc.code}: {exc.read()[:200]!r}")
                if exc.code in (400, 401, 403):
                    return False  # token o chat_id sbagliati: riprovare non serve
            except Exception as exc:
                print(f"[telegram] errore invio: {exc}")
            time.sleep(2 * (attempt + 1))
        return False

    def poll_commands(self, offset: int) -> Tuple[List[str], int]:
        """
        Legge i comandi che hai mandato al bot dall'ultimo giro.
        Su GitHub Actions la risposta arrivera' al ciclo successivo, quindi con
        un ritardo di qualche minuto: e' il prezzo dell'hosting gratuito.
        """
        if self.dry_run or not self.token:
            return [], offset

        url = TELEGRAM_API.format(token=self.token, method="getUpdates")
        query = urllib.parse.urlencode({"offset": offset, "timeout": 0, "allowed_updates": '["message"]'})
        commands: List[str] = []
        try:
            with urllib.request.urlopen(f"{url}?{query}", timeout=20) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            print(f"[telegram] lettura comandi fallita: {exc}")
            return [], offset

        for update in data.get("result", []):
            offset = max(offset, update["update_id"] + 1)
            text = (update.get("message") or {}).get("text", "")
            if text.startswith("/"):
                commands.append(text.split("@")[0].strip().lower())
        return commands, offset


def _split(text: str, limit: int) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def _strip_html(text: str) -> str:
    for tag in ("<b>", "</b>", "<i>", "</i>", "<code>", "</code>"):
        text = text.replace(tag, "")
    return html.unescape(text)


def resolve_chat_id(token: str) -> None:
    """Stampa il chat_id dopo che hai scritto un messaggio qualsiasi al bot."""
    url = TELEGRAM_API.format(token=token, method="getUpdates")
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read().decode())

    if not data.get("result"):
        print("Nessun messaggio trovato. Apri Telegram, scrivi qualcosa al bot e riprova.")
        return

    seen = {}
    for update in data["result"]:
        chat = (update.get("message") or update.get("channel_post") or {}).get("chat")
        if chat:
            seen[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name", "")
    for chat_id, name in seen.items():
        print(f"chat_id: {chat_id}   ({name})")
