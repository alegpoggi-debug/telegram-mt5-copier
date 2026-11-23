"""Lettura dei messaggi del canale con regole fisse (nessuna AI, nessun costo).

Riconosce:
  - nuovi segnali:  "BUY GOLD 2350 SL 2340 TP 2360 TP 2370", "XAUUSD SELL LIMIT @ 2380-2385 SL: 2392 TP1: 2370 ..."
  - gestione: SL a pareggio, nuovo SL, chiusura totale/parziale, cancellazione, TP raggiunto
Tutte le parole chiave sono personalizzabili dalle impostazioni.
"""
from __future__ import annotations

import re

from .settings import parse_aliases, split_words

CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "XAU", "XAG", "BTC", "ETH", "SGD", "HKD",
              "NOK", "SEK", "ZAR", "MXN", "TRY", "PLN", "CNH", "XRP", "LTC", "SOL"}
KNOWN_SYMBOLS = {"US30", "NAS100", "US100", "US500", "SPX500", "GER40", "GER30", "DE40", "UK100", "FRA40", "JP225",
                 "AUS200", "HK50", "USOIL", "UKOIL", "WTI", "BRENT", "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "USTEC",
                 "NGAS", "EU50", "ESP35", "VIX", "DXY"}

NUM = r"(?<![A-Z0-9.])(\d+(?:\.\d+)?)(?![A-Z0-9])"


def _kw_pattern(words: list[str], digit_after: bool = False) -> str | None:
    words = sorted({w.upper() for w in words if w.strip()}, key=len, reverse=True)
    if not words:
        return None
    alts = "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in words)
    after = r"(?![A-Z])" if digit_after else r"(?![A-Z0-9])"
    return rf"(?<![A-Z0-9])(?:{alts}){after}"


def _has(text: str, words: list[str]) -> bool:
    p = _kw_pattern(words)
    return bool(p and re.search(p, text))


def normalize(text: str) -> str:
    t = (text or "").upper()
    t = t.replace(" ", " ")
    t = re.sub(r"(\d),(\d{1,5})(?!\d)", r"\1.\2", t)      # 2350,5 -> 2350.5
    t = re.sub(r"[–—−]", "-", t)
    t = re.sub(r"[*_`~|]", " ", t)                          # formattazione Telegram
    return t


def find_symbol(t: str, aliases: dict, extra: list[str]) -> str | None:
    tokens = re.findall(r"[#$]?[A-Z][A-Z0-9]*(?:[/.\-][A-Z0-9]+)?", t)
    extra_up = {e.upper() for e in extra}
    for raw in tokens:
        tok = raw.lstrip("#$")
        compact = tok.replace("/", "").replace("-", "")
        if compact in aliases:
            return aliases[compact]
        if tok in aliases:
            return aliases[tok]
        base = compact.split(".")[0]
        if base in extra_up or base in KNOWN_SYMBOLS:
            return base
        if len(base) == 6 and base[:3] in CURRENCIES and base[3:] in CURRENCIES:
            return base
    return None


