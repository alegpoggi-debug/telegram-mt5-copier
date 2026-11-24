"""Radar: come sarebbe andato un canale, senza aprire nessuna operazione.

Rilegge i messaggi del canale degli ultimi giorni (restano su Telegram) e li confronta con i prezzi
al minuto di MetaTrader (restano nello storico del broker), usando le impostazioni attuali.
Funziona quindi anche se il computer è rimasto spento: si recupera tutto all'apertura.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import DATA_DIR
from .parser import describe, entry_window, find_duplicate, fingerprint, parse_message

HORIZON = 60 * 86400         # un segnale viene seguito fino alla chiusura (al massimo 60 giorni)
RADAR_DIR = DATA_DIR / "radar"
CACHE_DIR = RADAR_DIR / "prezzi"


# ============================================================================ ora del broker
def _nth_sunday(year, month, n):
    first = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def us_dst(utc_ts: float) -> bool:
    d = dt.datetime.fromtimestamp(utc_ts, dt.timezone.utc)
    start = _nth_sunday(d.year, 3, 2).replace(hour=7)
    end = _nth_sunday(d.year, 11, 1).replace(hour=6)
    return start <= d < end


def server_offset(utc_ts: float, base: int) -> int:
    """Differenza ora del broker − UTC in quel momento.

    Quasi tutti i broker MetaTrader usano GMT+2 d'inverno e GMT+3 d'estate (cambio insieme agli USA):
    in quel caso l'ora legale viene applicata data per data; altrimenti la differenza è fissa.
    """
    if base in (7200, 10800):
        return 7200 + 3600 * us_dst(utc_ts)
    return base


def measure_offset(server_now: int, utc_now: float, saved: int | None) -> int:
    """Differenza attuale broker − UTC (se il mercato è chiuso usa l'ultima misurata)."""
    raw = server_now - utc_now
    q = round(raw / 900) * 900
    if server_now and abs(raw - q) < 180 and abs(q) <= 14 * 3600:
        return int(q)
    return saved if saved is not None else 7200


# ============================================================================ risultati
@dataclass
class RSignal:
    msg_id: int
    t: int                       # ora del broker del messaggio
    symbol: str
    side: str
    otype: str
    entry: float | None
    entry2: float | None
    sl: float | None
    tps: list
    text: str
    status: str = ""             # vinto / perso / pareggio / aperto / ignorato: … / scaduto / senza prezzi
    points: float = 0.0
    money: float | None = None
    entry_t: int | None = None
    entry_p: float | None = None
    exit_t: int | None = None
    exit_p: float | None = None
    sl_now: float | None = None
    tp_first: float | None = None
    exits: list = field(default_factory=list)
    edited: bool = False         # il canale ha modificato il messaggio dopo averlo inviato
    vpp: float | None = None     # valore di 1 punto con 1 lotto, in dollari (None = non stimabile)
    vpp_acc: float | None = None  # valore esatto di 1 punto con 1 lotto nella valuta del conto (dati di MetaTrader)
    lots: float = 1.0            # quante volte il lotto scelto viene usato (una posizione per TP)
    swap_lot: float | None = None  # swap totale con 1 lotto, valuta del conto (None = non noto)
    exit_reason: str = ""        # TP, SL, Pareggio, TP + pareggio, Chiuso dal canale…
    spark: list = field(default_factory=list)   # miniatura del grafico: [[tempo, prezzo], …]


@dataclass
class RMessage:
    msg_id: int
    t: int
    text: str
    reading: str
    note: str = ""
    edited: bool = False


@dataclass
class Report:
    channel_id: int
    channel: str
    days: int
    generated: str
    signals: list
    messages: list
    stats: dict
    equity: list
    error: str = ""
    spreads: dict = field(default_factory=dict)      # spread usato nella stima, in punti, per simbolo
    usd_to_acc: float | None = None                  # 1 dollaro in valuta del conto
    currency: str = "USD"
    note: str = ""
    precise: bool = False                            # spread storico e dati esatti del simbolo dal ponte
    settings_sig: str = ""                           # impostazioni del Radar usate per questa analisi

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False)

    @staticmethod
    def from_json(text: str) -> "Report":
        d = json.loads(text)
        d["signals"] = [RSignal(**x) for x in d["signals"]]
        d["messages"] = [RMessage(**x) for x in d["messages"]]
        return Report(**d)


def save_report(rep: Report):
    RADAR_DIR.mkdir(parents=True, exist_ok=True)
    (RADAR_DIR / f"canale_{rep.channel_id}.json").write_text(rep.to_json(), encoding="utf-8")


def load_report(cid: int) -> Report | None:
    p = RADAR_DIR / f"canale_{cid}.json"
    try:
        return Report.from_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, KeyError):
        return None


# ============================================================================ simulazione
CONTRACT = {"XAU": 100, "XAG": 5000, "XPT": 100, "XPD": 100, "BTC": 1, "ETH": 1, "LTC": 1, "XRP": 1, "SOL": 1}
CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "XAU", "XAG", "XPT", "XPD", "BTC", "ETH", "LTC",
              "XRP", "SOL", "SGD", "HKD", "NOK", "SEK", "ZAR", "MXN", "TRY", "PLN", "CNH"}


def _point(bars) -> float:
    """Dimensione di un punto dedotta dai prezzi (solo se MetaTrader non l'ha fornita)."""
    best = 0
    for b in bars[-300:]:
        for v in b[1:5]:
            txt = f"{v:.6f}".rstrip("0")
            best = max(best, len(txt.split(".")[1]) if "." in txt else 0)
    return 10 ** -best if best else 1.0


