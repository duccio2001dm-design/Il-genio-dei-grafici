# Come farlo partire — guida passo passo, senza terminale

Tutto quello che serve si fa dal browser, anche dal telefono. Costo totale: zero.
Tempo: una ventina di minuti la prima volta.

Il meccanismo: GitHub esegue gratuitamente uno script ogni pochi minuti. Lo script
guarda i grafici, e se trova qualcosa ti scrive su Telegram. Non c'e' nessun server
da gestire, nessuna carta di credito, niente da tenere acceso in casa.

---

## Passo 1 — Crea il bot Telegram (3 minuti)

1. Apri Telegram e cerca **@BotFather** (quello con la spunta blu).
2. Mandagli `/newbot`.
3. Ti chiede un nome: scrivi quello che vuoi, per esempio `Assistente Grafici`.
4. Ti chiede uno username: deve finire con `bot`, per esempio `miei_grafici_bot`.
   Se e' gia' preso te lo dice, provane un altro.
5. Ti risponde con un **token**, una riga tipo `8123456789:AAHxyz...`.
   Copialo e incollalo da qualche parte, ti serve tra poco.

⚠️ Il token e' come una password: chi ce l'ha può scrivere dal tuo bot. Non metterlo
in chiaro in nessun file che pubblichi.

---

## Passo 2 — Trova il tuo chat_id (2 minuti)

1. Cerca su Telegram il bot che hai appena creato e mandagli un messaggio qualsiasi,
   anche solo `ciao`. **Questo passaggio è obbligatorio**: un bot non può scrivere
   per primo a nessuno.
2. Apri nel browser questo indirizzo, sostituendo `IL_TUO_TOKEN`:

```
https://api.telegram.org/botIL_TUO_TOKEN/getUpdates
```

Esempio: `https://api.telegram.org/bot8123456789:AAHxyz.../getUpdates`

3. Vedrai un testo pieno di parentesi graffe. Cerca `"chat":{"id":` seguito da un
   numero, tipo `"id":123456789`. **Quel numero è il tuo chat_id.** Copialo.

Se vedi `{"ok":true,"result":[]}` vuol dire che il messaggio al bot non è arrivato:
torna al punto 1.

---

## Passo 3 — Prendi la chiave per l'oro (3 minuti)

Bitcoin non richiede nessuna registrazione. L'oro sì, perché serve un fornitore di
dati forex.

1. Vai su **twelvedata.com** e registrati con l'email (piano gratuito).
2. Nella dashboard trovi la tua **API key**. Copiala.

Se l'oro non ti interessa subito, puoi saltare questo passo: il sistema funzionerà
solo su Bitcoin e ti scriverà che i dati dell'oro non sono disponibili.

---

## Passo 4 — Metti il codice su GitHub (5 minuti)

1. Crea un account su **github.com** se non ce l'hai (gratis).
2. In alto a destra, **+** → **New repository**.
3. Dai un nome, per esempio `assistente-trading`.
4. Scegli **Public**. Vedi la nota qui sotto sul perché.
5. **Create repository**.
6. Nella pagina che si apre clicca **uploading an existing file**.
7. Carica tutti i file della cartella, **compresa la cartella `.github`**.
   Se carichi da telefono e la cartella `.github` non viene su, vedi la nota in fondo.
8. In basso clicca **Commit changes**.

**Perché pubblico?** Su GitHub i repository pubblici hanno minuti di esecuzione
illimitati e gratuiti; quelli privati ne hanno 2000 al mese, che con un ciclo ogni
5 minuti non bastano. Pubblico significa che si vede il *codice* — che è comunque
il codice che ti ho dato, niente di tuo. Il token e la chiave **non** finiscono nel
repository: li metti nel passo successivo, dove GitHub li tiene cifrati e non li
mostra mai, nemmeno nei log.

Se preferisci comunque tenerlo privato: apri `.github/workflows/Assistente tradding.yml` e
cambia la riga del cron in `- cron: "13,43 * * * *"` (ogni 30 minuti invece di 5).
Così rientri nei 2000 minuti gratuiti.

---

## Passo 5 — Inserisci i tre segreti (3 minuti)

Nel tuo repository: **Settings** (in alto) → nella colonna di sinistra
**Secrets and variables** → **Actions** → pulsante verde **New repository secret**.

