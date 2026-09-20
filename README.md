# Assistente tecnico BTCUSD / XAUUSD

Legge i grafici di Bitcoin e oro al posto tuo, 24 ore su 24, e quando trova una
situazione che merita ti manda su Telegram un messaggio operativo:

```
🟢 COMPRA BTCUSD  ·  confluenza 76/100
Ritracciamento in trend rialzista

━━ OPERATIVO ━━
Ingresso: 68.068,8
Stop loss: 67.703,3  (rischio 365,5 per BTC)
Take profit 1: 68.434,3  (1R)
Take profit 2: 68.799,9  (2R)
Size: 0,0274 BTC  (rischi 10,00 USD)

Perche'
• Il prezzo e' rientrato sulla zona EMA20/50 dentro un trend orario rialzista
• Chiusura nella parte alta della candela (71% del range)
• RSI a 56: ritracciamento, non ipervenduto
...
```

Python puro, nessuna libreria da installare, gira gratis su GitHub Actions.

👉 **Per metterlo in funzione leggi [GUIDA-GITHUB.md](GUIDA-GITHUB.md)**: venti minuti,
tutto dal browser, anche dal telefono, zero euro e nessuna carta di credito.

---

## 1. Come sono fatti i consigli

Entrata, stop e target non sono numeri a caso. Lo stop sta oltre l'ultimo minimo (o
massimo) di struttura delle ultime tre ore e mezza, più un margine proporzionale alla
volatilità del momento, perché è lì che l'idea tecnica smette di avere senso. I target
sono multipli della distanza dello stop, e il messaggio ti dice anche dove sta il primo
ostacolo reale sul grafico, che spesso è più vicino del take profit 2. La size è
calcolata all'indietro dal rischio: se hai impostato 1% su 1000 euro, la quantità è
quella che ti fa perdere 10 euro se lo stop viene preso.

C'è una cosa che devi sapere e che nessun sistema di questo tipo ti dirà mai da solo.
Un messaggio che dice COMPRA con quattro numeri precisi **sembra** più affidabile di
uno che descrive la situazione, ma non lo è: sotto c'è la stessa identica analisi. Il
numero della confluenza conta quante condizioni tecniche coincidono in quel momento,
e 76/100 non significa 76% di probabilità di successo. Anche i sistemi tecnici che
funzionano sbagliano spesso quattro o cinque operazioni su dieci, e restano in utile
solo perché le operazioni giuste valgono più di quelle sbagliate. È il motivo per cui
lo stop loss nel messaggio conta più della direzione.

Tu hai detto che controlli comunque prima di operare: quello è esattamente l'uso
giusto. Tratta i messaggi come il collega che ti dice "guarda qui che sta succedendo
questo", non come un ordine.

---

## 2. Le condizioni che riconosce

Undici, ricalcolate a ogni ciclo su candele chiuse a 15 minuti, 1 ora e 4 ore:

| Condizione | Direzione | Cosa guarda |
|---|---|---|
| Ritracciamento in trend | 🟢 / 🔴 | rientro sulla zona EMA20/50 dentro un trend già impostato |
| Rottura di range | 🟢 / 🔴 | chiusura oltre 40 candele di massimo/minimo con espansione di volatilità |
| Divergenza RSI | 🟢 / 🔴 | prezzo e RSI che si contraddicono tra due pivot |
| Cambio di inerzia | 🟢 / 🔴 | istogramma MACD orario che attraversa lo zero |
| Compressione di volatilità | 🔵 | Bollinger ai minimi di 100 candele: si sta caricando qualcosa |
| Avvicinamento a un livello | 🔵 | il prezzo arriva su un supporto o resistenza a più tocchi |
| Estensione eccessiva | 🔵 | prezzo a oltre 2,6 ATR dalla media: brutto momento per entrare |

Le prime quattro producono un messaggio operativo con COMPRA o VENDI. Le tre in blu
sono informative: segnalano un contesto, non una direzione, perché fingere di saperla
in quelle situazioni sarebbe inventare.

---

## 3. Comandi Telegram

- `/stato` — riepilogo di entrambi gli strumenti
- `/aiuto` — elenco dei comandi