def value_per_point(symbol: str, point: float, price: float) -> float | None:
    """Quanti dollari vale 1 punto con 1 lotto (contratti standard). None se non stimabile (indici, cross…)."""
    letters = re.sub(r"[^A-Z]", "", symbol.upper().split(".")[0])
    if len(letters) < 6 or letters[:3] not in CURRENCIES or letters[3:6] not in CURRENCIES:
        return None
    base, quote = letters[:3], letters[3:6]
    contract = CONTRACT.get(base, 100000)
    value_in_quote = contract * point
    if quote == "USD":
        return value_in_quote
    if base == "USD" and price:
        return value_in_quote / price
    return None


def _weights(n, s) -> list[float]:
    """Peso di ogni posizione rispetto al lotto scelto (come il lotto fisso del programma vero)."""
    ws = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s.get("lot_split") or "")][:n]
    if ws and sum(ws) > 0:
        if len(ws) < n:
            rest = max(0.0, 100 - sum(ws))
            ws += [rest / (n - len(ws))] * (n - len(ws))
        tot = sum(ws)
        return [w / tot for w in ws]          # ripartizione: il lotto è il totale del segnale
    return [1.0] * n                          # senza ripartizione: ogni posizione ha il lotto intero


def _blocked(s, side, symbol, utc_ts) -> str | None:
    """Filtri delle impostazioni: direzione, simboli, giorni e orari (ora del Mac, come la copia vera)."""
    from .settings import split_words
    if s["direction_filter"] != "both" and side != s["direction_filter"]:
        return f"solo {s['direction_filter'].upper()}"
    base = re.sub(r"[^A-Z0-9]", "", symbol.upper().split(".")[0])
    if any(base.startswith(re.sub(r"[^A-Z0-9]", "", b.upper())) for b in split_words(s["blocked_symbols"]) if b):
        return "simbolo bloccato"
    allowed = [re.sub(r"[^A-Z0-9]", "", a.upper()) for a in split_words(s["allowed_symbols"]) if a]
    if allowed and not any(base.startswith(a) for a in allowed):
        return "simbolo non ammesso"
    local = dt.datetime.fromtimestamp(utc_ts)
    if local.weekday() not in s["days"]:
        return "giorno non attivo"
    if s["hours_enabled"]:
        a, b, hm = s["hours_start"], s["hours_end"], local.strftime("%H:%M")
        inside = (a <= hm <= b) if a <= b else (hm >= a or hm <= b)
        if not inside:
            return "fuori orario"
    return None


EXEC_DELAY = 1   # secondi tra il messaggio e l'ordine nella copia vera


def price_at(bars, times, t):
    """Prezzo (bid) stimato in un istante: interpolato dentro la candela al minuto che lo contiene."""
    i = bisect.bisect_right(times, t) - 1
    if i < 0 or t - times[i] >= 120:
        return None, None
    o, c = bars[i][1], bars[i][4]
    frac = min(1.0, max(0.0, (t - times[i]) / 60))
    return o + (c - o) * frac, i


