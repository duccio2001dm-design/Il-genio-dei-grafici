"""
Motore di analisi: legge le candele, costruisce una fotografia del mercato su piu'
timeframe, individua condizioni tecniche notevoli e calcola livelli e rischio.

Filosofia di fondo, perche' cambia come leggi l'output:
gli indicatori descrivono quello che e' gia' successo. Questo modulo NON prevede
il prezzo e non calcola probabilita'. Il punteggio che vedrai e' una misura di
QUANTE condizioni tecniche sono allineate in quel momento, non di quanto e'
probabile che il trade vada bene. Serve a decidere cosa guardare, non cosa fare.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import indicators as ind
from datafeed import Candle

# --------------------------------------------------------------------------
# Strutture dati
# --------------------------------------------------------------------------


@dataclass
class TFView:
    """Fotografia di un singolo timeframe."""

    tf: str
    close: float
    ema20: Optional[float]
    ema50: Optional[float]
    ema200: Optional[float]
    rsi: Optional[float]
    macd_hist: Optional[float]
    macd_hist_prev: Optional[float]
    adx: Optional[float]
    plus_di: Optional[float]
    minus_di: Optional[float]
    atr: Optional[float]
    atr_pct: Optional[float]
    bb_width: Optional[float]
    bb_width_rank: Optional[float]
    trend: str
    trend_dir: int  # +1 rialzista, -1 ribassista, 0 laterale

    def describe(self) -> str:
        bits = [self.trend]
        if self.rsi is not None:
            bits.append(f"RSI {self.rsi:.0f}")
        if self.adx is not None:
            bits.append(f"ADX {self.adx:.0f}")
        return " · ".join(bits)


@dataclass
class Level:
    price: float
    kind: str  # 'supporto' | 'resistenza'
    source: str  # da dove arriva
    touches: int = 1


@dataclass
class Plan:
    """Livelli operativi derivati dalla struttura, non una raccomandazione."""

    direction: str  # 'long' | 'short'
    reference: float  # prezzo di riferimento (chiusura corrente)
    invalidation: float  # dove l'idea tecnica smette di valere
    risk_per_unit: float
    target_1r: float
    target_2r: float
    structural_target: Optional[float]
    size_units: Optional[float]
    size_lots: Optional[float]
    money_at_risk: Optional[float]
    warning: Optional[str] = None


@dataclass
class Signal:
    key: str
    title: str
    direction: str  # 'long' | 'short' | 'neutro'
    score: int
    reasons: List[str] = field(default_factory=list)
    plan: Optional[Plan] = None


@dataclass
class Snapshot:
    symbol: str
    price: float
    digits: int
    views: Dict[str, TFView]
    levels: List[Level]
    signals: List[Signal]
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Costruzione della vista per timeframe
# --------------------------------------------------------------------------


def build_view(candles: List[Candle], tf: str) -> TFView:
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    ema20 = ind.last(ind.ema(closes, 20))
    ema50 = ind.last(ind.ema(closes, 50))
    ema200 = ind.last(ind.ema(closes, 200)) if len(closes) >= 200 else None

    rsi_series = ind.rsi(closes, 14)
    _, _, hist = ind.macd(closes)
    plus_di, minus_di, adx_series = ind.adx(highs, lows, closes, 14)
    atr_series = ind.atr(highs, lows, closes, 14)
    _, _, _, bb_width = ind.bollinger(closes, 20, 2.0)

    close = closes[-1]
    atr_val = ind.last(atr_series)
    adx_val = ind.last(adx_series)

    # Direzione: allineamento delle medie + conferma della forza dell'ADX.
    bull = ema20 is not None and ema50 is not None and close > ema20 > ema50
    bear = ema20 is not None and ema50 is not None and close < ema20 < ema50
    if ema200 is not None:
        bull = bull and ema50 > ema200
        bear = bear and ema50 < ema200

    strong = adx_val is not None and adx_val >= 22
    if bull:
        trend, trend_dir = ("rialzista" if strong else "rialzista debole"), 1
    elif bear:
        trend, trend_dir = ("ribassista" if strong else "ribassista debole"), -1
    else:
        trend, trend_dir = "laterale", 0

    return TFView(
        tf=tf,
        close=close,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        rsi=ind.last(rsi_series),
        macd_hist=ind.last(hist),
        macd_hist_prev=ind.last(hist, 1),
        adx=adx_val,
        plus_di=ind.last(plus_di),
        minus_di=ind.last(minus_di),
        atr=atr_val,
        atr_pct=(100.0 * atr_val / close) if atr_val and close else None,
        bb_width=ind.last(bb_width),
        bb_width_rank=ind.percentile_rank(bb_width[-100:], ind.last(bb_width)),
        trend=trend,
        trend_dir=trend_dir,
    )


# --------------------------------------------------------------------------
# Livelli
# --------------------------------------------------------------------------


def find_levels(candles_1h: List[Candle], price: float, atr_val: float, max_each: int = 3) -> List[Level]:
    """Massimi e minimi di oscillazione sull'orario, raggruppati per vicinanza."""
    highs = [c.high for c in candles_1h]
    lows = [c.low for c in candles_1h]
    pivot_highs, pivot_lows = ind.swing_points(highs, lows, left=3, right=3)

    tolerance = max(atr_val * 0.6, price * 0.0008)
    raw: List[Level] = []
    for i in pivot_highs[-40:]:
        raw.append(Level(highs[i], "resistenza", "max orario"))
    for i in pivot_lows[-40:]:
        raw.append(Level(lows[i], "supporto", "min orario"))

    # massimo e minimo delle ultime 24 ore: livelli che tutti guardano
    if len(candles_1h) >= 24:
        window = candles_1h[-24:]
        raw.append(Level(max(c.high for c in window), "resistenza", "max 24h"))
        raw.append(Level(min(c.low for c in window), "supporto", "min 24h"))

    # raggruppa livelli vicini tra loro: piu' tocchi = livello piu' rilevante
    clustered: List[Level] = []
    for level in sorted(raw, key=lambda l: l.price):
        if clustered and abs(clustered[-1].price - level.price) <= tolerance:
            prev = clustered[-1]
            total = prev.touches + 1
            prev.price = (prev.price * prev.touches + level.price) / total
            prev.touches = total
            if "24h" in level.source:
                prev.source = level.source
        else:
            clustered.append(Level(level.price, level.kind, level.source))

    # riclassifica rispetto al prezzo attuale e tieni i piu' vicini
    for level in clustered:
        level.kind = "resistenza" if level.price > price else "supporto"

    supports = sorted([l for l in clustered if l.kind == "supporto"], key=lambda l: price - l.price)
    resistances = sorted([l for l in clustered if l.kind == "resistenza"], key=lambda l: l.price - price)
    return supports[:max_each] + resistances[:max_each]


