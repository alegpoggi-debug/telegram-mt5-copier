"""Tutte le impostazioni del programma (salvate in data/settings.json).

SCHEMA descrive ogni impostazione: la finestra Impostazioni viene costruita da qui.
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

# ---------------------------------------------------------------- parole chiave predefinite
DEFAULT_KEYWORDS = {
    "kw_buy": "BUY, COMPRA, COMPRARE, LONG, ACQUISTA",
    "kw_sell": "SELL, VENDI, VENDERE, SHORT",
    "kw_limit": "LIMIT",
    "kw_stop_order": "STOP ORDER, BUY STOP, SELL STOP",
    "kw_entry": "ENTRY, ENTRATA, INGRESSO, PREZZO, PRICE, @, OPEN, NOW",
    "kw_sl": "SL, S/L, S.L., STOP LOSS, STOPLOSS, STOP",
    "kw_tp": "TP, T/P, T.P., TAKE PROFIT, TAKEPROFIT, TARGET, TG",
    "kw_breakeven": "BREAKEVEN, BREAK EVEN, BE, PAREGGIO, SL A PAREGGIO, SL IN BE, SL TO ENTRY, MOVE SL TO ENTRY, RISK FREE",
    "kw_close_all": "CLOSE ALL, CLOSE NOW, CHIUDETE, CHIUDI, CHIUDERE TUTTO, CLOSE, EXIT NOW",
    "kw_close_partial": "CHIUDETE METÀ, CHIUDETE META, CLOSE HALF, CLOSE PARTIAL, PARZIALE, PARZIALI, TAKE PARTIAL, SECURE PROFIT",
    "kw_cancel": "CANCEL, CANCELLA, CANCELLATE, ANNULLA, DELETE, ELIMINA, ORDINE CANCELLATO",
    "kw_move_sl": "MOVE SL, NEW SL, NUOVO SL, SPOSTA SL, SPOSTATE SL, SL A",
    "kw_tp_hit": "TP1 HIT, TP 1 HIT, TP1 PRESO, TP1 RAGGIUNTO, TP HIT, TARGET HIT, TP1 ✅, TP1✅",
    "kw_ignore": "",
}

DEFAULT_ALIASES = "GOLD=XAUUSD\nORO=XAUUSD\nXAU=XAUUSD\nSILVER=XAGUSD\nARGENTO=XAGUSD\nDOW=US30\nDJ30=US30\nNASDAQ=NAS100\nNAS=NAS100\nUSTEC=NAS100\nSP500=US500\nSPX=US500\nDAX=GER40\nGER30=GER40\nBTC=BTCUSD\nBITCOIN=BTCUSD\nOIL=USOIL\nPETROLIO=USOIL"

DEFAULTS = {
    # Telegram
    "tg_api_id": "",
    "tg_api_hash": "",
    "tg_channels": [],  # lista di {"id": int, "name": str}
    # MetaTrader
    "mt5_dir": "",
    "magic": 770077,
    "slippage_points": 30,   # solo per l'avviso: esecuzione peggiore di questi punti (non annulla nulla)
    # Volume e rischio
    "dry_run": True,
    "allow_real": False,
    "paused": False,
    "lot_mode": "fixed",
    "fixed_lot": 0.01,
    "risk_percent": 1.0,
    "risk_money": 10.0,
    "max_lot_per_trade": 0.10,
    "max_total_lots": 0.50,
    "max_open_trades": 6,
    "max_trades_per_symbol": 3,
    "daily_loss_limit": 0.0,
    "daily_profit_target": 0.0,
    # Ingresso
    "multi_tp_mode": "split",
    "tp_index": 1,
    "lot_split": "",
    "order_type_mode": "as_signal",
    "max_entry_distance_points": 0,
    "ignore_duplicates": True,
    "duplicate_window_min": 10,
    "radar_channels": [],  # canali solo osservati dal Radar: lista di {"id": int, "name": str}
    "radar_days": 30,
    "channel_overrides": {},  # impostazioni personalizzate per canale: {"id canale": {chiave: valore}}
    "removed_chats": [],      # canali tolti dall'elenco con il cestino
    "zone_wait": False,
    "zone_wait_seconds": 15,
    "min_sl_distance_pips": 40,   # non entra se il prezzo è a questi pips (o meno) dallo Stop Loss; 0 = no
    "zone_margin_points": 0,
    "max_spread_points": 0,
    "pending_expiry_min": 0,
    "no_sl_mode": "ignore",
    "default_sl_points": 300,
    "no_tp_mode": "open",
    "default_tp_points": 300,
    "sl_adjust_points": 0,
    "tp_adjust_points": 0,
    "reverse": False,
    "default_symbol": "",
    "direction_filter": "both",
    # Orari e simboli
    "hours_enabled": False,
    "hours_start": "08:00",
    "hours_end": "22:00",
    "days": [0, 1, 2, 3, 4],
    "allowed_symbols": "",
    "blocked_symbols": "",
    "symbol_aliases": DEFAULT_ALIASES,
    # Gestione automatica
    "be_on_tp1": True,
    "be_offset_points": 10,
    "be_after_points": 0,
    "trailing_points": 0,
    "trailing_step_points": 10,
    "be_on_tp2": False,            # dopo il secondo TP, SL a pareggio sulle posizioni rimaste
    "sl_to_tp1_after_tp2": False,  # dopo il secondo TP, SL al livello del primo TP
    "max_hours_open": 0,           # chiude l'operazione dopo queste ore (0 = mai)
    # Messaggi del canale
    "updates_target": "last_signal",
    "follow_edits": True,
    "follow_breakeven": True,
    "follow_close": True,
    "follow_partial": True,
    "partial_percent": 50,
    "follow_cancel": True,
    "follow_move_sl": True,
    "tp_hit_action": "nothing",
    "tp_hit_partial_percent": 50,
    **DEFAULT_KEYWORDS,
}

# ---------------------------------------------------------------- schema per la finestra
# (chiave, etichetta, tipo, opzioni, aiuto)
SCHEMA = {
    "Volume e rischio": [
        ("dry_run", "Modalità PROVA (legge e mostra, ma non apre ordini)", "bool", None, "Tienila attiva finché non hai verificato che tutto funziona."),
        ("allow_real", "Permetti operazioni sul conto REALE", "bool", None, "Se disattivato, sul conto reale il programma non apre nulla."),
        ("lot_mode", "Come calcolare il lotto", "choice", [("fixed", "Lotto fisso per ogni posizione"), ("risk_percent", "% del saldo rischiata per segnale"), ("risk_money", "Importo fisso rischiato per segnale")], None),
        ("fixed_lot", "Lotto fisso", "float", (0.01, 100, 2), None),
        ("risk_percent", "Rischio % per segnale", "float", (0.01, 20, 2), "Diviso tra le posizioni del segnale. Serve lo SL."),
        ("risk_money", "Importo rischiato per segnale (valuta conto)", "float", (0.1, 100000, 2), "Diviso tra le posizioni del segnale. Serve lo SL."),
        ("max_lot_per_trade", "Lotto massimo per singola posizione", "float", (0.01, 100, 2), None),
        ("max_total_lots", "Lotti totali massimi aperti", "float", (0.01, 1000, 2), None),
        ("max_open_trades", "Posizioni aperte massime", "int", (1, 500), None),
        ("max_trades_per_symbol", "Posizioni massime per simbolo", "int", (1, 500), None),
        ("daily_loss_limit", "Perdita massima giornaliera (0 = nessuna)", "float", (0, 1e9, 2), "Raggiunta, non apre più fino a domani."),
        ("daily_profit_target", "Obiettivo di profitto giornaliero (0 = nessuno)", "float", (0, 1e9, 2), "Raggiunto, non apre più fino a domani."),
    ],
    "Ingresso": [
        ("multi_tp_mode", "Segnali con più TP", "choice", [("split", "Una posizione per ogni TP"), ("first", "Una posizione, solo primo TP"), ("last", "Una posizione, solo ultimo TP"), ("index", "Una posizione, TP numero…")], None),
        ("tp_index", "TP numero (se scelto sopra)", "int", (1, 10), None),
        ("lot_split", "Ripartizione lotto tra i TP (es. 50,30,20 — vuoto = uguale)", "str", None, "Percentuali del lotto/rischio per TP1, TP2, TP3… Con lotto fisso, il lotto fisso diventa il totale del segnale."),
        ("order_type_mode", "Tipo di ordine", "choice", [("as_signal", "Come indicato nel segnale"), ("market", "Sempre a mercato"), ("limit", "Sempre pendente al prezzo di entrata")], None),
        ("max_entry_distance_points", "Distanza massima dal prezzo di entrata (punti, 0 = nessun controllo)", "int", (0, 1000000), "Se il prezzo è già troppo lontano dall'entrata del segnale, non entra."),
        ("pending_expiry_min", "Scadenza ordini pendenti (minuti, 0 = mai)", "int", (0, 100000), None),
        ("no_sl_mode", "Segnali senza Stop Loss", "choice", [("ignore", "Ignora il segnale"), ("default", "Usa lo SL predefinito"), ("none", "Apri senza SL (rischioso)")], None),
        ("default_sl_points", "SL predefinito (punti)", "int", (1, 1000000), None),
        ("no_tp_mode", "Segnali senza Take Profit", "choice", [("open", "Apri senza TP"), ("default", "Usa il TP predefinito")], None),
        ("default_tp_points", "TP predefinito (punti)", "int", (1, 1000000), None),
        ("sl_adjust_points", "Allarga lo SL di (punti)", "int", (-100000, 100000), "Positivo = SL più lontano, negativo = più vicino."),
        ("tp_adjust_points", "Anticipa il TP di (punti)", "int", (-100000, 100000), "Positivo = TP più vicino (più facile da prendere)."),
        ("default_symbol", "Simbolo da usare se il segnale non lo indica (vuoto = ignora)", "str", None, "Utile se il canale fa segnali solo su uno strumento, es. XAUUSD."),
        ("reverse", "Inverti i segnali (BUY ↔ SELL)", "bool", None, None),
        ("direction_filter", "Direzioni ammesse", "choice", [("both", "BUY e SELL"), ("buy", "Solo BUY"), ("sell", "Solo SELL")], None),
    ],
    "Orari e simboli": [
        ("hours_enabled", "Opera solo in questi orari", "bool", None, "Ora del Mac. Fuori orario i nuovi segnali vengono ignorati (la gestione continua)."),
        ("hours_start", "Dalle (HH:MM)", "time", None, None),
        ("hours_end", "Alle (HH:MM)", "time", None, None),
        ("days", "Giorni attivi", "days", None, None),
        ("allowed_symbols", "Simboli ammessi (separati da virgola, vuoto = tutti)", "str", None, None),
        ("blocked_symbols", "Simboli bloccati (separati da virgola)", "str", None, None),
        ("symbol_aliases", "Nomi alternativi (uno per riga: NOME_CANALE=NOME_BROKER)", "text", None, "Il suffisso del broker (es. .m, .pro) viene trovato da solo."),
    ],
    "Gestione automatica": [
        ("be_on_tp1", "Quando una posizione chiude al TP, sposta le altre dello stesso segnale a pareggio", "bool", None, None),
        ("be_offset_points", "Pareggio + (punti)", "int", (-10000, 10000), "Lo SL va al prezzo di entrata più questi punti, per coprire commissioni."),
        ("be_after_points", "Sposta a pareggio dopo X punti di profitto (0 = no)", "int", (0, 1000000), None),
        ("trailing_points", "Trailing stop a X punti (0 = no)", "int", (0, 1000000), None),
        ("trailing_step_points", "Passo del trailing (punti)", "int", (1, 100000), None),
    ],
    "Messaggi del canale": [
        ("updates_target", "Gli aggiornamenti senza 'rispondi' valgono per", "choice", [("last_signal", "L'ultimo segnale del canale"), ("all_channel", "Tutte le posizioni del canale")], "Se il messaggio è una risposta a un segnale vale sempre per quel segnale."),
        ("follow_edits", "Segui i messaggi modificati (aggiorna SL/TP o apre se diventa un segnale)", "bool", None, None),
        ("follow_breakeven", "Esegui 'SL a pareggio' del canale", "bool", None, None),
        ("follow_move_sl", "Esegui 'nuovo SL a …' del canale", "bool", None, None),
        ("follow_close", "Esegui 'chiudete' del canale", "bool", None, None),
        ("follow_partial", "Esegui 'chiudete parziale' del canale", "bool", None, None),
        ("partial_percent", "Percentuale da chiudere nei parziali (se non indicata)", "int", (1, 99), None),
        ("follow_cancel", "Esegui 'cancellate l'ordine' del canale", "bool", None, None),
        ("tp_hit_action", "Quando il canale scrive 'TP raggiunto'", "choice", [("nothing", "Non fare nulla"), ("breakeven", "Sposta SL a pareggio"), ("partial", "Chiudi una parte"), ("partial_be", "Chiudi una parte e SL a pareggio")], None),
        ("tp_hit_partial_percent", "Parte da chiudere al 'TP raggiunto' (%)", "int", (1, 99), None),
    ],
    "Parole chiave": [
        ("kw_buy", "Parole per BUY", "str", None, None),
        ("kw_sell", "Parole per SELL", "str", None, None),
        ("kw_limit", "Parole per ordine LIMIT", "str", None, None),
        ("kw_stop_order", "Parole per ordine STOP (pendente)", "str", None, None),
        ("kw_entry", "Parole per il prezzo di entrata", "str", None, None),
        ("kw_sl", "Parole per Stop Loss", "str", None, None),
        ("kw_tp", "Parole per Take Profit", "str", None, None),
        ("kw_breakeven", "Frasi per 'SL a pareggio'", "str", None, None),
        ("kw_move_sl", "Frasi per 'nuovo SL a …'", "str", None, None),
        ("kw_close_all", "Frasi per 'chiudete'", "str", None, None),
        ("kw_close_partial", "Frasi per 'chiudete parziale'", "str", None, None),
        ("kw_cancel", "Frasi per 'cancellate l'ordine'", "str", None, None),
        ("kw_tp_hit", "Frasi per 'TP raggiunto'", "str", None, None),
        ("kw_ignore", "Ignora i messaggi che contengono", "str", None, "Es. RISULTATI, PROMO, VIP"),
    ],
}


# impostazioni che si possono personalizzare per ogni canale
CHANNEL_KEYS = ["lot_mode", "fixed_lot", "risk_percent", "risk_money", "multi_tp_mode", "no_sl_mode",
                "default_sl_points", "follow_breakeven", "be_on_tp1", "be_after_points", "be_offset_points",
                "tp_hit_action", "zone_wait", "zone_wait_seconds", "min_sl_distance_pips"]


def for_channel(general: dict, cid) -> dict:
    """Impostazioni valide per un canale: quelle generali con le personalizzazioni del canale."""
    s = dict(general)
    over = (general.get("channel_overrides") or {}).get(str(cid)) or {}
    s.update({k: v for k, v in over.items() if k in CHANNEL_KEYS})
    return s


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULTS)
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.data.update({k: v for k, v in saved.items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass
        self.save()

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)

    def __getitem__(self, key):
        with self._lock:
            return copy.deepcopy(self.data[key])

    def update(self, changes: dict):
        with self._lock:
            for k, v in changes.items():
                if k in DEFAULTS:
                    self.data[k] = v
            self._save_locked()

    def save(self):
        with self._lock:
            self._save_locked()

    def _save_locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def split_words(s: str) -> list[str]:
    return [w.strip() for w in (s or "").split(",") if w.strip()]


def parse_aliases(s: str) -> dict:
    out = {}
    for line in (s or "").splitlines():
        if "=" in line:
            a, b = line.split("=", 1)
            if a.strip() and b.strip():
                out[a.strip().upper()] = b.strip().upper()
    return out
