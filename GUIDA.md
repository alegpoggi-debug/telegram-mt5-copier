# Telegram → MT5 Copier: guida rapida (Mac)

Copia in automatico su MetaTrader 5 i segnali dei canali Telegram che scegli. È **gratuito**: non usa nessuna AI a pagamento e tutto si imposta dalla finestra del programma.

---

## 1) Avvio (la prima volta installa tutto da solo)
1. Estrai lo zip, per esempio in *Documenti*.
2. Fai doppio clic su **`Avvia.command`**.
   - La prima volta il Mac potrebbe bloccarlo. In quel caso fai **tasto destro → Apri → Apri**. Serve solo la prima volta.
   - Se manca Python, il Mac propone di installarlo: accetta e poi rifai doppio clic su `Avvia.command`.
   - La prima installazione dura 1–3 minuti.
3. Si apre la finestra. In alto c'è il riquadro blu **Configurazione** con 4 passaggi: man mano che li completi diventano ✅.

## 2) Collegare Telegram: pulsante "Configura Telegram"
1. Premi **Apri my.telegram.org** e accedi con il tuo numero. Poi apri *API development tools*, crea un'app con nome a piacere e copia **api_id** e **api_hash** nei due campi.
2. Premi **Accedi a Telegram**: scrivi il numero (+39…) e il codice che ricevi nell'app Telegram. Se hai la verifica in due passaggi, ti chiede anche la password.
3. Si carica l'elenco dei tuoi canali: **spunta quelli da copiare** e premi **Salva**.

## 3) Collegare MetaTrader 5: pulsante "Configura MetaTrader"
Prima installa MetaTrader 5 per Mac (dal sito del broker o da metatrader5.com), accedi al tuo **conto DEMO** e aprilo almeno una volta.
1. Premi **Installa il ponte in MetaTrader**: il file viene copiato da solo nella cartella giusta.
2. **Chiudi e riapri MetaTrader**.
3. Nel *Navigatore* (a sinistra) apri *Expert Advisors* e **trascina "TelegramBridge" su un grafico qualsiasi**.
4. Nella finestra che compare, scheda *Comuni*, spunta **Consenti Algo Trading** e premi OK.
5. Premi il pulsante **Algo Trading** in alto in MetaTrader: deve diventare **verde**.

Se "TelegramBridge" non compare: tasto destro su *Expert Advisors* → *Aggiorna*. Se ancora non c'è, aprilo con MetaEditor e premi *Compila*.

Quando il pallino **MetaTrader 5** nella finestra diventa verde, il collegamento è completo.

## 4) Prove (importante)
- Il programma parte in **MODALITÀ PROVA**: legge i messaggi e mostra cosa farebbe, ma **non apre ordini**.
- Con **🧪 Prova un messaggio** incolli un messaggio del canale e vedi subito come viene letto e quali ordini partirebbero.
- Quando tutto è giusto, vai in *Impostazioni → Volume e rischio* e togli **Modalità PROVA**. Prova prima sul **conto demo**.
- Sul conto **reale** il programma non apre nulla finché non attivi *Permetti operazioni sul conto REALE*.

---

## Tutte le impostazioni (⚙ Impostazioni)
| Scheda | Cosa puoi personalizzare |
|---|---|
| **Volume e rischio** | lotto fisso, % del saldo o importo fisso di rischio; lotto massimo; lotti totali; numero massimo di posizioni (totali e per simbolo); perdita massima e obiettivo di profitto giornalieri; modalità prova; conto reale |
| **Ingresso** | gestione dei TP multipli (una posizione per TP, solo il primo, solo l'ultimo, il TP numero X); ripartizione del lotto tra i TP (es. 50,30,20); ordini a mercato o pendenti; distanza massima dall'entrata; scadenza dei pendenti; SL/TP predefiniti se mancano; allargare lo SL o anticipare il TP; inversione dei segnali; solo BUY o solo SELL; simbolo predefinito |
| **Orari e simboli** | orario di lavoro, giorni attivi, simboli ammessi e bloccati, nomi alternativi (es. GOLD=XAUUSD) |
| **Gestione automatica** | pareggio sulle altre posizioni quando ne chiude una al TP; pareggio dopo X punti; trailing stop |
| **Messaggi del canale** | cosa seguire tra: pareggio, nuovo SL, chiusure, parziali, cancellazioni, messaggi modificati; cosa fare al "TP raggiunto"; a quali posizioni si applicano gli aggiornamenti senza "rispondi" |
| **Parole chiave** | tutte le parole e frasi che il programma riconosce, modificabili; parole per ignorare un messaggio |

Ogni scheda ha il pulsante **Ripristina predefiniti**.

## Pulsanti nella finestra principale
- **⏸ Pausa / ▶ Riprendi**: ferma i nuovi segnali; la gestione delle posizioni aperte continua.
- **Selezionate**: chiudi, chiudi 50%, SL a pareggio (seleziona prima le righe nella tabella).
- **Tutte**: SL a pareggio su tutte, chiudi 50% di tutte, **CHIUDI TUTTO** (chiede conferma).

## Da sapere
- Tieni aperti **MetaTrader** e il **programma**. L'app Telegram invece non serve aperta. Finché il programma è aperto, il Mac non va in stop.
- Le distanze sono in **punti** di MetaTrader: sul forex 1 pip = 10 punti; sull'oro 1 dollaro = 100 punti.
- Il programma tocca solo le operazioni che ha aperto lui. Le tue operazioni manuali restano libere.
- Il registro dettagliato si trova in `data/app.log`.

| Problema | Soluzione |
|---|---|
| Pallino MetaTrader rosso | TelegramBridge non è sul grafico, oppure *Algo Trading* non è verde |
| "simbolo non trovato sul broker" | *Orari e simboli → Nomi alternativi*: aggiungi `NOME_DEL_CANALE=NOME_DEL_BROKER` |
| Un messaggio non viene riconosciuto | incollalo in **Prova un messaggio** e aggiungi la parola mancante in *Parole chiave* |
| "RIFIUTATO … Invalid stops" | il prezzo è già oltre lo SL o il TP del segnale |
| "prezzo troppo lontano dall'entrata" | è la protezione *Distanza massima*: aumentala o mettila a 0 |

**Avvertenza:** il programma esegue i segnali e le tue regole ma non garantisce guadagni; il trading comporta il rischio di perdere il capitale. Fai sempre prima le prove sul conto demo.