def nearest_level(levels: List[Level], price: float, kind: str) -> Optional[Level]:
    candidates = [l for l in levels if l.kind == kind]
    if not candidates:
        return None
    return min(candidates, key=lambda l: abs(l.price - price))


# --------------------------------------------------------------------------
# Piano operativo (aritmetica di gestione del rischio)
# --------------------------------------------------------------------------


def build_plan(
    direction: str,
    candles_15m: List[Candle],
    view15: TFView,
    levels: List[Level],
    account: dict,
    contract_size: float,
) -> Optional[Plan]:
    if view15.atr is None or view15.atr <= 0:
        return None

    close = view15.close
    atr_val = view15.atr
    lookback = candles_15m[-14:]

    if direction == "long":
        structure = min(c.low for c in lookback)
        invalidation = structure - 0.3 * atr_val
        risk = close - invalidation
    else:
        structure = max(c.high for c in lookback)
        invalidation = structure + 0.3 * atr_val
        risk = invalidation - close

    warning = None
    if risk <= 0:
        return None
    if risk > 3.5 * atr_val:
        warning = "Stop tecnico molto largo rispetto alla volatilita': il rapporto rischio/rendimento parte penalizzato."

    if direction == "long":
        target_1r, target_2r = close + risk, close + 2 * risk
        struct_level = nearest_level(levels, close, "resistenza")
    else:
        target_1r, target_2r = close - risk, close - 2 * risk
        struct_level = nearest_level(levels, close, "supporto")

    structural_target = struct_level.price if struct_level else None
    if structural_target is not None and abs(structural_target - close) < 0.5 * risk:
        warning = (warning or "") + " Il primo livello di struttura e' molto vicino: poco spazio prima dell'ostacolo."

    size_units = size_lots = money = None
    equity = account.get("equity")
    risk_pct = account.get("risk_pct")
    if equity and risk_pct:
        money = equity * risk_pct / 100.0
        size_units = money / risk
        size_lots = size_units / contract_size if contract_size else None

    return Plan(
        direction=direction,
        reference=close,
        invalidation=invalidation,
        risk_per_unit=risk,
        target_1r=target_1r,
        target_2r=target_2r,
        structural_target=structural_target,
        size_units=size_units,
        size_lots=size_lots,
        money_at_risk=money,
        warning=warning.strip() if warning else None,
    )


