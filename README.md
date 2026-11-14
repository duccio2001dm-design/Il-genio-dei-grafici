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
Gestione: a Target 1 chiudi meta' e sposta lo stop a pareggio
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

Sette, ricalcolate a ogni ciclo su candele chiuse a 15 minuti, 1 ora e 4 ore:

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

**Sull'oro il filtro è più severo.** Un segnale COMPRA/VENDI su XAUUSD parte solo se
l'orario e il 4 ore sono entrambi nella sua direzione, e lo stop ha un margine più
largo (0,5 ATR invece di 0,3). Il motivo: nel primo mese di registro i segnali
sull'oro hanno chiuso a −26R contro i +22R di Bitcoin. I segnali scartati dal filtro
non ti arrivano ma vengono seguiti lo stesso in silenzio, e `/report` ti mostra come
sarebbero andati: se fanno meglio di quelli inviati, il filtro va tolto
(`"require_trend_alignment": false` in `config.json`).

---

## 3. Dopo il segnale: gestione e aggiornamenti

Il bot segue ogni operazione che ti ha segnalato e ti scrive quando succede qualcosa:

- 🎯 **Target 1 raggiunto**: chiudi metà e sposta lo stop a pareggio
- ⚖️ **Tornato al pareggio** dopo il Target 1: la parte rimasta si chiude a zero (+0,5R in totale)
- ✅ **Target 2 raggiunto** / 🛑 **Stop loss raggiunto**

Perché questa gestione: nel registro 17 operazioni su 64 finite in stop avevano già
toccato il Target 1. Chiudendo metà lì, il risultato complessivo del primo mese passa
da −4R a +6,5R. Sono poche operazioni e il calcolo non include spread e commissioni:
è un'indicazione, non una garanzia.

Durante le ore di silenzio questi aggiornamenti non si perdono: arrivano tutti insieme
appena finisce il silenzio.

---

## 4. L'oracolo (solo BTCUSD)

Oltre ai segnali, il bot ha un **oracolo probabilistico** per Bitcoin a 1 e 4 ore.
Non dice "compra" o "vendi": dice quanto è probabile che il prezzo salga e dentro
quale forbice resterà.

```
🔮 Oracolo BTCUSD · 10/11 14:00 UTC
Prossima ora: ↗️ 54% rialzo
  forbice 80%: 74.827 – 75.537
Prossime 4 ore: ➖ 52% rialzo, troppo vicino al 50%: nessun vantaggio
  forbice 80%: 74.463 – 75.893
```

Lo trovi con `/oracolo`, nel riepilogo ogni 4 ore e, in una riga, in ogni segnale
COMPRA/VENDI su BTC ("concorda", "contrario" o "neutro").

**Come è stato costruito.** Un modello statistico (regressione logistica per la
direzione, un modello di volatilità per le forbici) addestrato su 8 anni di candele
da 15 minuti di BTC/USD (Bitstamp, 2018-2026). È stato verificato in modo
*walk-forward*: per ogni anno dal 2021 al 2026 è stato addestrato solo sugli anni
precedenti e misurato su quello, che non aveva mai visto. Le probabilità sono
calibrate: quando dice 55%, storicamente si è avverato il 55% delle volte.

**Cosa è venuto fuori, senza abbellimenti:**

| | Media 2021-2026 | 2026 |
|---|---|---|
| Forbice 80%: il prezzo ci resta dentro | 78-80% | 79% |
| Direzione giusta a 1 ora | 53,2% | 51,7% |
| Direzione giusta a 4 ore | 53,5% | 51,3% |

Le **forbici funzionano**: sono stabili su tutti gli anni e molto più informative di
una stima ingenua. Servono per mettere stop e target dove il mercato ha spazio di
muoversi, o per capire quando un movimento è fuori dal normale. Sulla **direzione**
il vantaggio è piccolo, è calato negli anni e nel 2026 è quasi nullo: vale meno dello
spread di un broker, quindi da solo non giustifica un'operazione. Per questo, sotto il
53% o sopra il 47%, l'oracolo risponde "nessun vantaggio", che è la cosa onesta.