def _nights(t1, t2, swap3):
    """Notti di swap tra due istanti (ora del broker). swap3: giorno del triplo (0=domenica … 6=sabato)."""
    n = 0
    d = (int(t1) // 86400 + 1) * 86400          # prima mezzanotte dopo l'entrata
    while d <= t2:
        day_before = dt.datetime.fromtimestamp(d - 1, dt.timezone.utc)
        mql_wd = (day_before.weekday() + 1) % 7   # lunedì=1 … domenica=0
        if 1 <= mql_wd <= 5:
            n += 3 if mql_wd == swap3 else 1
        d += 86400
    return n


def simulate_signal(sig: RSignal, bars: list, events: list, s: dict, spread: float = 0.0, point: float | None = None,
                    spec: dict | None = None, max_positions: int | None = None):
    """Simula un segnale sulle candele M1 (prezzi bid, come in MetaTrader).

    spread: spread di riserva (se le candele non hanno quello storico). Dentro ogni minuto il prezzo
    segue il percorso apertura → minimo → massimo → chiusura (o massimo prima del minimo se la candela
    è ribassista), come il Tester di MetaTrader: così si sa se arriva prima lo SL o il TP.
    """
    if not bars:
        sig.status = "senza prezzi"
        return
    times = [b[0] for b in bars]
    pt = point or _point(bars)
    side = 1 if sig.side == "buy" else -1
    buy = side > 0

    def sp(j):
        b = bars[j]
        return b[5] * pt if len(b) > 5 and b[5] > 0 else spread

    t_exec = sig.t + EXEC_DELAY
    waited = False          # entrata dopo un'attesa (prezzo o ordine pendente): i messaggi di prima non la riguardano
    # ---- entrata
    if sig.otype == "market":
        bid, i = price_at(bars, times, t_exec)
        if bid is None:                         # messaggio in un momento senza prezzi (es. mercato chiuso)
            i = bisect.bisect_left(times, sig.t)
            if i >= len(bars) or bars[i][0] - sig.t > 3 * 3600:
                sig.status = "senza prezzi"
                return
            bid, entry_t = bars[i][1], bars[i][0]
        else:
            entry_t = t_exec
        first_check = i + 1
        price = bid + sp(i) if buy else bid
        if not s["reverse"]:
            # prezzi accettati (come la copia vera): margine dal prezzo del segnale dalla parte sfavorevole,
            # subito dalla parte favorevole, mai oltre lo SL né troppo vicino allo SL
            zone = s["zone_wait"] and sig.entry is not None
            wlo, whi = entry_window(buy, sig.entry, sig.entry2, sig.sl, s, pt, zone=zone)
            if (wlo is not None and price < wlo) or (whi is not None and price > whi):
                deadline = sig.t + max(0, s["zone_wait_seconds"])
                found = None
                for j in range(i + 1, len(bars)):
                    if bars[j][0] > deadline:
                        break
                    add = sp(j) if buy else 0.0
                    if (whi is None or bars[j][3] + add <= whi) and (wlo is None or bars[j][2] + add >= wlo):
                        found = (j, whi if whi is not None and price > whi else wlo)
                        break
                if not found:
                    near_sl = sig.sl and ((buy and wlo is not None and price < wlo) or
                                          (not buy and whi is not None and price > whi))
                    if near_sl:
                        sig.status = "ignorato: troppo vicino allo Stop Loss"
                    elif sig.entry2 is not None:
                        sig.status = "ignorato: prezzo fuori dalla fascia"
                    else:
                        sig.status = "ignorato: prezzo lontano da quello del segnale"
                    return
                first_check, price, entry_t = found[0], found[1], bars[found[0]][0]
                waited = True
        entry_p = price
    else:
        e = sig.entry
        expiry = s["pending_expiry_min"] * 60 if s["pending_expiry_min"] else None   # 0 = mai (come la copia)
        waited = True
        # «cancella» o «chiudete» prima dell'esecuzione: l'ordine pendente viene cancellato (come la copia vera)
        cancels = sorted(t for t, k, _p in events if k in ("cancel", "close"))
        found = None
        for j in range(bisect.bisect_left(times, sig.t), len(bars)):
            t, o, h, l_ = bars[j][:4]
            if (expiry is not None and t - sig.t > expiry) or any(ct <= t for ct in cancels) or t - sig.t > HORIZON:
                break
            add = sp(j) if buy else 0.0                 # i BUY si eseguono all'ask
            hh, ll, oo = h + add, l_ + add, o + add
            if sig.otype == "limit":
                hit = ll <= e if buy else hh >= e
                fill = min(oo, e) if buy else max(oo, e)
            else:
                hit = hh >= e if buy else ll <= e
                fill = max(oo, e) if buy else min(oo, e)
            if hit:
                found = (j, fill)
                break
        if found is None:
            sig.status = "ordine mai eseguito"
            return
        first_check = found[0] + 1
        entry_t, entry_p = bars[found[0]][0], found[1]
    sig.entry_t, sig.entry_p = entry_t, entry_p

    # ---- SL / TP come farebbe il programma
    sl = None if s["reverse"] else sig.sl
    if sl is None:
        if s["no_sl_mode"] == "default" or s["reverse"]:
            sl = entry_p - side * s["default_sl_points"] * pt
        elif s["no_sl_mode"] == "ignore":
            sig.status = "ignorato: senza Stop Loss"
            sig.entry_t = sig.entry_p = None
            return
    tps = [] if s["reverse"] else list(sig.tps)
    mode = s["multi_tp_mode"]
    if tps:
        if mode == "first":
            tps = tps[:1]
        elif mode == "last":
            tps = tps[-1:]
        elif mode == "index":
            tps = [tps[min(max(1, s["tp_index"]) - 1, len(tps) - 1)]]
    if not tps:
        tps = [entry_p + side * s["default_tp_points"] * pt] if (s["no_tp_mode"] == "default" or s["reverse"]) else [None]
    if max_positions is not None:
        tps = tps[:max(1, max_positions)]        # come la copia: oltre il limite si tagliano i TP in eccesso
    if sl is not None and s["sl_adjust_points"]:
        sl -= side * s["sl_adjust_points"] * pt
    tps = [tp - side * s["tp_adjust_points"] * pt if (tp is not None and s["tp_adjust_points"]) else tp for tp in tps]
    weights = _weights(len(tps), s)
    subs = [{"tp": tp, "sl": sl, "w": w, "open": w, "pnl": 0.0, "exit": None} for tp, w in zip(tps, weights)]
    sig.lots = sum(weights)
    sig.n_pos = len(subs)
    sig.tp_first = next((tp for tp in tps if tp is not None), None)
    be_price = entry_p + side * s["be_offset_points"] * pt

    # swap per notte con 1 lotto (valuta del conto)
    swap_night = None
    if spec and spec.get("vpp_acc"):
        rate = spec["swap_long"] if buy else spec["swap_short"]
        if spec.get("swap_mode") == 1:
            swap_night = rate * spec["vpp_acc"]
        elif spec.get("swap_mode") == 4:
            swap_night = rate
        elif spec.get("swap_mode") == 0:
            swap_night = 0.0
    swap_total = 0.0

    def close(sub, frac_w, exit_price, t, reason="sl"):
        nonlocal swap_total
        sub["reason"] = reason
        sub["pnl"] += (exit_price - entry_p) * side / pt * frac_w
        sub["open"] -= frac_w
        sig.exits.append((int(t), round(exit_price, 6)))
        if swap_night is not None:
            swap_total += swap_night * frac_w * _nights(entry_t, t, spec.get("swap3", 3))
        if sub["open"] <= 1e-9:
            sub["open"] = 0.0
            sub["exit"] = (t, exit_price)

    def to_be():
        for sub in subs:
            if sub["open"] > 0 and (sub["sl"] is None or (buy and sub["sl"] < be_price) or (not buy and sub["sl"] > be_price)):
                sub["sl"] = be_price

    def sl_reason(sub):
        return "be" if sub["sl"] is not None and abs(sub["sl"] - be_price) < pt / 2 else "sl"

    def on_tp():
        """Dopo ogni TP preso: pareggio sulle altre posizioni (se scelto)."""
        if s["be_on_tp1"]:
            to_be()

    def walk(j):
        """Percorso del prezzo dentro la candela j: SL e TP nell'ordine in cui vengono toccati."""
        t, o, h, l_, c = bars[j][:5]
        spj = sp(j)

        def trig(sub, kind):            # livello in prezzo bid: i SELL chiudono all'ask (bid + spread)
            lv = sub[kind]
            return None if lv is None else (lv if buy else lv - spj)
        for sub in subs:                # salto in apertura oltre SL o TP
            if sub["open"] <= 0:
                continue
            slb, tpb = trig(sub, "sl"), trig(sub, "tp")
            px = o if buy else o + spj
            if slb is not None and ((buy and o <= slb) or (not buy and o >= slb)):
                close(sub, sub["open"], px, t, sl_reason(sub))
            elif tpb is not None and ((buy and o >= tpb) or (not buy and o <= tpb)):
                close(sub, sub["open"], px, t, "tp")
                on_tp()
        path = [o, l_, h, c] if c >= o else [o, h, l_, c]
        for seg in range(3):
            a, b = path[seg], path[seg + 1]
            start = 0.0
            while True:
                best = None
                for sub in subs:
                    if sub["open"] <= 0:
                        continue
                    for kind in ("sl", "tp"):
                        lv = trig(sub, kind)
                        if lv is None:
                            continue
                        falling = (kind == "sl") == buy      # SL dei BUY e TP dei SELL scattano con il prezzo in discesa
                        crossed = (b <= lv < a) if falling else (a < lv <= b)
                        if not crossed:
                            continue
                        f = (a - lv) / (a - b) if a != b else 0.0
                        if f + 1e-12 >= start and (best is None or f < best[0] - 1e-12 or
                                                   (abs(f - best[0]) <= 1e-12 and kind == "sl")):
                            best = (f, sub, kind)
                if best is None:
                    break
                f, sub, kind = best
                close(sub, sub["open"], sub[kind], t + int(20 * (seg + f)), "tp" if kind == "tp" else sl_reason(sub))
                if kind == "tp":
                    on_tp()
                start = f

    ev = sorted(e for e in events if e[0] >= sig.t and e[1] != "cancel")
    if waited:
        # messaggi arrivati mentre l'operazione non era ancora aperta: non la riguardano
        # (solo «nuovo SL» vale anche per l'ordine pendente, come nella copia vera)
        for et, kind, prm in ev:
            if et < entry_t and kind == "move_sl" and sig.otype != "market":
                for sub in subs:
                    sub["sl"] = prm
        ev = [e for e in ev if e[0] >= entry_t]
    k = 0
    be_auto_done = False
    end_t = sig.t + HORIZON
    last_j = None
    for j in range(first_check, len(bars)):
        t = bars[j][0]
        if t > end_t:
            break
        last_j = j
        while k < len(ev) and ev[k][0] < t:      # messaggi del canale arrivati prima di questa candela
            et, kind, prm = ev[k]
            k += 1
            pb, pj = price_at(bars, times, et + EXEC_DELAY)
            if pb is None:
                pb, pj = bars[j][1], j
            exit_px = pb if buy else pb + sp(pj)  # chiusura a mercato nel momento del messaggio
            if kind == "breakeven":
                to_be()
            elif kind == "move_sl":
                for sub in subs:
                    if sub["open"] > 0:
                        sub["sl"] = prm
            elif kind == "close":
                for sub in subs:
                    if sub["open"] > 0:
                        close(sub, sub["open"], exit_px, et + EXEC_DELAY, "channel")
            elif kind == "partial":
                pct, also_be = prm
                for sub in subs:
                    if sub["open"] > 0:
                        close(sub, sub["open"] * pct / 100, exit_px, et + EXEC_DELAY, "partial")
                if also_be:
                    to_be()
        walk(j)
        if s["be_after_points"] and not be_auto_done and any(sub["open"] > 0 for sub in subs):
            b = bars[j]
            fav = (b[2] - entry_p) if buy else (entry_p - (b[3] + sp(j)))
            if fav >= s["be_after_points"] * pt:
                to_be()
                be_auto_done = True
        if all(sub["open"] <= 0 for sub in subs):
            break

    still_open = any(sub["open"] > 0 for sub in subs)
    total = sum(sub["pnl"] for sub in subs)
    if still_open and last_j is not None:
        last = bars[last_j]
        now_p = last[4] if buy else last[4] + sp(last_j)
        total += sum((now_p - entry_p) * side / pt * sub["open"] for sub in subs if sub["open"] > 0)
        if swap_night is not None:
            swap_total += swap_night * sum(sub["open"] for sub in subs) * _nights(entry_t, last[0], spec.get("swap3", 3))
    sig.points = round(total, 1)
    sig.swap_lot = round(swap_total, 4) if swap_night is not None else None
    sig.sl_now = next((sub["sl"] for sub in subs if sub["sl"] is not None), None)
    if still_open:
        sig.status = "aperto"
        sig.exit_t, sig.exit_p = (bars[last_j][0], bars[last_j][4]) if last_j is not None else (None, None)
    else:
        sig.exit_t = max(sub["exit"][0] for sub in subs if sub["exit"])
        tot_w = sum(sub["w"] for sub in subs) or 1
        sig.exit_p = sum(sub["exit"][1] * sub["w"] for sub in subs if sub["exit"]) / tot_w
        sig.status = "vinto" if sig.points > 0.5 else ("perso" if sig.points < -0.5 else "pareggio")
    reasons = {sub.get("reason") for sub in subs if sub["exit"]}
    if still_open:
        sig.exit_reason = "Aperto"
    elif reasons == {"tp"}:
        sig.exit_reason = "TP"
    elif reasons <= {"tp", "be"} and "tp" in reasons:
        sig.exit_reason = "TP + pareggio"
    elif reasons == {"be"}:
        sig.exit_reason = "Pareggio"
    elif reasons <= {"channel", "partial"}:
        sig.exit_reason = "Chiuso dal canale"
    elif reasons == {"sl"}:
        sig.exit_reason = "SL"
    elif "tp" in reasons and "sl" in reasons:
        sig.exit_reason = "TP + SL"
    else:
        sig.exit_reason = "Chiuso"
    sig.vpp = value_per_point(sig.symbol, pt, entry_p)
    sig.vpp_acc = spec.get("vpp_acc") if spec else None


def calibrate_offset(messages, s, resolve, rates_for) -> int:
    """Correzione dell'ora del broker (in secondi) confrontando i prezzi d'entrata dei segnali con i prezzi reali.

    Se un broker usa un fuso orario diverso dal solito, i segnali finirebbero sulle candele sbagliate:
    si prova a spostare di ±1/±2 ore e si sceglie lo spostamento che fa combaciare i prezzi.
    """
    samples = []
    for m in messages:
        r = parse_message(m["text"], s, None)
        if r["kind"] != "signal" or r["entry"] is None or r["order_type"] != "market" or not r["symbol"]:
            continue
        sym = resolve(r["symbol"])
        bars = rates_for(sym) if sym else []
        if bars:
            entry = r["entry"] if r.get("entry2") is None else (r["entry"] + r["entry2"]) / 2
            samples.append((m["t"], entry, bars, [b[0] for b in bars]))
    if len(samples) < 3:
        return 0
    scores = {}
    for delta in (0, -3600, 3600, -7200, 7200):
        errs = []
        for t, entry, bars, times in samples:
            p, _i = price_at(bars, times, t + delta)
            if p is not None:
                errs.append(abs(p - entry) / max(abs(entry), 1e-9))
        if len(errs) >= max(3, len(samples) // 2):
            errs.sort()
            scores[delta] = errs[len(errs) // 2]
    if 0 not in scores or not scores:
        return 0
    best = min(scores, key=scores.get)
    return best if best != 0 and scores[best] < scores[0] * 0.5 else 0


def analyze(messages: list[dict], s: dict, resolve, rates_for, spreads: dict | None = None,
            points: dict | None = None, specs: dict | None = None) -> tuple[list, list, dict, list]:
    """messages: [{id, t (ora broker), utc, text, reply_to, edited}] in ordine di tempo.
    resolve(raw) → simbolo del broker o None.   rates_for(symbol) → candele M1 [(t,o,h,l,c)].
    spreads / points: per simbolo, in prezzo (dati letti da MetaTrader).
    """
    spreads, points, specs = spreads or {}, points or {}, specs or {}
    signals: list[RSignal] = []
    rmsgs: list[RMessage] = []
    events: dict[int, list] = {}
    by_msg: dict[int, RSignal] = {}
    seen: dict = {}
    window = max(1, int(s["duplicate_window_min"])) * 60
    dups = 0

    def duplicate(fp, t) -> bool:
        """Stessa regola della copia vera: vale la prima volta, poi i doppi entro la finestra si ignorano."""
        nonlocal dups
        if not s["ignore_duplicates"] or fp is None:
            return False
        for key in [x for x, tt in seen.items() if t - tt >= window]:
            del seen[key]
        if find_duplicate(fp, seen) is not None:
            dups += 1
            return True
        seen[fp] = t
        return False

    for m in messages:
        r = parse_message(m["text"], s, None)
        rm = RMessage(m["id"], m["t"], m["text"][:500], describe(r), edited=bool(m.get("edited")))
        rmsgs.append(rm)
        kind = r["kind"]
        if kind in ("none", "ignore"):
            continue
        if kind == "signal":
            if not r["symbol"]:
                rm.note = "simbolo non riconosciuto"
                continue
            if duplicate(fingerprint(r), m["t"]):
                rm.note = "doppio: ignorato"
                continue
            sym = resolve(r["symbol"])
            side = r["side"]
            if s["reverse"]:
                side = "sell" if side == "buy" else "buy"
            otype = r["order_type"]
            if s["order_type_mode"] == "market" or s["reverse"]:
                otype = "market"
            elif s["order_type_mode"] == "limit" and r["entry"] is not None:
                otype = "limit"
            if otype in ("limit", "stop") and r["entry"] is None:
                otype = "market"
            sig = RSignal(m["id"], m["t"], sym or r["symbol"], side, otype, r["entry"], r.get("entry2"),
                          r["sl"], list(r["tps"]), m["text"][:500], edited=bool(m.get("edited")))
            if not sym:
                sig.status = "ignorato: simbolo non trovato sul broker"
            else:
                why = _blocked(s, side, sym, m["utc"])
                if why:
                    sig.status = f"ignorato: {why}"
            signals.append(sig)
            by_msg[m["id"]] = sig
            rm.note = "segnale"
            continue
        # gestione: stessa regola dei doppi della copia vera (stesso canale, stessa risposta)
        if duplicate(fingerprint(r, 0, m.get("reply_to")), m["t"]):
            rm.note = "doppio: ignorato"
            continue
        follow = {"breakeven": "follow_breakeven", "move_sl": "follow_move_sl", "close": "follow_close",
                  "partial": "follow_partial", "cancel": "follow_cancel"}
        if kind in follow and not s[follow[kind]]:
            rm.note = "non seguito (impostazioni)"
            continue
        if m.get("reply_to") in by_msg:
            targets = [by_msg[m["reply_to"]]]
        else:
            prev = [x for x in signals if x.t <= m["t"] and not x.status.startswith("ignorato")]
            if r.get("symbol"):
                prev = [x for x in prev if x.symbol.upper().startswith(r["symbol"].upper()[:6])] or prev
            targets = prev[-1:] if s["updates_target"] == "last_signal" else prev
        if not targets:
            rm.note = "nessun segnale a cui applicarlo"
            continue
        evs = []
        if kind == "breakeven":
            evs.append(("breakeven", None))
        elif kind == "move_sl" and r.get("new_sl") is not None:
            evs.append(("move_sl", r["new_sl"]))
        elif kind == "close":
            evs.append(("close", None))
        elif kind == "partial":
            evs.append(("partial", (r.get("percent") or s["partial_percent"],
                                    bool(r.get("also_breakeven")) and s["follow_breakeven"])))
        elif kind == "cancel":
            evs.append(("cancel", None))
        elif kind == "tp_hit":
            act = s["tp_hit_action"]
            if act in ("partial", "partial_be"):
                evs.append(("partial", (s["tp_hit_partial_percent"], act == "partial_be")))
            elif act == "breakeven":
                evs.append(("breakeven", None))
        for tgt in targets:
            for kk, prm in evs:
                events.setdefault(tgt.msg_id, []).append((m["t"], kk, prm))
        rm.note = "gestione" if evs else "nessuna azione"

    def sim(sig, max_positions=None):
        simulate_signal(sig, rates_for(sig.symbol), events.get(sig.msg_id, []), s, spread=spreads.get(sig.symbol, 0.0),
                        point=points.get(sig.symbol), spec=specs.get(sig.symbol), max_positions=max_positions)

    for sig in signals:
        if not sig.status:
            sim(sig)

    # limiti di posizioni aperte delle impostazioni (totali e per simbolo), in ordine di tempo come la copia vera
    limited = 0
    accepted = []          # (inizio, fine, simbolo, posizioni)
    for sig in sorted(signals, key=lambda x: x.t):
        if sig.status not in ("vinto", "perso", "pareggio", "aperto"):
            continue
        n = getattr(sig, "n_pos", 1)
        start = sig.t
        active = [a for a in accepted if a[1] is None or a[1] > start]
        free = min(s["max_open_trades"] - sum(a[3] for a in active),
                   s["max_trades_per_symbol"] - sum(a[3] for a in active if a[2] == sig.symbol))
        if free <= 0:
            for f in ("entry_t", "entry_p", "exit_t", "exit_p", "vpp", "vpp_acc", "swap_lot"):
                setattr(sig, f, None)
            sig.points, sig.exits = 0.0, []
            sig.status = "ignorato: troppe posizioni aperte"
            limited += 1
            continue
        if free < n:
            sig.status, sig.exits = "", []
            sim(sig, max_positions=free)
            n = free
        accepted.append((start, None if sig.status == "aperto" else sig.exit_t, sig.symbol, n))
    for sig in signals:
        if sig.status in ("vinto", "perso", "pareggio", "aperto") and sig.entry_t:
            sig.spark = make_spark(rates_for(sig.symbol), sig)
    stats, equity = summarize(signals, rmsgs, dups)
    stats["limited"] = limited
    return signals, rmsgs, stats, equity


def make_spark(bars, sig, max_points=90):
    """Prezzi attorno all'operazione (prima, durante e dopo), ridotti per una miniatura del grafico."""
    if not bars:
        return []
    times = [b[0] for b in bars]
    end = sig.exit_t or bars[-1][0]
    pad = max(15 * 60, int((end - sig.entry_t) * 0.35))
    i0 = max(0, bisect.bisect_left(times, sig.entry_t - pad))
    i1 = min(len(bars), bisect.bisect_right(times, end + pad))
    chunk = bars[i0:i1]
    if not chunk:
        return []
    step = max(1, -(-len(chunk) // max_points))
    out = []
    for k in range(0, len(chunk), step):
        part = chunk[k:k + step]
        out.append([part[-1][0], round(part[-1][4], 6), round(max(b[2] for b in part), 6), round(min(b[3] for b in part), 6)])
    return out


# ============================================================================ impostazioni usate dal Radar
# Il Radar non ha impostazioni proprie (a parte lotto e commissione per la stima in denaro):
# usa le impostazioni generali con le personalizzazioni del canale, come la copia vera.
SIM_KEYS = ["multi_tp_mode", "tp_index", "lot_split", "no_sl_mode", "default_sl_points", "no_tp_mode",
            "default_tp_points", "sl_adjust_points", "tp_adjust_points", "reverse", "direction_filter",
            "order_type_mode", "pending_expiry_min", "ignore_duplicates", "duplicate_window_min",
            "follow_breakeven", "follow_move_sl", "follow_close", "follow_partial", "follow_cancel",
            "partial_percent", "be_on_tp1", "be_after_points", "be_offset_points", "tp_hit_action",
            "tp_hit_partial_percent", "zone_wait", "zone_wait_seconds", "zone_margin_points", "min_sl_distance_pips",
            "updates_target",
            "max_open_trades", "max_trades_per_symbol", "hours_enabled", "hours_start", "hours_end", "days",
            "blocked_symbols", "allowed_symbols", "symbol_aliases"]


def effective_settings(general: dict, cid=None) -> dict:
    """Impostazioni generali con le personalizzazioni del canale (come la copia vera)."""
    from .settings import for_channel
    return for_channel(general, cid) if cid is not None else dict(general)


def settings_sig(s: dict) -> str:
    return json.dumps({k: s.get(k) for k in SIM_KEYS}, sort_keys=True, default=str)


def money_of(x, lot, usd_to_acc=None, commission=0.0):
    """Risultato in denaro di un segnale (valuta del conto) con il lotto scelto: prezzo, swap e commissioni."""
    if getattr(x, "vpp_acc", None) is not None:
        v = x.vpp_acc
    elif x.vpp is not None:
        v = x.vpp * (usd_to_acc or 1.0)
    else:
        return None
    lots = getattr(x, "lots", 1.0) or 1.0
    swap = getattr(x, "swap_lot", None) or 0.0
    return x.points * v * lot + swap * lot - commission * lot * lots


def summarize(signals, rmsgs, dups, lot: float | None = None, usd_to_acc: float | None = None,
              commission: float = 0.0):
    """Statistiche. Con `lot` (e valori per punto noti) calcola anche denaro, curva e calo massimo in denaro."""
    done = [x for x in signals if x.status in ("vinto", "perso", "pareggio", "aperto")]
    closed = [x for x in done if x.status != "aperto"]
    wins = [x for x in closed if x.status == "vinto"]
    losses = [x for x in closed if x.status == "perso"]
    money_ok = lot is not None and bool(done) and all(money_of(x, lot, usd_to_acc) is not None for x in done)

    def value(x):
        if money_ok:
            return money_of(x, lot, usd_to_acc, commission)
        return x.points
    equity, cum, peak, dd = [], 0.0, 0.0, 0.0
    streak = worst = 0
    for x in sorted(done, key=lambda z: z.exit_t or z.t):
        cum += value(x)
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        equity.append((x.exit_t or x.t, round(cum, 2)))
        streak = streak + 1 if x.status == "perso" else 0
        worst = max(worst, streak)
    symbols = {x.symbol for x in done}
    stats = {
        "messages": len(rmsgs),
        "signals": len(signals),
        "executed": len(done),
        "ignored": len([x for x in signals if x.status.startswith("ignorato") or x.status == "ordine mai eseguito"]),
        "no_data": len([x for x in signals if x.status == "senza prezzi"]),
        "open": len(done) - len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "winrate": round(100 * len(wins) / len(closed), 1) if closed else None,
        "points": round(sum(x.points for x in done), 1),
        "single_symbol": len(symbols) <= 1,
        "avg_win": round(sum(x.points for x in wins) / len(wins), 1) if wins else None,
        "avg_loss": round(sum(x.points for x in losses) / len(losses), 1) if losses else None,
        "max_drawdown": round(dd, 2),
        "worst_streak": worst,
        "duplicates": dups,
        "edited": len([x for x in rmsgs if x.edited]),
        "in_money": bool(money_ok),
        "money": round(cum, 2) if money_ok else None,
    }
    return stats, equity


# ============================================================================ raccolta dati (asincrona)
def parse_bars(text: str) -> list[tuple]:
    """Candele dal ponte o dal disco: (tempo, apertura, massimo, minimo, chiusura[, spread in punti])."""
    out = []
    for line in text.splitlines():
        p = line.strip().split("|")
        if len(p) >= 5:
            try:
                b = (int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]))
                if len(p) >= 6 and p[5] != "":
                    b = b + (int(float(p[5])),)
                out.append(b)
            except ValueError:
                continue
    out.sort(key=lambda b: b[0])
    return out


def _cache_path(symbol: str, day_start: int) -> Path:
    return CACHE_DIR / re.sub(r"[^A-Za-z0-9._-]", "_", symbol) / f"{day_start}.txt"


def prefs() -> dict:
    """Preferenze del Radar (lotto della stima)."""
    try:
        return json.loads((RADAR_DIR / "preferenze.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_prefs(**kw):
    p = prefs()
    p.update(kw)
    RADAR_DIR.mkdir(parents=True, exist_ok=True)
    (RADAR_DIR / "preferenze.json").write_text(json.dumps(p), encoding="utf-8")


async def scan(engine, cid: int, name: str, days: int, progress=lambda text: None) -> Report:
    """Analizza un canale degli ultimi `days` giorni. Non apre nessuna operazione."""
    from .chart import fetch
    s = effective_settings(engine.settings.get(), cid)
    generated = dt.datetime.now().strftime("%d/%m/%Y %H:%M")

    def fail(msg):
        return Report(cid, name, days, generated, [], [], {}, [], error=msg)

    if engine.tg is None or not engine.tg_authorized:
        return fail("Telegram non è collegato: Impostazioni → Telegram → Accedi.")
    if engine.bridge is None or not engine.mt5_connected():
        return fail("MetaTrader non è collegato: servono i suoi prezzi storici. Aprilo con TelegramBridge sul grafico.")

    # 1) messaggi del canale (restano su Telegram anche se il computer era spento)
    progress("Leggo i messaggi del canale…")
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    raw = []

    async def read():
        async for m in engine.tg.iter_messages(cid, limit=10000):
            if m.date < since:
                break
            if (m.message or "").strip():
                raw.append(m)
    try:
        await read()
    except ValueError:
        await engine.tg.get_dialogs()       # il canale non era ancora in memoria
        raw.clear()
        await read()
    raw.reverse()

    # 2) simboli usati, spread e dimensione del punto letti da MetaTrader
    symbols = []
    for m in raw:
        r = parse_message(m.message, s, None)
        if r["kind"] == "signal" and r["symbol"]:
            sym = engine.resolve_symbol(r["symbol"])
            if sym and sym not in symbols:
                symbols.append(sym)
    spreads, points, spread_pts, specs = {}, {}, {}, {}
    precise = True
    for sym in symbols:
        text, err, _now = await fetch(engine.bridge, {"action": "SPEC", "symbol": sym})
        if err:
            precise = False                  # ponte non aggiornato: si usa il metodo base
        else:
            kv = dict(line.split("=", 1) for line in (text or "").splitlines() if "=" in line)
            try:
                pt = float(kv["point"])
                tv, ts = float(kv["tick_value"]), float(kv["tick_size"])
                specs[sym] = {"vpp_acc": tv * pt / ts if ts > 0 else None, "swap_long": float(kv["swap_long"]),
                              "swap_short": float(kv["swap_short"]), "swap_mode": int(kv["swap_mode"]),
                              "swap3": int(kv["swap3"]), "currency": kv.get("account_currency", "").strip()}
                points[sym] = pt
            except (KeyError, ValueError):
                precise = False
        q = await engine._quote(sym)
        if q:
            bid, ask, pt = q
            if pt > 0:
                points.setdefault(sym, pt)
                sp = max(0.0, ask - bid)
                if sp > 200 * pt:          # spread anomalo (mercato chiuso): non lo uso
                    sp = 0.0
                spreads[sym] = sp
                spread_pts[sym] = round(sp / pt)

    # 3) ora del broker
    off_file = RADAR_DIR / "ora_broker.json"
    saved = None
    try:
        saved = json.loads(off_file.read_text())["offset"]
    except (OSError, ValueError, KeyError):
        pass
    base = saved if saved is not None else 7200
    if symbols:
        _t, _e, server_now = await fetch(engine.bridge, {"action": "RATES", "symbol": symbols[0], "tf": "M1",
                                                         "from": int(time.time()) - 2 * 86400, "to": 0})
        base = measure_offset(server_now, time.time(), saved)
        RADAR_DIR.mkdir(parents=True, exist_ok=True)
        off_file.write_text(json.dumps({"offset": base}))
    msgs = []
    for m in raw:
        utc = m.date.timestamp()
        msgs.append({"id": m.id, "utc": utc, "t": int(utc + server_offset(utc, base)), "text": m.message,
                     "reply_to": getattr(m, "reply_to_msg_id", None), "edited": bool(getattr(m, "edit_date", None))})

    # 4) prezzi al minuto, salvati sul disco (la seconda volta è molto più veloce)
    now_server = int(time.time() + server_offset(time.time(), base))
    today_server = now_server // 86400 * 86400
    need: dict[str, set] = {}
    for mm in msgs:
        r = parse_message(mm["text"], s, None)
        if r["kind"] == "signal" and r["symbol"]:
            sym = engine.resolve_symbol(r["symbol"])
            if sym:
                first = (mm["t"] - 3 * 3600) // 86400 * 86400       # margine per la verifica dell'ora
                last = min(mm["t"] + HORIZON, now_server) // 86400 * 86400
                for d in range(first, last + 1, 86400):
                    need.setdefault(sym, set()).add(d)
    total = sum(len(v) for v in need.values())
    bars_by_sym: dict[str, list] = {}
    done_n = 0
    for sym, day_set in need.items():
        bars = []
        for d in sorted(day_set):
            done_n += 1
            progress(f"Scarico i prezzi di {sym}: {done_n}/{total} giorni…")
            if d > today_server:
                continue
            path = _cache_path(sym, d)
            if path.exists() and d < today_server:
                cached = parse_bars(path.read_text())
                # con il ponte aggiornato le candele salvate senza spread storico vengono riscaricate
                if not (precise and cached and len(cached[0]) < 6):
                    bars += cached
                    continue
            day_bars = []
            if precise:
                text, err, _now = await fetch(engine.bridge, {"action": "RATESX", "symbol": sym, "tf": "M1",
                                                              "from": d, "to": d + 86399})
                day_bars = parse_bars(text or "") if not err else []
                if err and "Installa il ponte" in err:
                    precise = False
            if not precise:
                text, err, _now = await fetch(engine.bridge, {"action": "RATES", "symbol": sym, "tf": "M1",
                                                              "from": d, "to": d + 86399})
                day_bars = parse_bars(text or "")
            weekend = dt.datetime.fromtimestamp(d, dt.timezone.utc).weekday() >= 5
            if d < today_server and (day_bars or weekend):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join("|".join(str(v) for v in b) for b in day_bars))
            bars += day_bars
        bars.sort(key=lambda b: b[0])
        dedup = []
        for b in bars:
            if not dedup or b[0] != dedup[-1][0]:
                dedup.append(b)
        bars_by_sym[sym] = dedup

    # 5) verifica dell'ora del broker sui prezzi reali
    def rates_for(sym):
        return bars_by_sym.get(sym, [])
    shift = calibrate_offset(msgs, s, engine.resolve_symbol, rates_for)
    note = ""
    if shift:
        for mm in msgs:
            mm["t"] += shift
        note = f"ora del broker corretta di {shift // 3600:+d} h confrontando i prezzi dei segnali"

    # 6) simulazione
    progress("Calcolo i risultati…")
    signals, rmsgs, stats, equity = analyze(msgs, s, engine.resolve_symbol, rates_for, spreads, points, specs)
    acc = (engine.state or {}).get("account") or {}
    currency = acc.get("currency") or "USD"
    usd_to_acc = 1.0 if currency == "USD" else None
    if currency in ("EUR", "GBP", "AUD", "NZD"):
        fx = engine.resolve_symbol(currency + "USD")
        q = await engine._quote(fx) if fx else None
        if q and q[0] > 0:
            usd_to_acc = 1.0 / ((q[0] + q[1]) / 2)
    elif currency in ("CHF", "JPY", "CAD"):
        fx = engine.resolve_symbol("USD" + currency)
        q = await engine._quote(fx) if fx else None
        if q and q[0] > 0:
            usd_to_acc = (q[0] + q[1]) / 2
    if specs and all(sp.get("vpp_acc") for sp in specs.values()):
        currency = next(iter(specs.values())).get("currency") or currency
    rep = Report(cid, name, days, generated, signals, rmsgs, stats, equity, spreads=spread_pts,
                 usd_to_acc=usd_to_acc, currency=currency if (usd_to_acc or specs) else "USD", note=note,
                 precise=precise and bool(specs), settings_sig=settings_sig(s))
    save_report(rep)
    return rep