Creane tre, uno alla volta. Il nome va scritto **esattamente** così:

| Name | Secret |
|---|---|
| `TELEGRAM_BOT_TOKEN` | il token di BotFather |
| `TELEGRAM_CHAT_ID` | il numero del passo 2 |
| `TWELVEDATA_API_KEY` | la chiave di Twelve Data (lascia vuoto se salti l'oro) |

---

## Passo 6 — Accendi tutto (2 minuti)

1. Vai sulla scheda **Actions** del tuo repository.
2. Se compare un avviso tipo *"Workflows aren't being run on this forked repository"*
   oppure un pulsante verde **I understand my workflows, go ahead and enable them**,
   cliccalo.
3. Nella colonna di sinistra clicca **Assistente trading**.
4. A destra compare **Run workflow** → clicca → **Run workflow**.

Questo primo avvio manuale è importante: GitHub spesso non attiva la pianificazione
automatica finché il workflow non è partito almeno una volta.

5. Dopo qualche secondo compare una riga con un pallino giallo (in corso) che diventa
   verde (riuscito) o rosso (errore). Cliccaci sopra per vedere il log riga per riga.

Se è verde e non hai ricevuto niente su Telegram, è normale: significa che in quel
momento non c'era nessuna condizione sopra la soglia. Il primo riepilogo arriva
comunque entro poco. Per forzarlo subito, manda `/stato` al bot e rilancia il
workflow a mano.

---

## Dopo, nell'uso normale

- **Non devi fare niente.** Il workflow parte da solo: ogni 5 minuti con l'avvio esterno
  descritto sotto, ogni 15 minuti con il solo orario di GitHub.
- **Per un controllo immediato**: Actions → Assistente trading → Run workflow.
- **Per chiedere il punto della situazione**: manda `/stato` al bot. La risposta
  arriva al ciclo successivo, di solito entro 5-10 minuti. Con `/report` vedi i
  risultati di tutti i segnali, con `/salute` la puntualità dei giri, con `/aiuto`
  l'elenco dei comandi. È il limite di un
  sistema che non ha un server sempre acceso.
- **Per cambiare le impostazioni**: apri `config.json` nel repository, clicca la
  matita, modifica, **Commit changes**. Le più utili sono `min_score` (alzala a 70
  se ricevi troppi messaggi), `risk_pct` e `equity` per il calcolo della size, e
  `quiet_hours_utc` per le ore di silenzio (sono in UTC: l'Italia d'estate è UTC+2,
  d'inverno UTC+1).

---

## Rendere il bot affidabile (consigliato, 15 minuti)

GitHub, da solo, ha due difetti: gli orari pianificati arrivano spesso in ritardo
(anche 10-20 minuti) o saltano, e se il bot si ferma nessuno ti avvisa. Due servizi
gratuiti risolvono entrambe le cose. Il bot funziona anche senza, ma meno bene.

### A. Allarme se il bot si ferma (healthchecks.io)

Un servizio esterno aspetta un "battito" a ogni giro. Se i battiti smettono, ti
scrive lui su Telegram: è l'unico modo di saperlo, perché un bot fermo non può
avvisare di essere fermo.

1. Vai su **healthchecks.io** e crea un account gratuito.
2. Nel progetto che ti crea c'è già un check: aprilo, **Edit** sulla riga
   *Schedule*: imposta **Period 15 minutes** e **Grace 15 minutes**. Così ti avvisa
   se non arriva nessun giro per mezz'ora.
3. Copia l'indirizzo di ping, quello che inizia con `https://hc-ping.com/`.
4. Sempre su healthchecks.io: **Integrations** → **Telegram** → **Add Integration**,
   e segui le istruzioni (scrivi al loro bot e premi il link che ti manda).
5. Su GitHub, nel repository: **Settings → Secrets and variables → Actions →
   New repository secret**. Nome `HEALTHCHECK_URL`, valore l'indirizzo di ping.

Da questo momento, se il bot tace per mezz'ora ti arriva un messaggio da
healthchecks.io, e un altro quando riparte.

### B. Avvio puntuale ogni 5 minuti (cron-job.org)

Invece di aspettare l'orario di GitHub, un servizio esterno lo avvia lui, con
precisione. L'orario di GitHub resta attivo ogni 15 minuti come riserva.

**Prima crea un token GitHub che può solo avviare questo workflow:**

1. Su GitHub: foto profilo in alto a destra → **Settings** → in fondo a sinistra
   **Developer settings** → **Personal access tokens** → **Fine-grained tokens** →
   **Generate new token**.
2. Nome: `avvio-assistente`. Scadenza: la più lunga disponibile (segnati la data:
   quando scade, l'avvio esterno smette e resta solo la riserva ogni 15 minuti).
3. **Repository access** → *Only select repositories* → scegli `Il-genio-dei-grafici`.
4. **Permissions** → **Actions**: *Read and write*. Nient'altro.
5. **Generate token** e copialo subito (non lo rivedrai più).

**Poi il servizio che lo usa:**

1. Vai su **cron-job.org**, crea un account gratuito, **Create cronjob**.
2. **URL**:
   `https://api.github.com/repos/duccio2001dm-design/Il-genio-dei-grafici/actions/workflows/Assistente%20tradding.yml/dispatches`
3. **Execution schedule**: *Every 5 minutes*.
4. Nella scheda **Advanced**:
   - **Request method**: `POST`
   - **Headers**, aggiungine tre:
     `Authorization` = `Bearer ` seguito dal token (con lo spazio dopo Bearer)
     `Accept` = `application/vnd.github+json`
     `X-GitHub-Api-Version` = `2022-11-28`
   - **Request body**: `{"ref":"main"}`
5. Salva e premi **Test run**: deve rispondere **204**. Su GitHub, in **Actions**,
   vedrai partire un giro.

Se risponde 401 o 403 il token è sbagliato o non ha il permesso Actions; se risponde
404 controlla l'URL (lo spazio nel nome del file va scritto `%20`).

### Come vedi se funziona

Manda `/salute` al bot: ti dice quanti giri ha fatto negli ultimi 7 giorni,
ogni quanto di solito, quante pause lunghe ci sono state e chi li ha avviati
("avvio esterno" è cron-job.org, "orario GitHub" è la riserva). Inoltre il bot ti
scrive da solo se:

- un giro fallisce (🚨, con il link all'errore);
- è rimasto fermo più di 45 minuti (⏱️, appena riparte);
- i dati di uno strumento mancano da più di 30 minuti (📡), e quando tornano (✅).

---

## Cose che possono andare storto

**Non arriva niente e il workflow è verde.** Controlla di aver scritto al bot almeno
una volta e che `TELEGRAM_CHAT_ID` sia il numero giusto. Nel log del workflow, un
errore `HTTP 400` o `403` su Telegram indica token o chat_id sbagliati.

**Il workflow è rosso.** Aprilo e leggi l'ultima riga rossa. Gli errori più comuni:
chiave Twelve Data mancante o esaurita (limite giornaliero del piano gratuito), o
Coinbase temporaneamente irraggiungibile. Di solito al giro dopo torna tutto a posto.

**Gli orari non sono precisi.** GitHub esegue i lavori pianificati quando ha risorse
libere: ritardi di 5-20 minuti sono normali, soprattutto negli orari di punta. Per un
sistema intraday su candele da 15 minuti è accettabile, ma è giusto che tu lo sappia:
non è un feed in tempo reale.

**Dopo due mesi smette.** GitHub disattiva i workflow pianificati nei repository fermi
da 60 giorni. Questo salva `state.json` a ogni giro, quindi il repository non è mai
davvero fermo, ma se ricevi un'email di avviso basta aprire Actions e ripremere il
pulsante di riattivazione.

**Caricare `.github` da telefono.** L'app e il browser mobile a volte ignorano le
cartelle che iniziano con un punto. In quel caso: nel repository clicca
**Add file** → **Create new file**, e nel campo del nome scrivi esattamente
`.github/workflows/Assistente tradding.yml` (le barre creano le cartelle da sole), poi incolla
dentro il contenuto del file e salva.

---

## Il conto finale

| Voce | Costo |
|---|---|
| GitHub Actions (repository pubblico) | 0 € |
| Dati Bitcoin (Coinbase) | 0 € |
| Dati oro (Twelve Data, piano free) | 0 € |
| Telegram | 0 € |
| healthchecks.io e cron-job.org (piani gratuiti) | 0 € |

Nessuna carta di credito richiesta in nessun passaggio.
