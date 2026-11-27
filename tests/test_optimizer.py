"""Test di «Migliora impostazioni».   QT_QPA_PLATFORM=offscreen python -m tests.test_optimizer"""
import math
import os
import random
import sys
import tempfile
import time
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = Path(tempfile.mkdtemp())
os.environ["TGMT5_DATA"] = str(TMP / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app import gui, optimizer, radar  # noqa: E402
from app.config import DATA_DIR  # noqa: E402
from app.settings import SettingsStore, for_channel  # noqa: E402

PASSED = []


def check(cond, name, detail=""):
    if not cond:
        raise AssertionError(f"FALLITO: {name} {detail}")
    PASSED.append(name)


CID = -100123
T0 = 1_780_000_000 // 86400 * 86400 + 86400 * 3          # un lunedì circa


def make_bars(weeks=10, seed=3):
    rnd = random.Random(seed)
    bars, price = [], 2000.0
    for d in range(weeks * 7):
        day = T0 + d * 86400
        if ((day // 86400) + 4) % 7 >= 5:                  # sabato e domenica: niente prezzi
            continue
        for mnt in range(0, 1440):
            t = day + mnt * 60
            drift = 0.004 * math.sin(t / 50000.0)
            o = price
            c = o + drift + rnd.gauss(0, 0.35)
            h, l_ = max(o, c) + abs(rnd.gauss(0, 0.2)), min(o, c) - abs(rnd.gauss(0, 0.2))
            bars.append((t, round(o, 2), round(h, 2), round(l_, 2), round(c, 2), 20))
            price = c
    return bars


def make_report(bars, s):
    rnd = random.Random(7)
    msgs, mid = [], 1000
    times = [b[0] for b in bars]
    for k in range(0, len(bars) - 600, 700):
        t = bars[k][0] + 30
        px = bars[k][4]
        buy = rnd.random() < 0.55
        e = round(px + (rnd.choice([-3, 0, 2]) if buy else rnd.choice([3, 0, -2])), 1)
        side = "BUY" if buy else "SELL"
        sl = e - 10 if buy else e + 10
        tps = [e + 5, e + 10, e + 15] if buy else [e - 5, e - 10, e - 15]
        text = f"XAUUSD {side} NOW {e}\nSL {sl}\n" + "\n".join(f"TP {x}" for x in tps)
        mid += 1
        msgs.append({"id": mid, "t": t, "utc": t - 7200, "text": text, "reply_to": None, "edited": False})
        j = min(len(bars) - 1, k + 120)
        mid += 1
        msgs.append({"id": mid, "t": times[j], "utc": times[j] - 7200, "text": "Tp 1 hit", "reply_to": mid - 1,
                     "edited": False})
    # come con il ponte aggiornato: valore del punto e swap esatti dal broker
    spec = {"vpp_acc": 0.92, "swap_long": -6.5, "swap_short": 2.1, "swap_mode": 1, "swap3": 3}
    sig, rmsgs, stats, eq = radar.analyze(msgs, s, lambda raw: "XAUUSD", lambda sym: bars, spreads={"XAUUSD": 0.2},
                                          points={"XAUUSD": 0.01}, specs={"XAUUSD": spec})
    rep = radar.Report(CID, "Canale di prova", 70, "oggi", sig, rmsgs, stats, eq, spreads={"XAUUSD": 20},
                       usd_to_acc=0.9, currency="EUR", settings_sig=radar.settings_sig(s))
    radar.save_report(rep)
    days = {}
    for b in bars:
        days.setdefault(b[0] // 86400 * 86400, []).append(b)
    for d, bb in days.items():
        p = radar._cache_path("XAUUSD", d)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join("|".join(str(v) for v in b) for b in bb))
    return rep


def small_plan():
    def first():
        out = []
        for tp in ({"multi_tp_mode": "first"}, {"multi_tp_mode": "split", "be_on_tp1": True}, {"multi_tp_mode": "last"}):
            for zw in (False, True):
                c = dict(tp, be_after_points=0, tp_hit_action="partial_be", min_sl_distance_pips=40, zone_wait=zw)
                if zw:
                    c.update(zone_margin_points=600, zone_wait_seconds=3600)
                out.append(c)
        return out

    def second(res):
        best = max(res, key=lambda r: optimizer._quick(r[1]))[0]
        best = dict(best, zone_wait=True, zone_margin_points=600, zone_wait_seconds=3600)
        return [dict(best, zone_margin_points=m, zone_wait_seconds=w, be_after_points=b)
                for m in (500, 600, 700) for w in (1800, 3600, 5400) for b in (0, 50, 100)]
    return first, second


def main():
    app = QApplication([])  # noqa: F841
    answers = {"q": QMessageBox.Yes}
    QMessageBox.question = staticmethod(lambda *a, **k: answers["q"])
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    eng = types.SimpleNamespace(settings=SettingsStore(DATA_DIR / "settings.json"))
    # impostazioni attuali volutamente scarse (SL minimo enorme: quasi nessuna operazione) → deve proporre di meglio
    eng.settings.update({"days": list(range(7)), "multi_tp_mode": "split", "zone_wait": False,
                         "min_sl_distance_pips": 95})
    radar_s = radar.effective_settings(eng.settings.get(), CID)

    # ---- dati mancanti: messaggio chiaro, nessun errore
    d = __import__("app.optimizer_ui", fromlist=["x"]).OptimizeDialog(None, eng, CID, "Canale di prova", 0.03)
    QTest.qWait(200)
    check("Aggiorna" in d.lbl_phase.text() and d.run is None, "senza analisi: spiega cosa fare", d.lbl_phase.text())
    d.reject()

    bars = make_bars()
    rep = make_report(bars, radar_s)
    payload = optimizer.prepare(radar.load_report(CID), eng.settings.get(), CID, 0.03)
    check(optimizer.check(payload) is None and payload["nweeks"] >= 9, "dati preparati dai file salvati",
          (optimizer.check(payload), payload["nweeks"]))
    check(len(payload["bars"]["XAUUSD"]) == len(bars) and payload["resolve"].get("XAUUSD") == "XAUUSD",
          "prezzi e simboli ritrovati sul disco")
    check(sum(1 for m in payload["msgs"] if m["reply_to"]) > 10, "risposte del canale mantenute")
    short = dict(payload, nweeks=3)
    check("6 settimane" in (optimizer.check(short) or ""), "pochi dati: consiglia un periodo più lungo")

    # ---- la simulazione rifatta coincide con quella del Radar
    optimizer._init(payload)
    same = optimizer.simulate({k: radar_s.get(k) for k in optimizer.KEYS})
    pts_radar = sum(x.points for x in rep.signals if x.status in ("vinto", "perso", "pareggio", "aperto"))
    sig2, *_ = radar.analyze(payload["msgs"], dict(radar_s), lambda raw: "XAUUSD", lambda sym: payload["bars"]["XAUUSD"],
                             spreads=payload["spreads"], points=payload["points"])
    check(abs(sum(x.points for x in sig2 if x.status in ("vinto", "perso", "pareggio", "aperto")) - pts_radar) < 1,
          "stessi risultati del Radar con le impostazioni attuali")
    check(same["n"] > 50, "operazioni simulate", same["n"])
    check(payload["vpp_acc"].get("XAUUSD") == 0.92, "valore del punto del broker usato per gli euro")
    check(radar.simulate_signal(radar.RSignal(1, T0 + 600, "XAUUSD", "buy", "market", None, None, 1990.0, [2030.0], "x"),
                                bars, [], dict(radar_s), point=0.01, spec={"vpp_acc": 1.0}) is None,
          "dati del simbolo incompleti: nessun errore")

    # ---- ricerca completa (piano ridotto), con più processori
    r = optimizer.Run(payload, workers=2, plan=small_plan())
    t0 = time.time()
    r.run()
    check(r.error is None and r.result, "ricerca completata", r.error)
    res = r.result
    check(7 <= res["tested"] <= 1 + 6 + 27, "combinazioni provate senza doppioni", res["tested"])
    print(f"   (ricerca ridotta: {res['tested']} combinazioni in {time.time() - t0:.1f}s)")
    for k, p in res["picks"].items():
        check(p["train"] > 0 and p["test"] > 0 and p["nb_min"] > 0, f"proposta «{k}» in guadagno anche sui mesi mai visti",
              (p["train"], p["test"], p["nb_min"]))
    check(set(res["series"]) == {"attuali"} | set(res["picks"]) and all(len(v) > 30 for v in res["series"].values()),
          "curve per il grafico")

    # ---- se nessuna combinazione è migliore delle attuali, nessuna proposta
    rows = optimizer.evaluate([({"a": 1}, res["current"]["m"])])
    check(optimizer.pick(rows, rows[0]) == {}, "impostazioni già migliori: nessuna proposta")

    # ---- descrizione delle differenze
    cur = {k: radar_s.get(k) for k in optimizer.KEYS}
    ch = optimizer.changes(cur, dict(cur, multi_tp_mode="first", zone_wait=True, zone_margin_points=600, zone_wait_seconds=3600))
    labels = [c[0] for c in ch]
    check("Segnali con più TP" in labels and "Margine" in labels and "Tempo massimo di attesa" in labels and
          any("Solo il primo TP" in c[2] for c in ch), "elenco delle impostazioni che cambiano", ch)
    check(next(c for c in ch if c[0] == "Margine")[1] == "non usato", "margine «non usato» se l'attesa è spenta", ch)
    check(optimizer.changes(cur, cur) == [], "nessuna differenza se uguali")

    # ---- interruzione
    stop = optimizer.Run(payload, workers=1)
    import threading
    th = threading.Thread(target=stop.run)
    th.start()
    time.sleep(1.5)
    stop.cancel()
    th.join(30)
    check(not th.is_alive() and stop.result is None and stop.done < stop.total, "«Ferma» interrompe la ricerca",
          (stop.done, stop.total))

    # ---- finestra completa
    from app.optimizer_ui import OptimizeDialog
    applied = []
    d = OptimizeDialog(None, eng, CID, "Canale di prova", 0.03, on_applied=lambda: applied.append(1), workers=2,
                       plan=small_plan())
    d.show()
    for _ in range(600):
        QTest.qWait(100)
        if d.stack.currentIndex() == 1:
            break
    check(d.stack.currentIndex() == 1, "risultati mostrati nella finestra", d.lbl_phase.text())
    check(len(d.chart.series) == 1 + len(d.result["picks"]) and d.table.rowCount() >= 2, "grafico e tabella mese per mese")
    d.resize(1180, 860)
    QTest.qWait(100)
    QTest.mouseMove(d.chart, d.chart.rect().center())
    QTest.qWait(50)
    check(d.chart.hover is not None, "valori al passaggio del mouse")
    d.grab().save(str(TMP / "migliora.png"))
    key = next((k for k in ("consigliata", "guadagno", "tranquilla") if k in d.apply_buttons), None)
    check(key is not None, "pulsante «Applica» presente")
    general_before = eng.settings.get()
    cfg = d.result["picks"][key]["cfg"]
    answers["q"] = QMessageBox.No
    d.apply_buttons[key].click()
    check(not eng.settings.get()["channel_overrides"], "con «No» non cambia niente")
    answers["q"] = QMessageBox.Yes
    d.apply_buttons[key].click()
    over = eng.settings.get()["channel_overrides"].get(str(CID), {})
    eff = for_channel(eng.settings.get(), CID)
    check(all(eff[k] == cfg[k] for k in optimizer.KEYS if k in cfg and not (k == "tp_index" and cfg.get("multi_tp_mode") != "index")),
          "«Applica» mette le impostazioni per il canale", (over, cfg))
    after = eng.settings.get()
    check({k for k in general_before if general_before[k] != after[k]} == {"channel_overrides"},
          "le impostazioni generali non cambiano")
    check(applied == [1], "dopo «Applica» il Radar si aggiorna")
    check(radar.settings_sig(radar.effective_settings(after, CID)) != rep.settings_sig, "il Radar vede le nuove impostazioni")

    # ---- il ⚙ del canale conserva le impostazioni che non mostra (es. TP n° 2)
    eng.settings.update({"channel_overrides": {str(CID): dict(over, multi_tp_mode="index", tp_index=2)}})
    cd = gui.ChannelSettingsDialog(None, eng, CID, "Canale di prova")
    cd.save()
    check(eng.settings.get()["channel_overrides"][str(CID)].get("tp_index") == 2, "il ⚙ conserva «TP n° 2»")
    check(any(it[0] == ["zone_margin_points"] for it in cd.items), "margine anche nel ⚙ del canale")
    # ---- risultato salvato: riaprendo (anche dopo aver chiuso l'app) si ritrova senza rifare il calcolo
    saved = optimizer.load_result(CID)
    check(saved and saved["result"]["tested"] == d.result["tested"] and saved.get("saved_at") and
          saved.get("report_generated") == "oggi", "risultato salvato sul disco con data e dati usati")
    backs = []
    v = OptimizeDialog(None, eng, CID, "Canale di prova", 0.03, embedded=True, on_back=lambda: backs.append(1),
                       workers=2, plan=small_plan())
    v.show()
    QTest.qWait(300)
    check(v.run is None and v.stack.currentIndex() == 1 and v.btn_redo.isVisible(),
          "riaperto: risultato salvato mostrato subito, con «Rifai il calcolo»")
    check("Calcolo del" in v.lbl_saved.text(), "data del calcolo mostrata", v.lbl_saved.text())
    check("cambiate dopo questo calcolo" in v.lbl_saved.text(), "avvisa se le impostazioni sono cambiate dopo",
          v.lbl_saved.text())
    v.btn_back.click()
    check(backs == [1] and v.run is None, "«← Torna al Radar»")
    # Radar aggiornato dopo il calcolo: lo segnala
    rep2 = radar.load_report(CID)
    rep2.generated = "domani"
    radar.save_report(rep2)
    v2 = OptimizeDialog(None, eng, CID, "Canale di prova", 0.03, embedded=True, workers=2, plan=small_plan())
    QTest.qWait(300)
    check("aggiornato dopo questo calcolo" in v2.lbl_saved.text(), "avvisa se il Radar è stato aggiornato dopo",
          v2.lbl_saved.text())
    # «Rifai il calcolo»: ricalcola e salva il nuovo risultato
    v2.btn_redo.click()
    QTest.qWait(100)
    check(v2.is_running() and v2.stack.currentIndex() == 0, "«Rifai il calcolo» riparte")
    # la pagina viene chiusa durante il calcolo: riaprendola si ritrova il calcolo in corso, che si salva da solo
    v3 = OptimizeDialog(None, eng, CID, "Canale di prova", 0.03, embedded=True, workers=2, plan=small_plan())
    QTest.qWait(100)
    check(v3.run is v2.run, "pagina riaperta: ritrova il calcolo in corso")
    v2.deleteLater()
    for _ in range(600):
        QTest.qWait(100)
        if not v3.is_running() and v3.stack.currentIndex() == 1:
            break
    check(optimizer.load_result(CID)["report_generated"] == "domani" and v3.stack.currentIndex() == 1,
          "il calcolo finito si salva anche se la pagina è stata chiusa")
    check("aggiornato dopo" not in v3.lbl_saved.text(), "nessun avviso dopo il ricalcolo", v3.lbl_saved.text())
    # «Ferma» in una pagina: torna al risultato salvato
    v3.btn_redo.click()
    QTest.qWait(200)
    v3.stop()
    for _ in range(300):
        QTest.qWait(100)
        if not v3.is_running():
            break
    QTest.qWait(300)
    check(v3.stack.currentIndex() == 1, "dopo «Ferma» resta il risultato salvato")

    print(f"TUTTI I {len(PASSED)} TEST DI «MIGLIORA IMPOSTAZIONI» SUPERATI")
    print("screenshot:", TMP)
    os._exit(0)


if __name__ == "__main__":
    main()