**La pagella dal vivo.** Una volta all'ora l'oracolo registra la sua previsione, e
un'ora e quattro ore dopo la confronta con il prezzo vero. `/oracolo` ti mostra il
conteggio aggiornato: se le forbici smettono di prendere il 75-80% o la direzione
scende stabilmente sotto il 50%, il modello va rifatto.

**Rifare i pesi** (facoltativo, una volta ogni qualche mese): con un CSV di candele
da 15 minuti (`ts,open,high,low,close,volume`) lancia
`pip install numpy scikit-learn` e poi `python train_oracle.py candele.csv`. Il
risultato sostituisce `oracle_model.json` e stampa la verifica anno per anno.

Per disattivarlo: in `config.json` metti `"oracle": {"enabled": false}`.

---

## 5. Comandi Telegram

- `/stato` — riepilogo di entrambi gli strumenti
- `/report` — risultati di tutti i segnali registrati, per strumento e tipo di segnale,
  sia tenendo tutta la posizione sia con la gestione "metà a T1 + pareggio"
- `/report 30` — lo stesso, solo sugli ultimi 30 giorni
- `/oracolo` — probabilità e forbici di BTC a 1 e 4 ore, con la pagella
- `/salute` — quanti giri ha fatto negli ultimi 7 giorni, ogni quanto, le pause
  lunghe e lo stato dei dati
- `/aiuto` — elenco dei comandi

La risposta arriva al ciclo successivo (di solito entro 5-10 minuti): senza un server
sempre acceso il bot non può ascoltare in tempo reale.

Il bot ti avvisa da solo se un giro fallisce, se è rimasto fermo più di 45 minuti
o se i dati di uno strumento mancano da più di mezz'ora. Per sapere subito se si
ferma del tutto e per farlo partire puntuale ogni 5 minuti, segui la sezione
"Rendere il bot affidabile" in [GUIDA-GITHUB.md](GUIDA-GITHUB.md). Per una risposta immediata, lancia il
workflow a mano da GitHub → Actions → Run workflow.

---

## 6. Impostazioni utili (`config.json`)

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

Per ogni strumento, dentro `symbols`:

```jsonc
"require_trend_alignment": true,  // COMPRA/VENDI solo con 1h e 4h concordi (attivo sull'oro)
"stop_atr_buffer": 0.5            // margine dello stop oltre la struttura, in ATR (default 0,3)
```

Troppi messaggi → alza `min_score` a 70. Troppo pochi → scendi a 55, ricordando che
abbassare la soglia moltiplica i falsi allarmi molto più delle occasioni buone.

L'orologio è in UTC: d'estate l'Italia è UTC+2, d'inverno UTC+1. Se vuoi silenzio
dall'1 alle 7 del mattino italiane d'estate, metti `[23,0,1,2,3,4,5]`.

---

## 7. Provarlo in locale (facoltativo)

Se hai Python sul computer:

```bash
python3 main.py --demo      # dati finti, nessuna chiave, vedi il formato dei messaggi
python3 main.py --dry-run   # dati veri, messaggi stampati a schermo invece che inviati
python3 main.py --once      # un ciclo completo
python3 main.py             # servizio continuo
```

Non è necessario per usarlo: su GitHub Actions gira tutto da solo.

---

## 8. I dati

**Bitcoin**: Coinbase, endpoint pubblico, gratis, nessuna registrazione, 24/7.
In alternativa c'è Binance (`"source": "binance"`, `"api_symbol": "BTCUSDT"`), che però
spesso non risponde ai server di GitHub.

**Oro**: Twelve Data, piano gratuito. Per risparmiare chiamate scarico solo le candele
da 15 minuti e ricostruisco 1 ora e 4 ore in locale: una chiamata per ciclo invece di
tre, meno di 300 al giorno con il ciclo da 5 minuti, dentro il limite di 800 del piano free.

Il mercato dell'oro chiude nel weekend e il sistema lo sa: sabato e domenica non prova
nemmeno a scaricare. E il prezzo del provider **non coincide mai esattamente** con
quello del tuo broker: spread e feed diversi, spesso qualche decimo di differenza.
Controlla sempre i livelli sulla tua piattaforma prima di piazzare un ordine.

---

## 9. Cosa il sistema non può vedere

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