# --------------------------------------------------------------------------
# Rilevatori di condizioni
# --------------------------------------------------------------------------


def _higher_tf_bonus(view1h: TFView, view4h: TFView, direction: str) -> Tuple[int, List[str]]:
    wanted = 1 if direction == "long" else -1
    bonus = 0
    reasons = []
    if view1h.trend_dir == wanted:
        bonus += 10
        reasons.append(f"1h coerente ({view1h.trend})")
    elif view1h.trend_dir == -wanted:
        bonus -= 20
        reasons.append(f"1h in contrasto ({view1h.trend})")
    if view4h.trend_dir == wanted:
        bonus += 8
        reasons.append(f"4h coerente ({view4h.trend})")
    elif view4h.trend_dir == -wanted:
        bonus -= 12
        reasons.append(f"4h in contrasto ({view4h.trend})")
    return bonus, reasons


def detect_pullback(candles15: List[Candle], v15: TFView, v1h: TFView, v4h: TFView) -> Optional[Signal]:
    """Ritracciamento sulla media in un trend gia' impostato: il setup piu' banale e piu' usato."""
    if v15.ema20 is None or v15.ema50 is None or v15.atr is None or v15.rsi is None:
        return None

    last_candle = candles15[-1]
    body_range = last_candle.high - last_candle.low
    if body_range <= 0:
        return None

    zone_low = min(v15.ema20, v15.ema50) - 0.25 * v15.atr
    zone_high = max(v15.ema20, v15.ema50) + 0.25 * v15.atr

    # long
    if v1h.trend_dir == 1 and zone_low <= last_candle.low <= zone_high and 35 <= v15.rsi <= 58:
        close_position = (last_candle.close - last_candle.low) / body_range
        if close_position >= 0.5:
            score = 52
            reasons = [
                "Il prezzo e' rientrato sulla zona EMA20/50 dentro un trend orario rialzista",
                f"Chiusura nella parte alta della candela ({close_position*100:.0f}% del range)",
                f"RSI a {v15.rsi:.0f}: ritracciamento, non ipervenduto",
            ]
            bonus, extra = _higher_tf_bonus(v1h, v4h, "long")
            score += bonus
            reasons += extra
            if v15.adx and v15.adx >= 20:
                score += 6
                reasons.append(f"ADX 15m a {v15.adx:.0f}: il movimento ha ancora direzionalita'")
            return Signal("pullback_long", "Ritracciamento in trend rialzista", "long", score, reasons)

    # short
    if v1h.trend_dir == -1 and zone_low <= last_candle.high <= zone_high and 42 <= v15.rsi <= 65:
        close_position = (last_candle.high - last_candle.close) / body_range
        if close_position >= 0.5:
            score = 52
            reasons = [
                "Il prezzo e' risalito sulla zona EMA20/50 dentro un trend orario ribassista",
                f"Chiusura nella parte bassa della candela ({close_position*100:.0f}% del range)",
                f"RSI a {v15.rsi:.0f}: rimbalzo tecnico, non ipercomprato",
            ]
            bonus, extra = _higher_tf_bonus(v1h, v4h, "short")
            score += bonus
            reasons += extra
            if v15.adx and v15.adx >= 20:
                score += 6
                reasons.append(f"ADX 15m a {v15.adx:.0f}: il movimento ha ancora direzionalita'")
            return Signal("pullback_short", "Ritracciamento in trend ribassista", "short", score, reasons)

    return None


