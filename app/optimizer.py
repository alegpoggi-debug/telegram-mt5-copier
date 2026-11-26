"""«Migliora impostazioni»: prova migliaia di combinazioni sui dati già salvati dal Radar.

Non usa MetaTrader né Telegram: rifà le simulazioni con i messaggi e i prezzi dell'ultima analisi.
Per non premiare le combinazioni fortunate:
  - divide il periodo in due: sceglie guardando i mesi più vecchi («allenamento») e controlla
    come sarebbero andati i mesi più recenti («prova»);
  - scarta le combinazioni che crollano se si cambia di poco un'impostazione (valori vicini);
  - guarda quanti periodi di 4 settimane sono in guadagno.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import itertools
import json
import multiprocessing as mp
import os
import statistics

from . import radar
from .parser import parse_message

# impostazioni che il miglioramento può cambiare (tutte esistono anche nel ⚙ del canale)
KEYS = ["multi_tp_mode", "tp_index", "be_on_tp1", "zone_wait", "zone_margin_points", "zone_wait_seconds",
        "be_after_points", "tp_hit_action", "min_sl_distance_pips"]

TP_CHOICES = [
    ("split", {"multi_tp_mode": "split", "be_on_tp1": False}),
    ("split_be", {"multi_tp_mode": "split", "be_on_tp1": True}),
    ("first", {"multi_tp_mode": "first", "be_on_tp1": False}),
    ("tp2", {"multi_tp_mode": "index", "tp_index": 2, "be_on_tp1": False}),
    ("last", {"multi_tp_mode": "last", "be_on_tp1": False}),
]
ZONES1 = [None, (300, 3600), (500, 1800), (600, 3600), (800, 3600)]
BE1 = (0, 100, 300)
HITS = ("nothing", "breakeven", "partial_be")
SLMIN = (0, 40)
MARGINS = (300, 400, 500, 600, 700, 800, 900, 1000)
WAITS = (900, 1800, 3600, 5400, 7200)
BES = (0, 50, 100, 150, 200, 300)

LABELS = {
    "multi_tp_mode": "Segnali con più TP", "be_on_tp1": "Pareggio dopo il primo TP",
    "zone_wait": "Aspetta il prezzo del segnale", "zone_margin_points": "Margine",
    "zone_wait_seconds": "Tempo massimo di attesa", "be_after_points": "Pareggio automatico dopo",
    "tp_hit_action": "Messaggio «TP raggiunto»", "min_sl_distance_pips": "Distanza minima dallo Stop Loss",
    "tp_index": "TP scelto",
}


def describe_value(key, value, s=None) -> str:
    if key == "multi_tp_mode":
        return {"split": "Una posizione per ogni TP", "first": "Solo il primo TP", "last": "Solo l'ultimo TP",
                "index": f"Solo il TP n° {(s or {}).get('tp_index', 2)}"}.get(value, str(value))
    if key in ("be_on_tp1", "zone_wait"):
        return "acceso" if value else "spento"
    if key == "zone_margin_points":
        return f"{value} punti"
    if key == "zone_wait_seconds":
        return f"{value} secondi ({value // 60} min)"
    if key == "be_after_points":
        return "spento" if not value else f"{value} punti"
    if key == "tp_hit_action":
        return {"nothing": "Non fare nulla", "breakeven": "SL a pareggio", "partial": "Chiudi una parte",
                "partial_be": "Chiudi una parte e SL a pareggio"}.get(value, str(value))
    if key == "min_sl_distance_pips":
        return "nessun controllo" if not value else f"{value} pips"
    return str(value)


def changes(current: dict, proposal: dict) -> list[tuple[str, str, str]]:
    """Solo le impostazioni che cambiano: (nome, adesso, proposta)."""
    out = []
    for k in KEYS:
        if k == "tp_index" and proposal.get("multi_tp_mode") != "index":
            continue
        if k in ("zone_margin_points", "zone_wait_seconds") and not proposal.get("zone_wait"):
            continue
        if k == "be_on_tp1" and proposal.get("multi_tp_mode") != "split":
            continue
        if current.get(k) != proposal.get(k):
            old = describe_value(k, current.get(k), current)
            if k in ("zone_margin_points", "zone_wait_seconds") and not current.get("zone_wait"):
                old = "non usato"
            if k == "be_on_tp1" and current.get("multi_tp_mode") != "split":
                old = "non usato"
            out.append((LABELS[k], old, describe_value(k, proposal.get(k), proposal)))
    return out


# ============================================================================ dati
def prepare(rep, general: dict, cid, lot: float) -> dict:
    """Tutto quello che serve per rifare le simulazioni, dai dati già salvati dal Radar."""
    base = radar.effective_settings(general, cid)
    base_off = 7200
    try:
        base_off = json.loads((radar.RADAR_DIR / "ora_broker.json").read_text())["offset"]
    except (OSError, ValueError, KeyError):
        pass
    msgs = []
    for m in rep.messages:
        utc = m.t - radar.server_offset(m.t - base_off, base_off)
        msgs.append({"id": m.msg_id, "t": m.t, "utc": utc, "text": m.text, "reply_to": getattr(m, "reply_to", None),
                     "edited": m.edited})
    msgs.sort(key=lambda m: (m["t"], m["id"]))
    resolve, specs, vpp_acc = {}, {}, {}
    for sg in rep.signals:
        r = parse_message(sg.text, base, None)
        if r.get("symbol"):
            resolve[r["symbol"].upper()] = sg.symbol
        if getattr(sg, "vpp_acc", None):
            vpp_acc[sg.symbol] = sg.vpp_acc
    start = (msgs[0]["t"] - 86400) if msgs else 0
    bars, points, spreads = {}, {}, {}
    for sym in set(resolve.values()):
        folder = radar._cache_path(sym, 0).parent
        seen = {}
        if folder.exists():
            for f in folder.glob("*.txt"):
                try:
                    for b in radar.parse_bars(f.read_text()):
                        if b[0] >= start:
                            seen[b[0]] = b
                except OSError:
                    continue
        bars[sym] = [seen[k] for k in sorted(seen)]
        if bars[sym]:
            points[sym] = radar._point(bars[sym])
            spreads[sym] = (rep.spreads or {}).get(sym, 0) * points[sym]
    first_bar = min((b[0][0] for b in bars.values() if b), default=start)
    last_bar = max((b[-1][0] for b in bars.values() if b), default=start)
    week0 = max(first_bar, start) // 86400 * 86400
    end = last_bar - 3 * 3600
    msgs = [m for m in msgs if m["t"] <= end]
    nweeks = max(1, int((end - week0) // (7 * 86400)) + 1)
    # i dati del simbolo (swap…) non sono nel risultato salvato: per il denaro si usa il valore del punto del broker
    return {"msgs": msgs, "resolve": resolve, "bars": bars, "points": points, "spreads": spreads, "specs": specs,
            "vpp_acc": vpp_acc,
            "usd_to_acc": rep.usd_to_acc, "base": base, "lot": lot, "week0": week0, "nweeks": nweeks, "end": end}


def check(payload: dict) -> str | None:
    """Motivo per cui non si può migliorare con questi dati (None = si può)."""
    if not payload["msgs"]:
        return "Non ci sono messaggi salvati: premi prima «Aggiorna» nel Radar."
    if not any(payload["bars"].values()):
        return "Non ci sono prezzi salvati per questo canale: premi prima «Aggiorna» nel Radar con MetaTrader aperto."
    if payload["nweeks"] < 6:
        return ("Servono almeno 6 settimane di dati per un risultato affidabile. Nel Radar scegli «Ultimi 90 giorni», "
                "«Ultimi 6 mesi» o «Ultimo anno» e premi «Aggiorna».")
    return None


# ============================================================================ simulazioni (anche in altri processi)
_P: dict = {}


def _init(payload):
    global _P
    _P = payload


def _value(x, lot, usd_to_acc, vpp_map=None):
    """Risultato in denaro di un segnale con lo stesso lotto PER SEGNALE (diviso tra le posizioni)."""
    v = (vpp_map or {}).get(x.symbol) or getattr(x, "vpp_acc", None)
    if v is None:
        v = (x.vpp or 1.0) * (usd_to_acc or 1.0)
    n = x.lots or 1.0
    return x.points / n * v * lot + (x.swap_lot or 0.0) * lot / n


def simulate(cfg: dict, detail: bool = False) -> dict:
    p = _P
    s = dict(p["base"])
    s.update(cfg)
    sig, _rm, _st, _eq = radar.analyze(p["msgs"], s, lambda raw: p["resolve"].get((raw or "").upper(), raw),
                                       lambda sym: p["bars"].get(sym, []), spreads=p["spreads"], points=p["points"],
                                       specs=p["specs"])
    done = [x for x in sig if x.status in ("vinto", "perso", "pareggio", "aperto")]
    weeks = [0.0] * p["nweeks"]
    ev = []
    for x in done:
        v = _value(x, p["lot"], p["usd_to_acc"], p.get("vpp_acc"))
        w = int((x.t - p["week0"]) // (7 * 86400))
        if 0 <= w < p["nweeks"]:
            weeks[w] += v
        ev.append((x.exit_t or x.t, v))
    ev.sort()
    cum = peak = dd = 0.0
    for _t, v in ev:
        cum += v
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    out = {"weeks": weeks, "tot": sum(weeks), "dd": dd, "n": len(done),
           "wins": sum(1 for x in done if x.status == "vinto")}
    if detail:
        out["events"] = ev
    return out


def _job(cfg):
    return cfg, simulate(cfg)


# ============================================================================ ricerca
def stage1() -> list[dict]:
    out = []
    for (_n, tp), z, be, hit, sl in itertools.product(TP_CHOICES, ZONES1, BE1, HITS, SLMIN):
        c = dict(tp, be_after_points=be, tp_hit_action=hit, min_sl_distance_pips=sl)
        c.update({"zone_wait": True, "zone_margin_points": z[0], "zone_wait_seconds": z[1]} if z else {"zone_wait": False})
        out.append(c)
    return out


def family(c: dict) -> tuple:
    return (c.get("multi_tp_mode"), c.get("tp_index") if c.get("multi_tp_mode") == "index" else None,
            c.get("be_on_tp1"), c.get("zone_wait"), c.get("tp_hit_action"), c.get("min_sl_distance_pips"))


def stage2(results: list, top: int = 6) -> list[dict]:
    """Le famiglie migliori, esplorate in dettaglio (margine, attesa, pareggio)."""
    ranked = sorted(results, key=lambda r: -_quick(r[1]))
    fams, out = [], []
    for c, _m in ranked:
        f = family(c)
        if f not in fams:
            fams.append(f)
        if len(fams) >= top:
            break
    for f in fams:
        c0 = next(c for c, _m in ranked if family(c) == f)
        if c0.get("zone_wait"):
            for mg, w, be in itertools.product(MARGINS, WAITS, BES):
                out.append(dict(c0, zone_margin_points=mg, zone_wait_seconds=w, be_after_points=be))
        else:
            for be in BES:
                out.append(dict(c0, be_after_points=be))
    return out


def _split(n):
    k = max(1, round(n * 2 / 3))
    return range(0, k), range(k, n)


def _quick(m):
    tr, te = _split(len(m["weeks"]))
    a, b = sum(m["weeks"][i] for i in tr), sum(m["weeks"][i] for i in te)
    return m["tot"] if a > 0 and b > 0 else -1e9 + m["tot"]


def _key(c):
    return json.dumps({k: c.get(k) for k in KEYS}, sort_keys=True)


def evaluate(results: list) -> list[dict]:
    """Metriche di ogni combinazione: allenamento, prova, periodi in guadagno, valori vicini."""
    idx = {_key(c): m for c, m in results}
    rows = []
    for c, m in results:
        w = m["weeks"]
        tr, te = _split(len(w))
        blocks = [sum(w[i:i + 4]) for i in range(0, len(w), 4)]
        if len(blocks) > 1 and len(w) % 4:
            blocks = blocks[:-1]                     # l'ultimo periodo incompleto non conta
        nb = []
        for k, vals in (("zone_margin_points", MARGINS), ("zone_wait_seconds", WAITS), ("be_after_points", BES)):
            if k != "be_after_points" and not c.get("zone_wait"):
                continue
            if c.get(k) not in vals:
                continue
            i = vals.index(c[k])
            for j in (i - 1, i + 1):
                if 0 <= j < len(vals):
                    other = idx.get(_key(dict(c, **{k: vals[j]})))
                    if other:
                        nb.append(other["tot"])
        allv = [m["tot"]] + nb
        rows.append({"cfg": c, "m": m, "train": sum(w[i] for i in tr), "test": sum(w[i] for i in te),
                     "pos": sum(1 for b in blocks if b > 0), "nblocks": len(blocks), "worst": min(blocks) if blocks else 0,
                     "nb_min": min(allv), "nb_med": statistics.median(allv), "nb_n": len(nb)})
    return rows


def pick(rows: list, current: dict | None) -> dict:
    """Le tre proposte: consigliata (equilibrio), più guadagno, più tranquilla."""
    min_ops = max(10, int(0.3 * (current["m"]["n"] if current else 0)))

    def ok(r, strict=True):
        good = r["train"] > 0 and r["test"] > 0 and r["m"]["n"] >= min_ops and r["nb_min"] > 0
        if strict:
            good = good and r["nb_n"] >= 2 and r["pos"] >= 0.7 * r["nblocks"]
        return good
    cur_tot = current["m"]["tot"] if current else -1e18
    cur_dd = current["m"]["dd"] if current else 1e18

    def better(r):
        """Davvero migliore delle attuali: guadagna di più, oppure quasi uguale (80%) con cali molto più piccoli."""
        tot, dd = r["m"]["tot"], r["m"]["dd"]
        return tot > cur_tot + 1 or (tot > 0 and tot >= 0.8 * cur_tot and dd <= 0.7 * cur_dd)
    strict = [r for r in rows if ok(r) and better(r)]
    loose = [r for r in rows if ok(r, strict=False) and r["m"]["tot"] > cur_tot + 1]
    out = {}
    if strict:
        # consigliata = equilibrio: guadagno (anche dei valori vicini) meno due volte il calo massimo,
        # tra le combinazioni che guadagnano almeno il 60% della migliore: costante ma non «prudente e basta»
        top = max(r["nb_med"] for r in strict)
        cand = [r for r in strict if r["nb_med"] >= 0.6 * top]
        out["consigliata"] = max(cand, key=lambda r: (r["nb_med"] - 2 * r["m"]["dd"], r["nb_med"]))
    if loose:
        best = max(loose, key=lambda r: r["m"]["tot"])
        if "consigliata" not in out or _key(best["cfg"]) != _key(out["consigliata"]["cfg"]):
            out["guadagno"] = best
    calm = [r for r in rows if ok(r) and r["m"]["dd"] < 0.9 * cur_dd and r["m"]["tot"] >= max(0.5 * cur_tot, 0.5 * (
        out["consigliata"]["m"]["tot"] if "consigliata" in out else 0), 1)]
    if calm:
        # tra quelle con il calo più piccolo (entro il 10%), la più redditizia
        low = min(r["m"]["dd"] for r in calm)
        best = max([r for r in calm if r["m"]["dd"] <= 1.1 * low], key=lambda r: (r["nb_med"], -r["m"]["dd"]))
        if all(_key(best["cfg"]) != _key(v["cfg"]) for v in out.values()):
            out["tranquilla"] = best
    return out


def months(events: list) -> dict:
    out = {}
    for t, v in events:
        k = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m")
        out[k] = out.get(k, 0.0) + v
    return out


def daily(events: list, week0: int, end: int) -> list:
    pts, cum, k = [], 0.0, 0
    for d in range(week0, end + 86400, 86400):
        if dt.datetime.fromtimestamp(d, dt.timezone.utc).weekday() >= 5:
            continue
        while k < len(events) and events[k][0] < d + 86400:
            cum += events[k][1]
            k += 1
        pts.append((d, round(cum, 2)))
    return pts


class Run:
    """Esecuzione in background: più processori insieme, con avanzamento e annullamento."""

    def __init__(self, payload: dict, workers: int | None = None, plan=None):
        self.payload = payload
        self.workers = workers if workers is not None else max(1, min(8, (os.cpu_count() or 2) - 1))
        self.plan = plan or (stage1, stage2)
        self.done = 0
        self.total = 0
        self.phase = ""
        self.cancelled = False
        self.result: dict | None = None
        self.error: str | None = None

    def cancel(self):
        self.cancelled = True

    def _map(self, ex, cfgs):
        self.total += len(cfgs)
        out = []
        if ex is None:
            for c in cfgs:
                if self.cancelled:
                    return out
                out.append(_job(c))
                self.done += 1
            return out
        futs = [ex.submit(_job, c) for c in cfgs]
        for f in cf.as_completed(futs):
            if self.cancelled:
                for g in futs:
                    g.cancel()
                return out
            out.append(f.result())
            self.done += 1
        return out

    def run(self):
        try:
            _init(self.payload)
            ex = None
            if self.workers > 1:
                try:
                    ex = cf.ProcessPoolExecutor(max_workers=self.workers, mp_context=mp.get_context("spawn"),
                                                initializer=_init, initargs=(self.payload,))
                except (OSError, ValueError, ImportError):
                    ex = None
            try:
                current_cfg = {k: self.payload["base"].get(k) for k in KEYS}
                self.phase = "Le tue impostazioni attuali…"
                res = self._map(ex, [current_cfg])
                first, second = self.plan
                self.phase = "Prima ricerca: tutte le famiglie di impostazioni…"
                res += self._map(ex, first())
                if not self.cancelled:
                    self.phase = "Seconda ricerca: le migliori, in dettaglio…"
                    seen = {_key(c) for c, _m in res}
                    res += self._map(ex, [c for c in second(res) if _key(c) not in seen])
            finally:
                if ex is not None:
                    ex.shutdown(wait=False, cancel_futures=True)
            if self.cancelled:
                return
            self.phase = "Scelgo le proposte più costanti…"
            rows = evaluate(res)
            self.rows = rows
            cur = next(r for r in rows if _key(r["cfg"]) == _key(current_cfg))
            picks = pick(rows, cur)
            p = self.payload
            out = {"current": cur, "picks": picks, "tested": len(res), "weeks": p["nweeks"],
                   "week0": p["week0"], "end": p["end"], "series": {}, "months": {}}
            for name, r in [("attuali", cur)] + list(picks.items()):
                detail = simulate(r["cfg"], detail=True)
                out["series"][name] = daily(detail["events"], p["week0"], p["end"])
                out["months"][name] = months(detail["events"])
            self.result = out
        except Exception as e:      # noqa: BLE001 - l'errore va mostrato nella finestra
            self.error = str(e)
