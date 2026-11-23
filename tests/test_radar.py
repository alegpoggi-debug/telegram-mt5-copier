"""Test del Radar: simulazione dei segnali su prezzi costruiti a mano (risultati noti in anticipo).
python -m tests.test_radar
"""
import copy
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["TGMT5_DATA"] = str(TMP / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import radar  # noqa: E402
from app.settings import DEFAULTS  # noqa: E402

PASSED = []
T0 = 1_763_000_000 // 86400 * 86400 + 3 * 86400 + 9 * 3600   # una domenica alle 9:00 (ora del broker)


def check(cond, name, detail=""):
    if not cond:
        raise AssertionError(f"FALLITO: {name} {detail}")
    PASSED.append(name)


def S(**kw):
    s = copy.deepcopy(DEFAULTS)
    s.update({"days": [0, 1, 2, 3, 4, 5, 6]})
    s.update(kw)
    return s


def bars_from(path, start=T0 - 600):
    """path: lista di prezzi (uno per minuto) → candele M1 con piccolo range."""
    out = []
    for i, p in enumerate(path):
        prev = path[i - 1] if i else p
        o, c = prev, p
        out.append((start + 60 * i, round(o, 2), round(max(o, c) + 0.05, 2), round(min(o, c) - 0.05, 2), round(c, 2)))
    return out


def ramp(a, b, n):
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def run(msgs, s, bars, spread=0.0, resolve=lambda raw: "XAUUSD", spec=None):
    m = [{"id": i + 1, "t": t, "utc": t - 7200, "text": text, "reply_to": rep, "edited": False}
         for i, (t, text, rep) in enumerate(msgs)]
    return radar.analyze(m, s, resolve, lambda sym: bars, {"XAUUSD": spread}, {"XAUUSD": 0.01},
                         {"XAUUSD": spec} if spec else None)


def main():
    flat = [2000.0] * 10
    # 1) BUY che prende il TP
    b = bars_from(flat + ramp(2000, 2012, 30) + [2012] * 30)
    sig, _, st, _ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), b)
    check(sig[0].status == "vinto" and abs(sig[0].points - 1000) < 1, "BUY al TP: vinto +1000 punti", (sig[0].status, sig[0].points))
    st_m, _ = radar.summarize(sig, [], 0, lot=0.01, usd_to_acc=1.0)
    check(st_m["in_money"] and abs(st_m["money"] - 10.0) < 1e-6, "stima in dollari con 0,01 lotti", st_m["money"])
    st_m, _ = radar.summarize(sig, [], 0, lot=0.5, usd_to_acc=0.9)
    check(abs(st_m["money"] - 1000 * 1.0 * 0.5 * 0.9) < 1e-6, "stima con il lotto scelto e in euro", st_m["money"])
    check(st["wins"] == 1 and st["winrate"] == 100.0, "statistiche vittorie")

    # 2) BUY che prende lo SL
    b = bars_from(flat + ramp(2000, 1985, 30) + [1985] * 10)
    sig, _, st, _ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), b)
    check(sig[0].status == "perso" and abs(sig[0].points + 1000) < 1, "BUY allo SL: perso −1000", sig[0].points)

    # 3) SELL al TP
    b = bars_from(flat + ramp(2000, 1988, 20) + [1988] * 10)
    sig, *_ = run([(T0, "SELL GOLD 2000 SL 2010 TP 1990", None)], S(), b)
    check(sig[0].status == "vinto" and abs(sig[0].points - 1000) < 1, "SELL al TP: vinto", sig[0].points)

    # 4) stessa candela tocca SL e TP: prudente, conta lo SL
    b = bars_from(flat + [2000.0]) + [(T0 + 60, 2000, 2015, 1985, 2000)] + bars_from([2000] * 5, T0 + 120)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), b)
    check(sig[0].status == "perso", "SL e TP nella stessa candela: conta lo SL")

    # 5) due TP + pareggio dopo il primo TP: il secondo chiude a pareggio
    b = bars_from(flat + ramp(2000, 2011, 20) + ramp(2011, 1985, 30) + [1985] * 10)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020", None)], S(be_on_tp1=True, be_offset_points=10), b)
    check(sig[0].status == "vinto" and abs(sig[0].points - (1000 + 10)) < 1, "TP1 + pareggio sul secondo", sig[0].points)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020", None)], S(be_on_tp1=False), b)
    check(abs(sig[0].points - (1000 - 1000)) < 1, "senza pareggio il secondo prende lo SL", sig[0].points)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020", None)], S(multi_tp_mode="first"), b)
    check(abs(sig[0].points - 1000) < 1, "solo primo TP", sig[0].points)

    # 6) "chiudete" del canale a metà strada
    b = bars_from(flat + ramp(2000, 2005, 10) + ramp(2005, 1980, 30))
    sig, msgs, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None), (T0 + 600, "Close all now", 1)], S(), b)
    check(sig[0].status == "vinto" and 400 < sig[0].points < 520, "chiusura del canale prima dello SL", sig[0].points)
    check(msgs[1].note == "gestione", "messaggio di gestione riconosciuto")
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None), (T0 + 600, "Close all now", 1)], S(follow_close=False), b)
    check(sig[0].status == "perso", "'chiudete' non seguito se disattivato")

    # 7) chiusura parziale 50% poi SL
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None), (T0 + 600, "chiudete metà", 1)], S(), b)
    check(sig[0].status in ("perso", "vinto") and -600 < sig[0].points < -200, "metà chiusa in guadagno, metà allo SL",
          sig[0].points)

    # 8) doppioni: tre messaggi uguali = un solo segnale
    b = bars_from(flat + ramp(2000, 2012, 30) + [2012] * 30)
    sig, msgs, st, _ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None),
                            (T0 + 20, "BUY GOLD 2000 SL 1990 TP 2010", None),
                            (T0 + 45, "🔥 buy gold 2000\nsl 1990\ntp 2010", None)], S(), b)
    check(len(sig) == 1 and st["duplicates"] == 2 and msgs[1].note == "doppio: ignorato", "doppioni ignorati nel Radar",
          (len(sig), st["duplicates"]))
    sig, _, st, _ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None),
                         (T0 + 20, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(ignore_duplicates=False), b)
    check(len(sig) == 2, "protezione spenta: contati due volte")

    # 9) fascia di entrata
    b = bars_from([2004.0] * 10 + ramp(2004, 2001, 3) + ramp(2001, 2012, 20) + [2012] * 20)
    s_zone = S(zone_wait=True, zone_wait_seconds=300)
    sig, *_ = run([(T0, "BUY GOLD 2000-2002 SL 1990 TP 2010", None)], s_zone, b)
    check(sig[0].status == "vinto" and abs(sig[0].entry_p - 2002) < 1e-6, "rientra nella fascia: entra al bordo",
          (sig[0].status, sig[0].entry_p))
    b = bars_from([2004.0] * 40 + ramp(2004, 2012, 20))
    sig, *_ = run([(T0, "BUY GOLD 2000-2002 SL 1990 TP 2010", None)], S(zone_wait=True, zone_wait_seconds=60), b)
    check(sig[0].status.startswith("ignorato") and "fascia" in sig[0].status, "non rientra: segnale ignorato", sig[0].status)
    # prezzo già migliore del segnale: entra subito (BUY più basso / SELL più alto), mai oltre lo SL
    b = bars_from([1996.0] * 40 + ramp(1996, 2015, 20) + [2015] * 10)
    zs = S(zone_wait=True, zone_wait_seconds=60, zone_margin_points=300)
    sig, *_ = run([(T0, "BUY GOLD 2010-2012 SL 1990 TP 2014", None)], zs, b)
    check(sig[0].status == "vinto" and abs(sig[0].entry_p - 1996) < 1e-6, "BUY più basso della fascia: entra subito",
          (sig[0].status, sig[0].entry_p))
    sig, *_ = run([(T0, "SELL GOLD 2010-2012 SL 2030 TP 1990", None)], zs, b)
    check(sig[0].status.startswith("ignorato"), "SELL più basso della fascia: aspetta e salta", sig[0].status)
    sig, *_ = run([(T0, "BUY GOLD 2010-2012 SL 1998 TP 2014", None)], zs, b)
    check(sig[0].status.startswith("ignorato"), "BUY oltre lo SL: non entra", sig[0].status)
    b = bars_from([2024.0] * 40 + ramp(2024, 2000, 20) + [2000] * 10)
    sig, *_ = run([(T0, "SELL GOLD 2010-2012 SL 2030 TP 2005", None)], zs, b)
    check(sig[0].status == "vinto" and abs(sig[0].entry_p - 2024) < 1e-6, "SELL più alto della fascia: entra subito",
          (sig[0].status, sig[0].entry_p))

    # troppo vicino allo Stop Loss: non entra (copia e Radar uguali)
    b = bars_from([2003.0] * 40 + ramp(2003, 2015, 20) + [2015] * 10)
    sig, *_ = run([(T0, "BUY GOLD NOW SL 2000 TP 2012", None)], S(min_sl_distance_pips=40, zone_wait_seconds=0), b)
    check(sig[0].status == "ignorato: troppo vicino allo Stop Loss", "BUY a 30 pips dallo SL: non entra", sig[0].status)
    sig, *_ = run([(T0, "BUY GOLD NOW SL 2000 TP 2012", None)], S(min_sl_distance_pips=20, zone_wait_seconds=0), b)
    check(sig[0].status == "vinto", "distanza minima 20 pips: entra", sig[0].status)
    b = bars_from([2003.0] * 15 + ramp(2003, 2015, 20) + [2015] * 10)
    sig, *_ = run([(T0, "BUY GOLD NOW SL 2000 TP 2014", None)], S(min_sl_distance_pips=40, zone_wait_seconds=1800), b)
    check(sig[0].status == "vinto" and abs(sig[0].entry_p - 2004) < 0.01,
          "il prezzo si allontana dallo SL entro il tempo: entra a 40 pips", (sig[0].status, sig[0].entry_p))
    b = bars_from([1997.0] * 40 + ramp(1997, 1985, 20) + [1985] * 10)
    sig, *_ = run([(T0, "SELL GOLD NOW SL 2000 TP 1988", None)], S(min_sl_distance_pips=40, zone_wait_seconds=0), b)
    check(sig[0].status == "ignorato: troppo vicino allo Stop Loss", "SELL a 30 pips dallo SL: non entra", sig[0].status)

    # messaggi del canale arrivati PRIMA dell'entrata (in attesa del prezzo): non toccano l'operazione
    b = bars_from([2008.0] * 20 + ramp(2008, 2002, 6) + ramp(2002, 2012, 20) + [2012] * 20)
    zs = S(zone_wait=True, zone_wait_seconds=3600, zone_margin_points=50, min_sl_distance_pips=0)
    sig, msgs, st, _ = run([(T0, "BUY GOLD NOW 2002 SL 1990 TP 2010", None), (T0 + 1, "chiudete metà", 1),
                            (T0 + 2, "chiudete tutto", 1)], zs, b)
    check(sig[0].entry_t and all(t >= sig[0].entry_t for t, _p in sig[0].exits) and sig[0].exit_reason == "TP"
          and abs(sig[0].points - (2010 - sig[0].entry_p) / 0.01) < 1,
          "messaggi prima dell'entrata ignorati: l'operazione arriva al TP intera",
          (sig[0].entry_t, sig[0].exits, sig[0].exit_reason, sig[0].points))
    # entrata immediata: i messaggi dello stesso istante valgono ancora (come prima)
    b = bars_from([2002.0] * 20 + ramp(2002, 2012, 20) + [2012] * 20)
    sig, *_ = run([(T0, "BUY GOLD NOW 2002 SL 1990 TP 2010", None), (T0 + 300, "chiudete metà", 1)], zs, b)
    check(len(sig[0].exits) == 2, "dopo l'entrata il messaggio vale", sig[0].exits)
    # ordine limit: «chiudete» prima dell'esecuzione lo cancella
    b = bars_from([2005.0] * 15 + ramp(2005, 1999, 10) + ramp(1999, 2012, 20))
    sig, *_ = run([(T0, "BUY LIMIT GOLD 2000 SL 1990 TP 2010", None), (T0 + 60, "chiudete", 1)], S(), b)
    check(sig[0].status == "ordine mai eseguito", "limit cancellato da «chiudete» prima dell'esecuzione", sig[0].status)

    # un solo prezzo (BUY NOW 2002): aspetta che il prezzo torni vicino, come la copia vera
    b = bars_from([2008.0] * 10 + ramp(2008, 2002, 4) + ramp(2002, 2012, 20) + [2012] * 20)
    sig, *_ = run([(T0, "BUY GOLD NOW 2002 SL 1990 TP 2010", None)], S(zone_wait=True, zone_wait_seconds=600,
                                                                        zone_margin_points=50), b)
    check(sig[0].status == "vinto" and abs(sig[0].entry_p - 2002.5) < 1e-6, "un solo prezzo: entra quando ci arriva",
          (sig[0].status, sig[0].entry_p))
    b = bars_from([2008.0] * 40 + ramp(2008, 2012, 20))
    sig, *_ = run([(T0, "BUY GOLD NOW 2002 SL 1990 TP 2010", None)], S(zone_wait=True, zone_wait_seconds=60,
                                                                        zone_margin_points=50), b)
    check(sig[0].status == "ignorato: prezzo lontano da quello del segnale", "un solo prezzo lontano: ignorato",
          sig[0].status)
    sig, *_ = run([(T0, "BUY GOLD NOW 2002 SL 1990 TP 2010", None)], S(zone_wait=False), b)
    check(sig[0].status == "vinto" and abs(sig[0].entry_p - 2008) < 1e-6, "opzione spenta: entra subito come prima",
          (sig[0].status, sig[0].entry_p))
    # due segnali nello stesso istante con prezzi diversi: ognuno entra al suo prezzo
    b = bars_from([2008.0] * 10 + ramp(2008, 2003, 5) + [2003.0] * 5 + ramp(2003, 1999, 5) + ramp(1999, 2015, 20))
    sig, *_ = run([(T0, "BUY GOLD NOW 2003 SL 1990 TP 2012", None), (T0, "BUY GOLD NOW 1999 SL 1990 TP 2012", None)],
                  S(zone_wait=True, zone_wait_seconds=1800, zone_margin_points=30), b)
    check(len(sig) == 2 and abs(sig[0].entry_p - 2003.3) < 1e-6 and abs(sig[1].entry_p - 1999.3) < 1e-6
          and sig[0].entry_t < sig[1].entry_t, "stesso istante, prezzi diversi: due entrate diverse",
          [(x.status, x.entry_p, x.entry_t) for x in sig])

    # 10) ordine limit: eseguito solo se il prezzo ci arriva
    b = bars_from([2005.0] * 15 + ramp(2005, 1999, 10) + ramp(1999, 2012, 20))
    sig, *_ = run([(T0, "BUY LIMIT GOLD 2000 SL 1990 TP 2010", None)], S(), b)
    check(sig[0].status == "vinto" and sig[0].entry_p == 2000, "limit eseguito quando il prezzo scende", sig[0].status)
    b = bars_from([2005.0] * 15 + ramp(2005, 2012, 20))
    sig, *_ = run([(T0, "BUY LIMIT GOLD 2000 SL 1990 TP 2010", None)], S(pending_expiry_min=10), b)
    check(sig[0].status == "ordine mai eseguito", "limit mai raggiunto", sig[0].status)

    # 11) gestione in risposta: vale solo per quel segnale
    b = bars_from(flat + ramp(2000, 2006, 10) + ramp(2006, 1980, 30))
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2050", None),
                   (T0 + 60, "BUY GOLD 2001 SL 1991 TP 2050", None),
                   (T0 + 600, "SL a pareggio", 1)], S(ignore_duplicates=False), b)
    check(sig[0].status == "vinto" and sig[1].status == "perso", "pareggio solo sul segnale a cui risponde",
          (sig[0].status, sig[1].status))

    # 12) segnale senza SL ignorato (impostazione predefinita) / con SL predefinito
    b = bars_from(flat + ramp(2000, 2012, 30))
    sig, *_ = run([(T0, "BUY GOLD 2000 TP 2010", None)], S(), b)
    check(sig[0].status == "ignorato: senza Stop Loss", "senza SL: ignorato", sig[0].status)
    sig, *_ = run([(T0, "BUY GOLD 2000 TP 2010", None)], S(no_sl_mode="default", default_sl_points=500), b)
    check(sig[0].status == "vinto", "senza SL: SL predefinito", sig[0].status)

    # 13) ancora aperto alla fine dei dati
    b = bars_from(flat + ramp(2000, 2004, 20))
    sig, _, st, _ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2050", None)], S(), b)
    check(sig[0].status == "aperto" and st["open"] == 1 and sig[0].points > 0, "operazione ancora aperta")

    # 14) mancano i prezzi
    sig, _, st, _ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), [])
    check(sig[0].status == "senza prezzi" and st["no_data"] == 1, "prezzi non disponibili")

    # 15) statistiche: serie di perdite e drawdown
    msgs, path = [], [2000.0] * 5
    for k in range(4):
        msgs.append((T0 + k * 3600, f"BUY GOLD 2000 SL {1990 - k * 0.01:.2f} TP 2010", None))
    b = bars_from([2000.0] * 10 + ramp(2000, 1985, 20) + ramp(1985, 2000, 20) + [2000] * 200)
    b = []
    for k in range(4):
        start = T0 + k * 3600 - 300
        b += bars_from([2000.0] * 6 + ramp(2000, 1985, 20) + ramp(1985, 2000, 20), start)
    b.sort()
    sig, _, st, eq = run(msgs, S(), b)
    check(st["losses"] == 4 and st["worst_streak"] == 4 and abs(st["max_drawdown"] - 4006) < 1, "serie peggiore e drawdown",
          (st["losses"], st["worst_streak"], st["max_drawdown"]))
    check(len(eq) == 4 and abs(eq[-1][1] + 4006) < 1, "curva dei risultati")

    # 16) filtri delle impostazioni
    b = bars_from(flat + ramp(2000, 2012, 30))
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(direction_filter="sell"), b)
    check(sig[0].status == "ignorato: solo SELL", "filtro direzione", sig[0].status)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(blocked_symbols="XAUUSD"), b)
    check(sig[0].status == "ignorato: simbolo bloccato", "simbolo bloccato", sig[0].status)

    # 17) ora del broker
    winter = 1_763_640_000          # novembre
    summer = 1_752_000_000          # luglio
    check(radar.server_offset(winter, 7200) == 7200 and radar.server_offset(summer, 7200) == 10800, "ora legale del broker")
    check(radar.server_offset(summer, 0) == 0, "broker in UTC")
    check(radar.measure_offset(1_763_640_000 + 7200 + 3, 1_763_640_000, None) == 7200, "misura differenza oraria")
    check(radar.measure_offset(1_763_000_000, 1_763_640_000, 10800) == 10800, "mercato chiuso: usa l'ultima misura")

    # 18) report salvato e riletto
    sig, msgs, st, eq = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), bars_from(flat + ramp(2000, 2012, 30)))
    rep = radar.Report(-100, "Canale", 30, "oggi", sig, msgs, st, eq)
    radar.save_report(rep)
    back = radar.load_report(-100)
    check(back and back.signals[0].status == "vinto" and back.stats["wins"] == 1, "report salvato e riletto")

    # 19) spread: i BUY comprano all'ask, i SELL chiudono all'ask
    b = bars_from(flat + ramp(2000, 2012, 30) + [2012] * 30)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), b, spread=0.30)
    check(abs(sig[0].entry_p - 2000.30) < 1e-6 and abs(sig[0].points - 970) < 1, "spread sull'entrata BUY",
          (sig[0].entry_p, sig[0].points))
    b = bars_from(flat + ramp(2000, 2009.8, 20) + [2009.8] * 20)
    sig, *_ = run([(T0, "SELL GOLD 2000 SL 2010 TP 1990", None)], S(), b, spread=0.30)
    check(sig[0].status == "perso", "SELL: lo SL scatta sull'ask (bid + spread)", sig[0].status)
    sig, *_ = run([(T0, "SELL GOLD 2000 SL 2010 TP 1990", None)], S(), b, spread=0.0)
    check(sig[0].status == "aperto", "senza spread lo stesso SELL resterebbe aperto", sig[0].status)

    # 20) entrata al secondo esatto del messaggio (dentro la candela al minuto)
    b = bars_from(flat + [2000.0]) + [(T0 + 60, 2000.0, 2006.1, 1999.9, 2006.0)] + bars_from([2006.0] * 40, T0 + 120)
    sig, *_ = run([(T0 + 90, "BUY GOLD 2000 SL 1990 TP 2050", None)], S(), b)
    check(abs(sig[0].entry_p - 2003.1) < 0.01 and sig[0].entry_t == T0 + 91, "entrata al secondo (1 s dopo il messaggio)",
          (sig[0].entry_p, sig[0].entry_t))

    # 21) salto oltre il TP: chiude al prezzo (migliore) di apertura
    b = bars_from(flat + [2000.0] * 5) + [(T0 + 300, 2015.0, 2016.0, 2014.0, 2015.0)] + bars_from([2015.0] * 5, T0 + 360)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), b)
    check(sig[0].status == "vinto" and abs(sig[0].points - 1500) < 1, "salto oltre il TP", sig[0].points)

    # 22) un'operazione lunga viene seguita fino alla chiusura (non si ferma dopo 5 giorni)
    long_path = [2000.0] * 10 + [2001.0] * (8 * 1440) + ramp(2001, 2012, 30)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), bars_from(long_path))
    check(sig[0].status == "vinto" and sig[0].exit_t > T0 + 7 * 86400, "operazione di 8 giorni seguita fino al TP")

    # 23) verifica automatica dell'ora del broker
    bars = bars_from([2000 + 0.5 * i for i in range(600)])
    msgs = [{"id": k, "t": T0 + k * 1800 - 3600, "utc": 0, "text": f"BUY GOLD {2000 + 0.5 * (10 + k * 30):.2f} SL 1900 TP 2900",
             "reply_to": None} for k in range(1, 8)]
    check(radar.calibrate_offset(msgs, S(), lambda r: "XAUUSD", lambda sym: bars) == 3600, "ora del broker corretta di +1 h")
    msgs_ok = [dict(m, t=m["t"] + 3600) for m in msgs]
    check(radar.calibrate_offset(msgs_ok, S(), lambda r: "XAUUSD", lambda sym: bars) == 0, "ora giusta: nessuna correzione")

    # 24) valore di un punto (contratti standard)
    vp = radar.value_per_point
    check(vp("XAUUSD.m", 0.01, 2000) == 1.0 and abs(vp("EURUSD", 0.00001, 1.08) - 1.0) < 1e-9, "valore punto oro/EURUSD")
    check(abs(vp("USDJPY", 0.001, 150) - 100 / 150) < 1e-9 and vp("US30", 0.1, 39000) is None and
          vp("EURGBP", 0.00001, 0.85) is None, "valore punto USDJPY, indici e cross")

    # 25) doppioni: stessa regola della copia vera
    b = bars_from(flat + ramp(2000, 2012, 30) + [2012] * 30)
    sig, msgs, st, _ = run([(T0, "BUY 2000 SL 1990 TP 2010", None), (T0 + 10, "BUY GOLD 2000 SL 1990 TP 2010", None),
                            (T0 + 20, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), b)
    check(len(sig) == 1 and st["duplicates"] == 1 and msgs[0].note == "simbolo non riconosciuto",
          "segnale senza simbolo non conta per i doppi (come la copia)", (len(sig), st["duplicates"]))
    sig, msgs, st, _ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2050", None), (T0 + 300, "chiudete metà", 1),
                            (T0 + 320, "chiudete metà", 1)], S(), b)
    check(st["duplicates"] == 1 and msgs[2].note == "doppio: ignorato" and len(sig[0].exits) == 1,
          "'chiudete metà' ripetuto: una sola chiusura", (st["duplicates"], sig[0].exits))
    sig, msgs, st, _ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None), (T0 + 11 * 60, "BUY GOLD 2000 SL 1990 TP 2010", None)],
                           S(), b)
    check(len(sig) == 2 and st["duplicates"] == 0, "oltre la finestra di 10 minuti: segnale nuovo")
    # stesso segnale riscritto (stessi valori): doppio anche nel Radar; valori diversi: segnale nuovo
    sig, msgs, st, _ = run([(T0, "BUY GOLD 2000-2003 SL 1990 TP 2010 TP 2020", None),
                            (T0, "BUY GOLD 2003-2000 SL 1990 TP 2020 TP 2010", None),
                            (T0, "📢 Gold buy 2000/2003\nSL: 1990\nTP1: 2010\nTP2: 2020", None),
                            (T0, "BUY GOLD 1996 SL 1990 TP 2010 TP 2020", None),
                            (T0 + 1, "BUY GOLD 2000 SL 1985 TP 2010", None),
                            (T0 + 1, "BUY GOLD 2000-2003 SL 1990 TP 2012 TP 2020", None),
                            (T0 + 100, "SELL GOLD 2000 SL 2010 TP 1990", None),
                            (T0 + 20 * 60, "BUY GOLD 2000-2003 SL 1990 TP 2010 TP 2020", None)], S(), b)
    check(st["duplicates"] == 2 and [m.note for m in msgs] == ["segnale", "doppio: ignorato", "doppio: ignorato",
                                                               "segnale", "segnale", "segnale", "segnale", "segnale"],
          "doppio solo con gli stessi valori; entrata, SL o TP diversi: segnale nuovo", [m.note for m in msgs])

    # 26) dentro lo stesso minuto: candela ribassista (massimo prima del minimo) → prima il TP
    b = bars_from(flat + [2000.0]) + [(T0 + 60, 2000.5, 2015, 1985, 2000)] + bars_from([2000] * 5, T0 + 120)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), b)
    check(sig[0].status == "vinto", "candela ribassista: il massimo (TP) arriva prima del minimo (SL)", sig[0].status)

    # 27) pareggio dopo il TP1 nello stesso minuto
    b = bars_from(flat + [2000.0]) + [(T0 + 60, 2000.5, 2011, 1995, 1996)] + bars_from([1996] * 30, T0 + 120)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010 TP 2030", None)], S(be_on_tp1=True, be_offset_points=10), b)
    check(sig[0].status == "vinto" and abs(sig[0].points - (1000 + 10)) < 1, "TP1 e pareggio nella stessa candela",
          sig[0].points)

    # 28) spread storico minuto per minuto (dal ponte aggiornato)
    b = [x + (50,) for x in bars_from(flat + ramp(2000, 2012, 30) + [2012] * 30)]
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), b, spread=0.10)
    check(abs(sig[0].entry_p - 2000.5) < 1e-6, "spread storico della candela al posto di quello di riserva", sig[0].entry_p)

    # 29) swap e commissioni
    wed = dt.datetime(2025, 11, 12, 10, tzinfo=dt.timezone.utc).timestamp()      # mercoledì
    check(radar._nights(wed - 2 * 86400, wed - 86400, 3) == 1, "lunedì → martedì: una notte di swap")
    check(radar._nights(wed - 3 * 86400, wed - 2 * 86400, 3) == 0, "domenica → lunedì: nessuno swap")
    check(radar._nights(wed, wed + 86400, 3) == 3, "swap triplo del mercoledì")
    check(radar._nights(wed + 2 * 86400, wed + 5 * 86400, 3) == 1, "venerdì → lunedì: una sola notte")
    check(radar._nights(wed, wed + 3600, 3) == 0, "nessuna notte nello stesso giorno")
    spec = {"vpp_acc": 0.9, "swap_long": -20.0, "swap_short": 5.0, "swap_mode": 1, "swap3": 3}
    long_path = [2000.0] * 10 + [2001.0] * (3 * 1440) + ramp(2001, 2012, 30)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010", None)], S(), bars_from(long_path), spec=spec)
    nights = radar._nights(sig[0].entry_t, sig[0].exit_t, 3)
    check(nights == 2 and abs(sig[0].swap_lot - (-20 * 0.9 * nights)) < 1e-6, "swap delle notti tenute aperte",
          (nights, sig[0].swap_lot))
    m = radar.money_of(sig[0], 0.1, None, commission=7.0)
    check(abs(m - (sig[0].points * 0.9 * 0.1 + sig[0].swap_lot * 0.1 - 7.0 * 0.1)) < 1e-9, "denaro = prezzo + swap − commissioni", m)

    # 30) limiti di posizioni aperte delle impostazioni (come la copia vera)
    b = bars_from(flat + [2000.0] * 30 + ramp(2000, 2040, 60))
    sig, _, st, _ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2030", None), (T0 + 300, "SELL EURUSD 1.08 SL 1.09 TP 1.07", None),
                         (T0 + 600, "BUY GOLD 2000 SL 1985 TP 2035", None)], S(max_open_trades=1), b)
    check(sig[2].status == "ignorato: troppe posizioni aperte" and st["limited"] >= 1, "limite posizioni aperte",
          [x.status for x in sig])
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020 TP 2030", None)], S(max_open_trades=2), b)
    check(sig[0].n_pos == 2, "TP in eccesso tagliati come nella copia", sig[0].n_pos)

    # 31) ordine pendente senza scadenza (0 = mai, come la copia)
    b = bars_from([2005.0] * 15 + [2005.0] * 2880 + ramp(2005, 1999, 10) + ramp(1999, 2012, 20))
    sig, *_ = run([(T0, "BUY LIMIT GOLD 2000 SL 1990 TP 2010", None)], S(pending_expiry_min=0), b)
    check(sig[0].status == "vinto", "limit eseguito dopo 2 giorni (nessuna scadenza)", sig[0].status)

    # 32) chiusura del canale al prezzo del momento (non del minuto dopo)
    b = bars_from(flat + [2000.0] * 9 + [2000.0]) + [(T0 + 600, 2000, 2008.1, 1999.9, 2008)] + bars_from([2008] * 5, T0 + 660)
    sig, *_ = run([(T0, "BUY GOLD 2000 SL 1990 TP 2050", None), (T0 + 605, "Close all now", 1)], S(), b)
    check(sig[0].status == "vinto" and abs(sig[0].exit_p - (2000 + 8 * 6 / 60)) < 0.02, "chiusura al prezzo del messaggio",
          sig[0].exit_p)

    print(f"TUTTI I {len(PASSED)} TEST DEL RADAR (SIMULAZIONE) SUPERATI")


if __name__ == "__main__":
    main()
