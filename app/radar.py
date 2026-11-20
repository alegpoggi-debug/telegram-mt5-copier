"""Radar: come sarebbe andato un canale, senza aprire nessuna operazione.

Rilegge i messaggi del canale degli ultimi giorni (restano su Telegram) e li confronta con i prezzi
al minuto di MetaTrader (restano nello storico del broker), usando le impostazioni attuali.
Funziona quindi anche se il computer è rimasto spento: si recupera tutto all'apertura.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import DATA_DIR
from .parser import describe, fingerprint, parse_message

HORIZON = 5 * 86400          # per quanto seguire un segnale al massimo
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
def _point(bars) -> float:
    """Dimensione di un punto, dedotta dai decimali dei prezzi."""
    best = 0
    for b in bars[-200:]:
        for v in b[1:]:
            s = f"{v:.6f}".rstrip("0")
            dec = len(s.split(".")[1]) if "." in s else 0
            best = max(best, dec)
    return 10 ** -max(best, 1) if best else 1.0


def _weights(n, s) -> list[float]:
    """Peso di ogni posizione rispetto al lotto (come fa il programma vero)."""
    ws = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s.get("lot_split") or "")][:n]
    if ws and sum(ws) > 0:
        if len(ws) < n:
            rest = max(0.0, 100 - sum(ws))
            ws += [rest / (n - len(ws))] * (n - len(ws))
        tot = sum(ws)
        return [w / tot for w in ws]          # ripartizione: il lotto è il totale del segnale
    return [1.0] * n if s["lot_mode"] == "fixed" else [1.0 / n] * n


def _blocked(s, side, symbol, utc_ts) -> str | None:
    """Filtri delle impostazioni: direzione, simboli, giorni e orari."""
    from .settings import split_words
    if s["direction_filter"] != "both" and side != s["direction_filter"]:
        return f"solo {s['direction_filter'].upper()}"
    base = re.sub(r"[^A-Z0-9]", "", symbol.upper())
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


def simulate_signal(sig: RSignal, bars: list, events: list, s: dict):
    """Simula un segnale sulle candele M1. events: [(ora broker, tipo, parametri)] già filtrati per questo segnale."""
    if not bars:
        sig.status = "senza prezzi"
        return
    times = [b[0] for b in bars]
    pt = _point(bars)
    side = 1 if sig.side == "buy" else -1
    i = bisect.bisect_left(times, sig.t)
    if i >= len(bars) or bars[i][0] - sig.t > 3600 * 3:
        sig.status = "senza prezzi"
        return

    # ---- entrata
    if sig.otype == "market":
        price = bars[i][1]
        if s["zone_wait"] and sig.entry is not None and sig.entry2 is not None and not s["reverse"]:
            lo, hi = sorted((sig.entry, sig.entry2))
            m = s["zone_margin_points"] * pt
            if not (lo - m <= price <= hi + m):
                n_wait = math.ceil(max(0, s["zone_wait_seconds"]) / 60)
                found = None
                for j in range(i, min(len(bars), i + n_wait)):
                    _t, _o, h, l_, _c = bars[j]
                    if h >= lo - m and l_ <= hi + m:
                        found = (j, hi + m if price > hi + m else lo - m)
                        break
                if not found:
                    sig.status = "ignorato: prezzo fuori dalla fascia"
                    return
                i, price = found
        entry_i, entry_p = i, price
    else:
        e = sig.entry
        expiry = (s["pending_expiry_min"] or 24 * 60) * 60
        entry_i = None
        cancels = [t for t, k, _p in events if k == "cancel"]
        for j in range(i, len(bars)):
            t, o, h, l_, c = bars[j]
            if t - sig.t > expiry or any(ct <= t for ct in cancels):
                break
            buy_side = sig.side == "buy"
            hit = (l_ <= e) if (sig.otype == "limit") == buy_side else (h >= e)
            if hit:
                entry_i, entry_p = j, e
                break
        if entry_i is None:
            sig.status = "ordine mai eseguito"
            return
    sig.entry_t, sig.entry_p = bars[entry_i][0], entry_p

    # ---- SL / TP come farebbe il programma
    sl = sig.sl
    if s["reverse"]:
        sl = None
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
    if sl is not None and s["sl_adjust_points"]:
        sl -= side * s["sl_adjust_points"] * pt
    tps = [tp - side * s["tp_adjust_points"] * pt if (tp is not None and s["tp_adjust_points"]) else tp for tp in tps]
    weights = _weights(len(tps), s)
    subs = [{"tp": tp, "sl": sl, "w": w, "open": w, "pnl": 0.0, "exit": None} for tp, w in zip(tps, weights)]
    sig.tp_first = next((tp for tp in tps if tp is not None), None)
    be_price = entry_p + side * s["be_offset_points"] * pt

    def close(sub, frac_w, price, t):
        sub["pnl"] += (price - entry_p) * side / pt * frac_w
        sub["open"] -= frac_w
        sig.exits.append((t, price))
        if sub["open"] <= 1e-9:
            sub["open"] = 0.0
            sub["exit"] = (t, price)

    def to_be(only_if_better=True):
        for sub in subs:
            if sub["open"] > 0 and (sub["sl"] is None or (side > 0 and sub["sl"] < be_price) or
                                    (side < 0 and sub["sl"] > be_price) or not only_if_better):
                sub["sl"] = be_price

    ev = sorted(e for e in events if e[0] >= sig.t and e[1] != "cancel")
    k = 0
    end_t = sig.t + HORIZON
    last = None
    for j in range(entry_i, len(bars)):
        t, o, h, l_, c = bars[j]
        if t > end_t:
            break
        last = bars[j]
        # messaggi del canale arrivati prima di questa candela
        while k < len(ev) and ev[k][0] < t:
            _et, kind, prm = ev[k]
            k += 1
            if kind == "breakeven":
                to_be()
            elif kind == "move_sl":
                for sub in subs:
                    if sub["open"] > 0:
                        sub["sl"] = prm
            elif kind == "close":
                for sub in subs:
                    if sub["open"] > 0:
                        close(sub, sub["open"], o, t)
            elif kind == "partial":
                pct, also_be = prm
                for sub in subs:
                    if sub["open"] > 0:
                        close(sub, sub["open"] * pct / 100, o, t)
                if also_be:
                    to_be()
        hit_tp = False
        for sub in subs:
            if sub["open"] <= 0:
                continue
            slv, tpv = sub["sl"], sub["tp"]
            if slv is not None and ((side > 0 and l_ <= slv) or (side < 0 and h >= slv)):
                price = o if ((side > 0 and o < slv) or (side < 0 and o > slv)) else slv
                close(sub, sub["open"], price, t)
            elif tpv is not None and ((side > 0 and h >= tpv) or (side < 0 and l_ <= tpv)):
                close(sub, sub["open"], tpv, t)
                hit_tp = True
        if hit_tp and s["be_on_tp1"]:
            to_be()
        if all(sub["open"] <= 0 for sub in subs):
            break

    still_open = any(sub["open"] > 0 for sub in subs)
    total = sum(sub["pnl"] for sub in subs)
    if still_open and last is not None:
        total += sum((last[4] - entry_p) * side / pt * sub["open"] for sub in subs if sub["open"] > 0)
    sig.points = round(total, 1)
    sig.sl_now = next((sub["sl"] for sub in subs if sub["sl"] is not None), None)
    if still_open:
        sig.status = "aperto"
        sig.exit_t, sig.exit_p = (last[0], last[4]) if last else (None, None)
    else:
        lt = max(sub["exit"][0] for sub in subs if sub["exit"])
        sig.exit_t = lt
        tot_w = sum(sub["w"] for sub in subs) or 1
        sig.exit_p = sum(sub["exit"][1] * sub["w"] for sub in subs if sub["exit"]) / tot_w
        sig.status = "vinto" if sig.points > 0.5 else ("perso" if sig.points < -0.5 else "pareggio")
    letters = re.sub(r"[^A-Z]", "", sig.symbol.upper().split(".")[0])
    if s["lot_mode"] == "fixed" and len(letters) >= 6 and letters[3:6] == "USD":
        # strumenti quotati in dollari (oro, EURUSD, …): con 0,01 lotti 1 punto ≈ 0,01 $
        sig.money = round(sig.points * s["fixed_lot"], 2)


def analyze(messages: list[dict], s: dict, resolve, rates_for) -> tuple[list, list, dict, list]:
    """messages: [{id, t (ora broker), utc, text, reply_to, edited}] in ordine di tempo.
    resolve(raw) → simbolo del broker o None.   rates_for(symbol) → candele M1 [(t,o,h,l,c)].
    """
    signals: list[RSignal] = []
    rmsgs: list[RMessage] = []
    events: dict[int, list] = {}
    by_msg: dict[int, RSignal] = {}
    seen: dict = {}
    window = max(1, int(s["duplicate_window_min"])) * 60
    dups = 0
    for m in messages:
        r = parse_message(m["text"], s, None)
        rm = RMessage(m["id"], m["t"], m["text"][:500], describe(r), edited=bool(m.get("edited")))
        rmsgs.append(rm)
        kind = r["kind"]
        if kind in ("none", "ignore"):
            continue
        fp = fingerprint(r, None, m.get("reply_to")) if kind == "signal" else fingerprint(r, 0, m.get("reply_to"))
        if s["ignore_duplicates"] and fp is not None:
            prev = seen.get(fp)
            if prev is not None and m["t"] - prev < window:
                dups += 1
                rm.note = "doppio: ignorato"
                continue
            seen[fp] = m["t"]
        if kind == "signal":
            sym = resolve(r["symbol"]) if r["symbol"] else None
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
            sig = RSignal(m["id"], m["t"], sym or (r["symbol"] or "?"), side, otype, r["entry"], r.get("entry2"),
                          r["sl"], list(r["tps"]), m["text"][:500])
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
        # gestione: a quale segnale si riferisce
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
            evs.append(("partial", (r.get("percent") or s["partial_percent"], bool(r.get("also_breakeven")) and s["follow_breakeven"])))
        elif kind == "cancel":
            evs.append(("cancel", None))
        elif kind == "tp_hit":
            act = s["tp_hit_action"]
            if act in ("partial", "partial_be"):
                evs.append(("partial", (s["tp_hit_partial_percent"], act == "partial_be")))
            elif act == "breakeven":
                evs.append(("breakeven", None))
        for tgt in targets:
            for k, prm in evs:
                events.setdefault(tgt.msg_id, []).append((m["t"], k, prm))
        rm.note = "gestione" if evs else "nessuna azione"

    for sig in signals:
        if sig.status:
            continue
        simulate_signal(sig, rates_for(sig.symbol), events.get(sig.msg_id, []), s)

    done = [x for x in signals if x.status in ("vinto", "perso", "pareggio", "aperto")]
    closed = [x for x in done if x.status != "aperto"]
    wins = [x for x in closed if x.status == "vinto"]
    losses = [x for x in closed if x.status == "perso"]
    equity, cum, peak, dd = [], 0.0, 0.0, 0.0
    streak = worst = 0
    for x in sorted(done, key=lambda z: z.exit_t or z.t):
        cum += x.points
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        equity.append((x.exit_t or x.t, round(cum, 1)))
        streak = streak + 1 if x.status == "perso" else 0
        worst = max(worst, streak)
    money = [x.money for x in done if x.money is not None]
    stats = {
        "messages": len(rmsgs),
        "signals": len(signals),
        "executed": len(done),
        "ignored": len([x for x in signals if x.status.startswith("ignorato") or x.status in ("ordine mai eseguito",)]),
        "no_data": len([x for x in signals if x.status == "senza prezzi"]),
        "open": len(done) - len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "winrate": round(100 * len(wins) / len(closed), 1) if closed else None,
        "points": round(sum(x.points for x in done), 1),
        "avg_win": round(sum(x.points for x in wins) / len(wins), 1) if wins else None,
        "avg_loss": round(sum(x.points for x in losses) / len(losses), 1) if losses else None,
        "max_drawdown": round(dd, 1),
        "worst_streak": worst,
        "duplicates": dups,
        "edited": len([x for x in rmsgs if x.edited]),
        "money": round(sum(money), 2) if money and len(money) == len(done) else None,
    }
    return signals, rmsgs, stats, equity


# ============================================================================ raccolta dati (asincrona)
def _cache_path(symbol: str, day_start: int) -> Path:
    return CACHE_DIR / re.sub(r"[^A-Za-z0-9._-]", "_", symbol) / f"{day_start}.txt"


async def scan(engine, cid: int, name: str, days: int, progress=lambda text: None) -> Report:
    """Analizza un canale degli ultimi `days` giorni. Non apre nessuna operazione."""
    from .chart import fetch, parse_rates
    s = engine.settings.get()
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
        async for m in engine.tg.iter_messages(cid, limit=5000):
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

    # 2) ora del broker
    probe_sym = None
    for m in raw:
        r = parse_message(m.message, s, None)
        if r["kind"] == "signal" and r["symbol"]:
            probe_sym = engine.resolve_symbol(r["symbol"])
            if probe_sym:
                break
    off_file = RADAR_DIR / "ora_broker.json"
    saved = None
    try:
        saved = json.loads(off_file.read_text())["offset"]
    except (OSError, ValueError, KeyError):
        pass
    base = saved if saved is not None else 7200
    if probe_sym:
        _t, _e, server_now = await fetch(engine.bridge, {"action": "RATES", "symbol": probe_sym, "tf": "M1",
                                                         "from": int(time.time()) - 2 * 86400, "to": 0})
        base = measure_offset(server_now, time.time(), saved)
        RADAR_DIR.mkdir(parents=True, exist_ok=True)
        off_file.write_text(json.dumps({"offset": base}))
    msgs = []
    for m in raw:
        utc = m.date.timestamp()
        msgs.append({"id": m.id, "utc": utc, "t": int(utc + server_offset(utc, base)), "text": m.message,
                     "reply_to": getattr(m, "reply_to_msg_id", None), "edited": bool(getattr(m, "edit_date", None))})

    # 3) prezzi al minuto (salvati sul disco: la seconda volta è molto più veloce)
    now_server = int(time.time() + server_offset(time.time(), base))
    today_server = now_server // 86400 * 86400
    need: dict[str, set] = {}
    for mm in msgs:
        r = parse_message(mm["text"], s, None)
        if r["kind"] == "signal" and r["symbol"]:
            sym = engine.resolve_symbol(r["symbol"])
            if sym:
                first = (mm["t"] - 600) // 86400 * 86400
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
            if path.exists():
                bars += parse_rates(path.read_text())
                continue
            text, err, _now = await fetch(engine.bridge, {"action": "RATES", "symbol": sym, "tf": "M1",
                                                          "from": d, "to": d + 86399})
            day_bars = parse_rates(text or "")
            weekend = dt.datetime.fromtimestamp(d, dt.timezone.utc).weekday() >= 5
            if d < today_server and (day_bars or weekend):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(f"{b[0]}|{b[1]}|{b[2]}|{b[3]}|{b[4]}" for b in day_bars))
            bars += day_bars
        bars.sort(key=lambda b: b[0])
        bars_by_sym[sym] = bars

    # 4) simulazione
    progress("Calcolo i risultati…")
    signals, rmsgs, stats, equity = analyze(msgs, s, engine.resolve_symbol, lambda sym: bars_by_sym.get(sym, []))
    rep = Report(cid, name, days, generated, signals, rmsgs, stats, equity)
    save_report(rep)
    return rep