def parse_message(text: str, s: dict, broker_symbols: list[str] | None = None) -> dict:
    """s = impostazioni (dict). Ritorna un dict con 'kind' e i dettagli."""
    t = normalize(text)
    res = {"kind": "none", "side": None, "symbol": None, "order_type": "market", "entry": None, "entry2": None,
           "sl": None, "tps": [], "percent": None, "new_sl": None, "info": ""}
    if not t.strip():
        return res
    if split_words(s["kw_ignore"]) and _has(t, split_words(s["kw_ignore"])):
        res["kind"] = "ignore"
        res["info"] = "contiene una parola da ignorare"
        return res

    aliases = parse_aliases(s["symbol_aliases"])
    extra = split_words(s["allowed_symbols"]) + [b.split(".")[0] for b in (broker_symbols or [])]
    symbol = find_symbol(t, aliases, extra)
    res["symbol"] = symbol

    # --- direzione e tipo ordine
    buy_p = _kw_pattern(split_words(s["kw_buy"]))
    sell_p = _kw_pattern(split_words(s["kw_sell"]))
    m_buy = re.search(buy_p, t) if buy_p else None
    m_sell = re.search(sell_p, t) if sell_p else None
    side = None
    if m_buy and (not m_sell or m_buy.start() < m_sell.start()):
        side = "buy"
    elif m_sell:
        side = "sell"

    work = t
    lim_p = _kw_pattern(split_words(s["kw_limit"]))
    stp_p = _kw_pattern(split_words(s["kw_stop_order"]))
    if side:
        side_p = buy_p if side == "buy" else sell_p
        if lim_p and re.search(rf"{side_p}\s*{lim_p}|{lim_p}\s*{side_p}", work):
            res["order_type"] = "limit"
            work = re.sub(rf"{side_p}\s*{lim_p}|{lim_p}\s*{side_p}", " SIDE ", work, count=1)
        elif stp_p and re.search(stp_p, work):
            res["order_type"] = "stop"
            work = re.sub(stp_p, " SIDE ", work, count=1)
        elif lim_p and re.search(lim_p, work):
            res["order_type"] = "limit"
            work = re.sub(lim_p, " ", work, count=1)
        # "BUY STOP" scritto come ordine: se dopo la parola di direzione c'è "STOP" seguito da un numero
        m = re.search(rf"{side_p}\s+STOP(?!\s*LOSS)(?![A-Z])", work)
        if m:
            res["order_type"] = "stop"
            work = work[:m.start()] + " SIDE " + work[m.end():]

    # --- SL
    sl_p = _kw_pattern(split_words(s["kw_sl"]), digit_after=True)
    sl_spans = []
    if sl_p:
        for m in re.finditer(rf"{sl_p}\s*(?:\d{{1,2}}(?![\d.]))?\s*[:=@\-]?\s*(?:A|AT|TO|A QUOTA)?\s*{NUM}", work):
            if res["sl"] is None:
                res["sl"] = float(m.group(1))
            sl_spans.append(m.span())

    # --- TP (TP, TP1, TP 2: …)
    tp_p = _kw_pattern(split_words(s["kw_tp"]), digit_after=True)
    tp_spans = []
    if tp_p:
        for m in re.finditer(rf"{tp_p}\s*(?:\d{{1,2}}(?![\d.]))?\s*[:=@\-]?\s*{NUM}", work):
            if any(a <= m.start() < b for a, b in sl_spans):
                continue
            res["tps"].append(float(m.group(1)))
            tp_spans.append(m.span())

    # --- entrata
    rest = work
    for a, b in sorted(sl_spans + tp_spans, reverse=True):
        rest = rest[:a] + " " * (b - a) + rest[b:]
    entry_p = _kw_pattern(split_words(s["kw_entry"]))
    rng = rf"{NUM}(?:\s*(?:-|/|TO|A)\s*{NUM})?"
    m = re.search(rf"{entry_p}\s*(?:ZONE|ZONA|AREA)?\s*[:=@\-]?\s*{rng}", rest) if entry_p else None
    if not m:
        # primo numero dopo la direzione (stessa riga o subito dopo)
        anchor = re.search(r"SIDE", rest) or (re.search(buy_p if side == "buy" else sell_p, rest) if side else None)
        if anchor:
            m = re.search(rf"^[^\n]*?{rng}", rest[anchor.end():])
            if m:
                res["entry"] = float(m.group(1))
                res["entry2"] = float(m.group(2)) if m.group(2) else None
    else:
        res["entry"] = float(m.group(1))
        res["entry2"] = float(m.group(2)) if m.group(2) else None
    if res["entry"] is not None and symbol and symbol.endswith(res["entry"].__str__().split(".")[0]):
        res["entry"] = None  # era un numero dentro il nome del simbolo

    pct = re.search(r"(\d{1,3})\s*%", t)

    # --- nuovo segnale?
    if side and (res["sl"] is not None or res["tps"]):
        res["kind"] = "signal"
        res["side"] = side
        if not symbol and s.get("default_symbol"):
            res["symbol"] = s["default_symbol"].strip().upper()
        if not res["symbol"]:
            res["info"] = "simbolo non riconosciuto"
        return res

    # --- messaggi di gestione
    if _has(t, split_words(s["kw_cancel"])):
        res["kind"] = "cancel"
    elif _has(t, split_words(s["kw_close_partial"])) or (pct and _has(t, split_words(s["kw_close_all"]))):
        res["kind"] = "partial"
        res["percent"] = float(pct.group(1)) if pct else None
        if "METÀ" in t or "META" in t or "HALF" in t:
            res["percent"] = res["percent"] or 50.0
        if _has(t, split_words(s["kw_breakeven"])):
            res["also_breakeven"] = True
    elif _has(t, split_words(s["kw_breakeven"])):
        res["kind"] = "breakeven"
    elif _has(t, split_words(s["kw_move_sl"])) and re.search(NUM, t):
        mv = _kw_pattern(split_words(s["kw_move_sl"]))
        m = re.search(rf"{mv}\s*(?:A|AT|TO)?\s*[:=@\-]?\s*{NUM}", t)
        if m:
            res["kind"] = "move_sl"
            res["new_sl"] = float(m.group(1))
    elif _has(t, split_words(s["kw_tp_hit"])):
        res["kind"] = "tp_hit"
    elif _has(t, split_words(s["kw_close_all"])):
        res["kind"] = "close"
    elif side and res["entry"] is not None:
        res["kind"] = "signal"
        res["side"] = side
        res["info"] = "segnale senza SL e senza TP"
    return res