La risposta arriva al ciclo successivo (entro un quarto d'ora): senza un server sempre
acceso il bot non può ascoltare in tempo reale. Per una risposta immediata, lancia il
workflow a mano da GitHub → Actions → Run workflow.

---

## 4. Impostazioni utili (`config.json`)

```jsonc
{
  "summary_every_minutes": 240,     // riepilogo automatico ogni 4 ore
  "quiet_hours_utc": [0,1,2,3,4],   // ore di silenzio, in UTC
  "min_score": 60,                  // soglia sotto la quale non ti disturba
  "cooldown_minutes": 90,           // non ripete lo stesso avviso prima di 90 minuti
  "account": {
    "equity": 1000,                 // serve solo a calcolare la size
    "risk_pct": 1.0,
    "currency": "USD"
  }
}
```

Troppi messaggi → alza `min_score` a 70. Troppo pochi → scendi a 55, ricordando che
abbassare la soglia moltiplica i falsi allarmi molto più delle occasioni buone.

L'orologio è in UTC: d'estate l'Italia è UTC+2, d'inverno UTC+1. Se vuoi silenzio
dall'1 alle 7 del mattino italiane d'estate, metti `[23,0,1,2,3,4,5]`.

---

## 5. Provarlo in locale (facoltativo)

Se hai Python sul computer:

```bash
python3 main.py --demo      # dati finti, nessuna chiave, vedi il formato dei messaggi
python3 main.py --dry-run   # dati veri, messaggi stampati a schermo invece che inviati
python3 main.py --once      # un ciclo completo
python3 main.py             # servizio continuo
```

Non è necessario per usarlo: su GitHub Actions gira tutto da solo.

---

## 6. I dati

**Bitcoin**: Binance, endpoint pubblico, gratis, nessuna registrazione, 24/7.
Se dai server GitHub non fosse raggiungibile, in `config.json` cambia `"source"` in
`"coinbase"` e `"api_symbol"` in `"BTC-USD"`.

**Oro**: Twelve Data, piano gratuito. Per risparmiare chiamate scarico solo le candele
da 15 minuti e ricostruisco 1 ora e 4 ore in locale: una chiamata per ciclo invece di
tre, circa 100 al giorno, comodamente dentro il piano free.

Il mercato dell'oro chiude nel weekend e il sistema lo sa: sabato e domenica non prova
nemmeno a scaricare. E il prezzo del provider **non coincide mai esattamente** con
quello del tuo broker: spread e feed diversi, spesso qualche decimo di differenza.
Controlla sempre i livelli sulla tua piattaforma prima di piazzare un ordine.

---

## 7. Cosa il sistema non può vedere

Vale la pena tenerlo a mente, perché sono i casi in cui sbaglia di più:

- **Le notizie.** Non legge il calendario macro. Sull'oro, dove un dato
  sull'inflazione americana muove il prezzo di decine di dollari in pochi secondi,
  è il buco più grosso. Tieni aperto un calendario economico e ignora i segnali nei
  dieci minuti attorno ai dati importanti.
- **Il ritardo.** Lavora su candele chiuse, e GitHub esegue i cicli con qualche
  minuto di ritardo. Tra l'inizio di un movimento e il messaggio possono passare
  venti minuti. È voluto in parte: analizzare candele ancora aperte produce segnali
  che spariscono da soli.
- **Il tuo contesto.** Non sa quante posizioni hai già aperte, quanto hai perso
  stamattina, o se stai operando stanco. Il calcolo della size presume che ogni
  operazione sia indipendente e che tu rispetti lo stop.
- **Se sta funzionando.** Un mese positivo non dimostra niente: servono decine di
  operazioni prima che i numeri inizino a dire qualcosa. Tieni un registro di cosa
  arriva e di cosa fai, e confrontalo ogni tanto con l'ipotesi "non faccio niente".
  È l'unico modo onesto per saperlo, e vale più di qualsiasi ottimizzazione dei
  parametri.

Le decisioni, il rischio e le conseguenze restano tue. Io ti do lo strumento che
guarda i grafici; quello che ci fai è una scelta che nessun software può prendere
al posto tuo.