def detect_breakout(candles15: List[Candle], v15: TFView, v1h: TFView, v4h: TFView, lookback: int = 40) -> Optional[Signal]:
    """Rottura di un range recente accompagnata da espansione di volatilita'."""
    if len(candles15) < lookback + 2 or v15.atr is None:
        return None

    window = candles15[-(lookback + 1) : -1]
    range_high = max(c.high for c in window)
    range_low = min(c.low for c in window)
    last_candle = candles15[-1]
    candle_range = last_candle.high - last_candle.low
    expansion = candle_range / v15.atr if v15.atr else 0

    if expansion < 1.15:
        return None  # rottura senza spinta: spesso e' un falso movimento

    if last_candle.close > range_high:
        score = 50 + min(int((expansion - 1.15) * 20), 12)
        reasons = [
            f"Chiusura sopra il massimo delle ultime {lookback} candele da 15m",
            f"Candela ampia {expansion:.1f} volte l'ATR: c'e' partecipazione dietro la rottura",
        ]
        bonus, extra = _higher_tf_bonus(v1h, v4h, "long")
        score += bonus
        reasons += extra
        if v15.bb_width_rank is not None and v15.bb_width_rank < 30:
            score += 6
            reasons.append("La rottura arriva da una fase di compressione: espansioni cosi' tendono a durare di piu'")
        return Signal("breakout_long", "Rottura al rialzo del range", "long", score, reasons)

    if last_candle.close < range_low:
        score = 50 + min(int((expansion - 1.15) * 20), 12)
        reasons = [
            f"Chiusura sotto il minimo delle ultime {lookback} candele da 15m",
            f"Candela ampia {expansion:.1f} volte l'ATR: c'e' partecipazione dietro la rottura",
        ]
        bonus, extra = _higher_tf_bonus(v1h, v4h, "short")
        score += bonus
        reasons += extra
        if v15.bb_width_rank is not None and v15.bb_width_rank < 30:
            score += 6
            reasons.append("La rottura arriva da una fase di compressione: espansioni cosi' tendono a durare di piu'")
        return Signal("breakout_short", "Rottura al ribasso del range", "short", score, reasons)

    return None


def detect_squeeze(v15: TFView, v1h: TFView) -> Optional[Signal]:
    """Compressione di volatilita': non dice dove andra', dice che si sta caricando."""
    if v15.bb_width_rank is None or v15.bb_width_rank > 8:
        return None
    reasons = [
        "L'ampiezza delle Bollinger e' ai minimi delle ultime 100 candele da 15m",
        "Fasi cosi' finiscono quasi sempre con un'espansione, ma la direzione non e' deducibile da qui",
        f"Contesto orario: {v1h.trend}",
    ]
    return Signal("squeeze", "Compressione di volatilita'", "neutro", 55, reasons)


def detect_divergence(candles15: List[Candle], v15: TFView, lookback: int = 45) -> Optional[Signal]:
    """Divergenza RSI fra gli ultimi due pivot: segnale di stanchezza, non di inversione."""
    if len(candles15) < lookback + 20:
        return None

    closes = [c.close for c in candles15]
    highs = [c.high for c in candles15]
    lows = [c.low for c in candles15]
    rsi_series = ind.rsi(closes, 14)
    pivot_highs, pivot_lows = ind.swing_points(highs, lows, left=3, right=3)

    recent_lows = [i for i in pivot_lows if i >= len(candles15) - lookback]
    if len(recent_lows) >= 2:
        a, b = recent_lows[-2], recent_lows[-1]
        if rsi_series[a] is not None and rsi_series[b] is not None:
            if lows[b] < lows[a] and rsi_series[b] > rsi_series[a] + 2 and rsi_series[a] < 42:
                return Signal(
                    "divergence_bull",
                    "Divergenza rialzista sull'RSI",
                    "long",
                    50,
                    [
                        "Il prezzo ha fatto un minimo piu' basso, l'RSI un minimo piu' alto",
                        f"RSI passato da {rsi_series[a]:.0f} a {rsi_series[b]:.0f}",
                        "La pressione in vendita sta calando, ma serve conferma dal prezzo prima di parlare di inversione",
                    ],
                )

    recent_highs = [i for i in pivot_highs if i >= len(candles15) - lookback]
    if len(recent_highs) >= 2:
        a, b = recent_highs[-2], recent_highs[-1]
        if rsi_series[a] is not None and rsi_series[b] is not None:
            if highs[b] > highs[a] and rsi_series[b] < rsi_series[a] - 2 and rsi_series[a] > 58:
                return Signal(
                    "divergence_bear",
                    "Divergenza ribassista sull'RSI",
                    "short",
                    50,
                    [
                        "Il prezzo ha fatto un massimo piu' alto, l'RSI un massimo piu' basso",
                        f"RSI passato da {rsi_series[a]:.0f} a {rsi_series[b]:.0f}",
                        "La spinta in acquisto sta calando, ma serve conferma dal prezzo prima di parlare di inversione",
                    ],
                )

    return None