def fingerprint(r: dict, cid=None, reply_id=None):
    """Impronta di un messaggio: due messaggi con la stessa impronta sono "lo stesso messaggio".

    Segnali: stesso simbolo, direzione, tipo, entrata, SL e TP (anche se scritti in modo diverso).
    Gestione: stesso tipo e parametri, nello stesso canale e in risposta allo stesso messaggio.
    """
    def rd(v):
        return None if v is None else round(float(v), 5)
    k = r.get("kind")
    if k == "signal":
        return ("signal", (r.get("symbol") or "").upper(), r.get("side"), r.get("order_type"), rd(r.get("entry")),
                rd(r.get("entry2")), rd(r.get("sl")), tuple(rd(t) for t in r.get("tps") or []))
    if k in ("breakeven", "move_sl", "close", "partial", "cancel", "tp_hit"):
        return (k, cid, reply_id, rd(r.get("percent")), rd(r.get("new_sl")), (r.get("symbol") or "").upper(),
                bool(r.get("also_breakeven")))
    return None


def _same_signal(a, b) -> bool:
    """Due segnali sono lo stesso segnale anche se riscritti in modo un po' diverso:
    stesso simbolo, direzione e Stop Loss; entrata compatibile (stesso prezzo o fascia che si tocca,
    fascia scritta al contrario, oppure uno dei due senza prezzo); TP uguali in qualsiasi ordine,
    oppure uno contiene l'altro (TP aggiunto o tolto nel messaggio ripetuto)."""
    _k1, sym1, side1, _ot1, e1, e1b, sl1, tps1 = a
    _k2, sym2, side2, _ot2, e2, e2b, sl2, tps2 = b
    if (sym1, side1, sl1) != (sym2, side2, sl2):
        return False
    if e1 is not None and e2 is not None:
        lo1, hi1 = min(e1, e1b if e1b is not None else e1), max(e1, e1b if e1b is not None else e1)
        lo2, hi2 = min(e2, e2b if e2b is not None else e2), max(e2, e2b if e2b is not None else e2)
        if lo1 > hi2 or lo2 > hi1:
            return False
    t1, t2 = set(tps1), set(tps2)
    return t1 <= t2 or t2 <= t1


def find_duplicate(fp, seen):
    """Impronta già vista uguale (o, per i segnali, lo stesso segnale riscritto): ritorna quella, altrimenti None."""
    if fp in seen:
        return fp
    if fp and fp[0] == "signal":
        for old in seen:
            if old and old[0] == "signal" and _same_signal(fp, old):
                return old
    return None


def describe(r: dict) -> str:
    k = r["kind"]
    if k == "signal":
        tps = ", ".join(f"{x:g}" for x in r["tps"]) or "nessuno"
        entry = "mercato" if r["entry"] is None else f"{r['entry']:g}" + (f"-{r['entry2']:g}" if r.get("entry2") else "")
        sl = "nessuno" if r["sl"] is None else f"{r['sl']:g}"
        extra = f" ({r['info']})" if r.get("info") else ""
        return (f"SEGNALE {r['side'].upper()} {r['order_type'].upper()} {r['symbol'] or '?'} — "
                f"entrata {entry}, SL {sl}, TP {tps}{extra}")
    return {
        "breakeven": "Gestione: SL a pareggio",
        "move_sl": f"Gestione: nuovo SL a {r.get('new_sl')}",
        "close": "Gestione: chiudere le posizioni",
        "partial": f"Gestione: chiusura parziale {r.get('percent') or ''}{'%' if r.get('percent') else ''}"
                   + (" + SL a pareggio" if r.get("also_breakeven") else ""),
        "cancel": "Gestione: cancellare gli ordini pendenti",
        "tp_hit": "Info: TP raggiunto",
        "ignore": f"Ignorato ({r.get('info')})",
        "none": "Nessuna azione (messaggio non riconosciuto come segnale o gestione)",
    }.get(k, k)