def detect_momentum_shift(v1h: TFView) -> Optional[Signal]:
    """Istogramma MACD orario che attraversa lo zero: cambio di inerzia sul timeframe di riferimento."""
    if v1h.macd_hist is None or v1h.macd_hist_prev is None:
        return None
    if v1h.macd_hist > 0 >= v1h.macd_hist_prev:
        return Signal(
            "momentum_up_1h",
            "L'inerzia oraria passa al rialzo",
            "long",
            48,
            ["L'istogramma MACD sull'orario ha superato lo zero", f"Trend orario attuale: {v1h.trend}"],
        )
    if v1h.macd_hist < 0 <= v1h.macd_hist_prev:
        return Signal(
            "momentum_down_1h",
            "L'inerzia oraria passa al ribasso",
            "short",
            48,
            ["L'istogramma MACD sull'orario e' sceso sotto lo zero", f"Trend orario attuale: {v1h.trend}"],
        )
    return None


def detect_level_approach(v15: TFView, levels: List[Level], digits: int) -> Optional[Signal]:
    """Prezzo in avvicinamento a un livello rilevante: il momento in cui vale la pena aprire il grafico."""
    if v15.atr is None or not levels:
        return None
    price = v15.close
    threshold = 0.35 * v15.atr

    close_levels = [l for l in levels if abs(l.price - price) <= threshold and l.touches >= 2]
    if not close_levels:
        return None

    level = min(close_levels, key=lambda l: abs(l.price - price))
    return Signal(
        "level_approach",
        f"Prezzo sul {level.kind} a " + f"{level.price:,.{digits}f}".replace(",", "§").replace(".", ",").replace("§", "."),
        "neutro",
        50 + min(level.touches * 4, 12),
        [
            f"Livello costruito su {level.touches} tocchi ({level.source})",
            "Distanza attuale: " + f"{abs(level.price - price):.{digits}f}".replace(".", ",") + ", meno di mezzo ATR",
            "Qui il mercato decide: o rimbalza o lo rompe. Vale la pena guardare come reagisce.",
        ],
    )


def detect_overextension(v15: TFView, v1h: TFView) -> Optional[Signal]:
    """Allontanamento eccessivo dalla media: avviso di cautela, mai un invito a entrare controtrend."""
    if v15.ema20 is None or v15.atr is None or v15.atr <= 0:
        return None
    distance = (v15.close - v15.ema20) / v15.atr
    if abs(distance) < 2.6:
        return None
    direction = "rialzo" if distance > 0 else "ribasso"
    return Signal(
        "overextension",
        f"Prezzo molto esteso al {direction}",
        "neutro",
        45,
        [
            f"Il prezzo dista {abs(distance):.1f} ATR dalla EMA20 sui 15m",
            "Entrare adesso nella direzione del movimento significa comprare caro o vendere a sconto",
            "Statisticamente segue una pausa o un ritracciamento, ma un trend forte puo' restare esteso a lungo",
        ],
    )


# --------------------------------------------------------------------------
# Orchestrazione
# --------------------------------------------------------------------------


def analyze(symbol_cfg: dict, frames: Dict[str, List[Candle]], account: dict) -> Snapshot:
    v15 = build_view(frames["15m"], "15m")
    v1h = build_view(frames["1h"], "1h")
    v4h = build_view(frames["4h"], "4h")

    price = v15.close
    digits = symbol_cfg.get("digits", 2)
    levels = find_levels(frames["1h"], price, v1h.atr or (price * 0.003))

    candles15 = frames["15m"]
    raw_signals = [
        detect_pullback(candles15, v15, v1h, v4h),
        detect_breakout(candles15, v15, v1h, v4h),
        detect_squeeze(v15, v1h),
        detect_divergence(candles15, v15),
        detect_momentum_shift(v1h),
        detect_level_approach(v15, levels, digits),
        detect_overextension(v15, v1h),
    ]
    signals = [s for s in raw_signals if s is not None]

    for signal in signals:
        signal.score = max(0, min(100, signal.score))
        if signal.direction in ("long", "short"):
            signal.plan = build_plan(
                signal.direction,
                candles15,
                v15,
                levels,
                account,
                symbol_cfg.get("contract_size", 1),
            )

    signals.sort(key=lambda s: s.score, reverse=True)

    notes: List[str] = []
    if v15.atr_pct is not None and v15.atr_pct > 0.6:
        notes.append("Volatilita' sopra la norma sui 15m: stop piu' larghi e size piu' piccola.")
    if v4h.trend_dir != 0 and v1h.trend_dir != 0 and v4h.trend_dir != v1h.trend_dir:
        notes.append("Orario e 4 ore puntano in direzioni opposte: fase di transizione, i falsi segnali aumentano.")

    return Snapshot(
        symbol=symbol_cfg["name"],
        price=price,
        digits=digits,
        views={"15m": v15, "1h": v1h, "4h": v4h},
        levels=levels,
        signals=signals,
        notes=notes,
    )
