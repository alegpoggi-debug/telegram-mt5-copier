"""Test completo del Radar: Telegram e MetaTrader simulati, analisi, finestra, grafico, cache.
QT_QPA_PLATFORM=offscreen python -m tests.test_radar_ui
"""
import datetime as dt
import math
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = Path(tempfile.mkdtemp())
os.environ["TGMT5_DATA"] = str(TMP / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import telethon  # noqa: E402

OFFSET = 7200          # broker GMT+2 (novembre)
NOW_UTC = time.time()
CH = -1001112223334
CH2 = -1005556667778


def price(t_server):
    return round(2000 + 12 * math.sin(t_server / 2400) + 4 * math.sin(t_server / 380), 2)


def make_messages():
    """Messaggi del canale negli ultimi 4 giorni: segnali, doppioni, gestione, un messaggio modificato."""
    out, mid = [], 0
    for k in range(10):
        utc = NOW_UTC - 4 * 86400 + k * 8 * 3600 + 777
        p = price(int(utc) + OFFSET)
        side = "BUY" if k % 2 == 0 else "SELL"
        sl, tp = (p - 6, p + 6) if side == "BUY" else (p + 6, p - 6)
        mid += 1
        text = f"{side} GOLD {p:.2f}\nSL {sl:.2f}\nTP {tp:.2f}"
        out.append(types.SimpleNamespace(id=mid, date=dt.datetime.fromtimestamp(utc, dt.timezone.utc), message=text,
                                         reply_to_msg_id=None, edit_date=dt.datetime.now() if k == 3 else None))
        if k % 3 == 0:     # il canale rimanda lo stesso segnale
            mid += 1
            out.append(types.SimpleNamespace(id=mid, date=dt.datetime.fromtimestamp(utc + 25, dt.timezone.utc),
                                             message=text, reply_to_msg_id=None, edit_date=None))
        if k == 4:
            mid += 1
            out.append(types.SimpleNamespace(id=mid, date=dt.datetime.fromtimestamp(utc + 1800, dt.timezone.utc),
                                             message="SL a pareggio ✅", reply_to_msg_id=mid - 1, edit_date=None))
    mid += 1
    out.append(types.SimpleNamespace(id=mid, date=dt.datetime.fromtimestamp(NOW_UTC - 3600, dt.timezone.utc),
                                     message="Buongiorno a tutti!", reply_to_msg_id=None, edit_date=None))
    out.sort(key=lambda m: m.date)
    return out


MESSAGES = make_messages()


class FakeClient:
    def __init__(self, *a, **k):
        self.connected = False
        self.cache_ok = False

    async def connect(self):
        self.connected = True

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return types.SimpleNamespace(first_name="Ale", username="ale")

    def add_event_handler(self, *a):
        pass

    async def get_dialogs(self):
        self.cache_ok = True

    async def iter_dialogs(self):
        for cid, n in ((CH, "Canale Radar"), (CH2, "Secondo Canale"), (-1009, "Canale Copia")):
            yield types.SimpleNamespace(id=cid, name=n, is_channel=True, is_group=False, entity=None)

    async def iter_messages(self, cid, limit=None):
        if not self.cache_ok:
            raise ValueError("Could not find the input entity")   # come Telethon se il canale non è in memoria
        msgs = MESSAGES if cid == CH else [m for m in MESSAGES if "BUY" in m.message]
        for m in reversed(msgs):       # dal più recente
            yield m

    async def disconnect(self):
        self.connected = False


telethon.TelegramClient = FakeClient

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app import gui, radar, radar_ui  # noqa: E402
from app.bridge import _parse_kv  # noqa: E402
from app.engine import Engine  # noqa: E402

QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
PASSED = []


def check(cond, name, detail=""):
    if not cond:
        raise AssertionError(f"FALLITO: {name} {detail}")
    PASSED.append(name)


def wait_until(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        QTest.qWait(50)
        if cond():
            return True
    return False


class FakeEA(threading.Thread):
    def __init__(self, d):
        super().__init__(daemon=True)
        self.dir = d / "TGBridge"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "symbols.txt").write_text("XAUUSD.m\r\nEURUSD.m\r\n")
        self.rates_calls = 0
        self.precise = True        # ponte aggiornato (comandi RATESX e SPEC)

    def run(self):
        while True:
            for f in sorted(self.dir.glob("cmd_*.txt")):
                kv = _parse_kv(f.read_text())
                f.unlink()
                cid = f.name[4:-4]
                now_server = int(time.time()) + OFFSET
                res = {"ok": 0, "msg": "?", "price": now_server}
                if kv["action"] == "QUOTE":
                    if kv["symbol"].startswith("EURUSD"):
                        res = {"ok": 1, "price": 1.0800, "msg": "ask:1.08010 point:0.00001000"}
                    else:
                        bid = price(now_server)
                        res = {"ok": 1, "price": bid, "msg": f"ask:{bid + 0.25:.2f} point:0.01000000"}
                if kv["action"] in ("RATESX", "SPEC") and not self.precise:
                    res = {"ok": 0, "msg": f"azione sconosciuta: {kv['action']}", "price": now_server}
                elif kv["action"] == "SPEC":
                    (self.dir / f"data_{cid}.txt").write_text(
                        "contract=100\ntick_value=0.92592593\ntick_size=0.01\npoint=0.01\ndigits=2\nswap_long=-30\n"
                        "swap_short=10\nswap_mode=1\nswap3=3\naccount_currency=EUR\n")
                    res = {"ok": 1, "msg": f"data_{cid}.txt", "price": now_server}
                if kv["action"] == "RATES" or (kv["action"] == "RATESX" and self.precise):
                    self.rates_calls += 1
                    sec = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}[kv["tf"]]
                    start = int(kv["from"]) // sec * sec
                    stop = min(int(kv["to"]) or now_server, now_server)
                    lines, t = [], start
                    while t <= stop:
                        if dt.datetime.fromtimestamp(t, dt.timezone.utc).weekday() < 5 or True:
                            o, c = price(t), price(t + sec)
                            line = f"{t}|{o:.2f}|{max(o, c) + .3:.2f}|{min(o, c) - .3:.2f}|{c:.2f}"
                            lines.append(line + ("|30" if kv["action"] == "RATESX" else ""))
                        t += sec
                    lines = lines[-3000:]
                    (self.dir / f"data_{cid}.txt").write_text("\r\n".join(lines) + "\r\n")
                    res = {"ok": 1, "msg": f"data_{cid}.txt", "price": now_server}
                (self.dir / f"rtmp_{cid}").write_text("\r\n".join(f"{k}={v}" for k, v in res.items()) + "\r\n")
                os.replace(self.dir / f"rtmp_{cid}", self.dir / f"res_{cid}.txt")
            (self.dir / "state.tmp").write_text("account|1|0|10000.00|10000.00|EUR|9000.00|Demo\r\ntime|x\r\n")
            os.replace(self.dir / "state.tmp", self.dir / "state.txt")
            time.sleep(0.02)


def main():
    app = QApplication([])  # noqa: F841
    ea = FakeEA(TMP / "Common/Files")
    ea.start()
    ui = gui.UiBridge()
    eng = Engine(ui)
    eng.settings.update({"mt5_dir": str(TMP / "Common/Files"), "tg_api_id": "123", "tg_api_hash": "h" * 32,
                         "days": list(range(7))})   # le prove devono funzionare in qualsiasi giorno
    win = gui.MainWindow(ui, eng)
    win.show()
    eng.start()
    wait_until(lambda: eng.mt5_connected() and eng.tg_authorized, 15)
    check(eng.mt5_connected() and eng.tg_authorized, "Telegram e MetaTrader simulati collegati")

    # ---------------------------------------------------- interruttore Radar accanto ai canali
    d = gui.SettingsDialog(win, eng, "Telegram")
    d.show()
    wait_until(lambda: len(d.rows) == 3, 5)
    row = next(r for r in d.rows if r.cid == CH)
    QTest.mouseClick(row.radar, Qt.LeftButton)
    QTest.qWait(100)
    check(eng.settings["radar_channels"] == [{"id": CH, "name": "Canale Radar"}], "interruttore Radar salva il canale")
    check(not row.isChecked() and eng.settings["tg_channels"] == [], "il Radar non attiva la copia")
    check(CH not in eng.channels, "il canale del Radar non viene copiato")
    d.save()
    d = gui.SettingsDialog(win, eng, "Telegram")
    check(any(r.cid == CH and r.radar.isChecked() and not r.isChecked() for r in d.rows), "riaprendo resta nel Radar")
    before = eng.settings.get()
    d.save()
    check(eng.settings.get() == before, "salvare senza toccare non cambia niente")

    # ---------------------------------------------------- pagina Radar e analisi
    d.show()
    d.open_radar()
    rw = d.radar
    check(d.stack.currentWidget() is rw and rw.isVisible() and rw.nav.count() == 1,
          "Apri Radar mostra il canale nella pagina Radar")
    check(not hasattr(radar_ui, "RadarSettingsDialog") and not hasattr(radar_ui, "RadarWindow"),
          "nessun menu né finestra a parte per il Radar")
    check("Aggiorna" in rw.lbl_progress.text(), "invito ad analizzare")
    check([rw.cb_days.itemData(i) for i in range(rw.cb_days.count())] == [7, 30, 60, 90, 180, 365] and
          rw.cb_days.itemText(5) == "Ultimo anno", "periodi fino a un anno")
    check(rw.btn_opt.text() == "Migliora impostazioni", "pulsante «Migliora impostazioni» nel Radar")
    rw.open_optimizer()          # prima dell'analisi: spiega cosa fare, non apre niente
    check("Aggiorna" in rw.lbl_progress.text(), "senza analisi: invito ad aggiornare", rw.lbl_progress.text())
    rw.cb_days.setCurrentIndex(rw.cb_days.findData(7))
    t0 = time.time()
    rw.btn_run.click()
    ok = wait_until(lambda: not rw.running and rw.report is not None, 60)
    first_time = time.time() - t0
    check(ok, "analisi completata", rw.lbl_progress.text())
    rep = rw.report
    st = rep.stats
    check(st["signals"] == 10 and st["duplicates"] == 4, "10 segnali, 4 doppioni ignorati", (st["signals"], st["duplicates"]))
    check(st["edited"] == 1, "messaggio modificato riconosciuto", st["edited"])
    check(st["executed"] >= 8 and st["wins"] + st["losses"] + st["open"] <= st["executed"], "segnali simulati", st)
    statuses = {s.status for s in rep.signals}
    check(statuses <= {"vinto", "perso", "pareggio", "aperto"}, "esiti validi", statuses)
    cards = rw.feed_cards()
    check(rw.t_sig.rowCount() == 10 and len(cards) == len(MESSAGES), "messaggi e tabella dei segnali",
          (rw.t_sig.rowCount(), len(cards)))
    first_card_date = cards[0].findChildren(type(rw.lbl_name))[0].text()
    check("/" in first_card_date and ":" in first_card_date, "data e ora di ogni messaggio", first_card_date)
    minis = [c.findChild(radar_ui.MiniChart) for c in cards]
    with_chart = [m for m in minis if m is not None]
    check(len(with_chart) == st["executed"] and all(len(m.sig.spark) > 5 for m in with_chart),
          "miniatura del grafico accanto a ogni segnale eseguito", (len(with_chart), st["executed"]))
    outcomes = {radar_ui._outcome(m.sig) for m in with_chart}
    check(outcomes <= {"TP", "SL", "Pareggio", "TP + pareggio", "Chiuso dal canale", "TP + SL", "Aperto"} and
          ("TP" in outcomes or "SL" in outcomes), "esito accanto alla miniatura (TP / SL / pareggio)", outcomes)
    check(all(p.isVisible() for p in rw.pills) and "€" in rw.pills[2].text() and "punti" in rw.pills[2].text(),
          "riepilogo in euro (conto in EUR) con i punti", rw.pills[2].text())
    check(rep.spreads == {"XAUUSD.m": 25} and "spread considerato: XAUUSD.m 25 punti" in rw.lbl_progress.text(),
          "spread reale letto da MetaTrader", (rep.spreads, rw.lbl_progress.text()))
    check(rep.currency == "EUR" and abs(rep.usd_to_acc - 1 / 1.08005) < 1e-6, "cambio dollaro/euro", rep.usd_to_acc)
    check(rep.precise and "dati esatti dal ponte" in rw.lbl_progress.text(), "dati esatti dal ponte aggiornato")
    done = [x for x in rep.signals if x.status in ("vinto", "perso", "pareggio", "aperto")]
    check(all(abs(x.vpp_acc - 0.92592593) < 1e-9 for x in done), "valore del punto esatto in euro dal ponte")
    check(all(x.swap_lot is not None for x in done), "swap calcolato (0 per le operazioni chiuse in giornata)")
    check(all(abs((x.entry_p - x.entry) - 0.30) < 0.25 for x in done if x.side == "buy" and x.entry),
          "BUY: entrata con lo spread storico (30 punti)")
    # commissione: abbassa il risultato di 7 € × lotto × posizioni
    def set_estimate(lot, comm):
        rw.sp_lot.setValue(lot)
        rw.sp_comm.setValue(comm)
    set_estimate(0.10, 0.0)
    base, _ = radar.summarize(rep.signals, rep.messages, 0, 0.10, rep.usd_to_acc, 0.0)
    set_estimate(0.10, 7.0)
    withc, _ = radar.summarize(rep.signals, rep.messages, 0, 0.10, rep.usd_to_acc, 7.0)
    check(abs((base["money"] - withc["money"]) - 7.0 * 0.10 * len(done)) < 0.05, "commissione per lotto sottratta",
          (base["money"], withc["money"]))
    check(radar.prefs().get("commission") == 7.0, "commissione ricordata")
    set_estimate(0.01, 0.0)
    m1, _ = radar.summarize(rep.signals, rep.messages, 0, 0.01, rep.usd_to_acc)
    txt1 = rw.pills[2].text()
    set_estimate(0.10, 0.0)
    m10, _ = radar.summarize(rep.signals, rep.messages, 0, 0.10, rep.usd_to_acc)
    check(abs(m10["money"] - 10 * m1["money"]) < 0.11 and txt1 != rw.pills[2].text(), "lotto 0,10 = 10 volte 0,01",
          (m1["money"], m10["money"]))
    check(radar.prefs().get("lot") == 0.1 and radar_ui.RadarPanel(eng).sp_lot.value() == 0.1,
          "lotto scelto ricordato e mostrato")
    cell = rw.t_sig.item(0, 7).text()
    check(("€" in cell) or cell == "", "risultato di ogni segnale in euro", cell)
    check("€" in rw.channel_gain_text(0), "guadagno del canale accanto al nome", rw.channel_gain_text(0))
    notes = [m.note for m in rep.messages]
    check(notes.count("doppio: ignorato") == 4 and "gestione" in notes, "nota dei messaggi", notes)
    # coerenza: i punti in alto sono la somma dei segnali
    check(abs(sum(s.points for s in rep.signals if s.status in ("vinto", "perso", "pareggio", "aperto")) - st["points"]) < 0.5,
          "totale coerente")

    # due canali nel Radar: ognuno con il suo guadagno separato
    eng.settings.update({"radar_channels": [{"id": CH, "name": "Canale Radar"}, {"id": CH2, "name": "Secondo Canale"}]})
    rw.reload_channels()
    rw.nav.setCurrentRow(1)
    rw.btn_run.click()
    wait_until(lambda: not rw.running and rw.report is not None and rw.report.channel_id == CH2, 60)
    g1, g2 = rw.channel_gain_text(0), rw.channel_gain_text(1)
    rep2 = radar.load_report(CH2)
    check(rep2.stats["signals"] == 5 and g1 != g2 and "€" in g1 and "€" in g2, "guadagni dei canali separati", (g1, g2))
    check(len(rw.feed_cards()) == 5 + 2, "messaggi del secondo canale (5 segnali + 2 doppioni)", len(rw.feed_cards()))
    rw.nav.setCurrentRow(0)
    check(rw.report.channel_id == CH and len(rw.feed_cards()) == len(MESSAGES), "clic su un canale: i suoi messaggi")

    # il Radar segue le impostazioni generali e quelle del singolo canale
    general = eng.settings.get()
    eff0 = radar.effective_settings(general, CH)
    check(all(eff0[k] == general[k] for k in radar.SIM_KEYS), "senza personalizzazioni: impostazioni generali")
    eng.settings.update({"channel_overrides": {str(CH): {"multi_tp_mode": "first", "be_after_points": 150,
                                                         "be_on_tp1": False}}})
    eff = radar.effective_settings(eng.settings.get(), CH)
    check(eff["multi_tp_mode"] == "first" and eff["be_after_points"] == 150 and not eff["be_on_tp1"],
          "il Radar usa le impostazioni del canale")
    eff2 = radar.effective_settings(eng.settings.get(), CH2)
    check(eff2["multi_tp_mode"] == general["multi_tp_mode"] and eff2["be_after_points"] == general["be_after_points"],
          "l'altro canale resta con le generali")
    rw.show_saved()
    check("impostazioni cambiate" in rw.lbl_progress.text(), "avviso: analisi da aggiornare", rw.lbl_progress.text())
    rw.nav.setCurrentRow(1)
    check("cambiate" not in rw.lbl_progress.text(), "nessun avviso sull'altro canale", rw.lbl_progress.text())
    rw.nav.setCurrentRow(0)
    rw.btn_run.click()
    wait_until(lambda: not rw.running, 60)
    check(rw.report.settings_sig == radar.settings_sig(eff) and "cambiate" not in rw.lbl_progress.text(),
          "analisi rifatta con le impostazioni del canale")
    first_tp_only = rw.report.stats["executed"]
    check(first_tp_only >= 8, "simulazione con «solo il primo TP»", first_tp_only)
    # un'impostazione generale cambiata vale anche per il Radar
    eng.settings.update({"zone_wait": not general["zone_wait"]})
    rw.show_saved()
    check("impostazioni cambiate" in rw.lbl_progress.text(), "anche le generali valgono per il Radar")
    eng.settings.update({"zone_wait": general["zone_wait"], "channel_overrides": {}})
    rw.btn_run.click()
    wait_until(lambda: not rw.running, 60)
    check(rw.report.settings_sig == radar.settings_sig(eff0), "tornato alle impostazioni generali")

    # giorni attivi: il Radar salta i segnali dei giorni in cui la copia non opera (come la copia vera)
    # giorno del Mac di ogni segnale (dall'ora del messaggio, non da quella del broker)
    day_of = {m.id: m.date.timestamp() for m in MESSAGES}
    day_of = {i: dt.datetime.fromtimestamp(t).weekday() for i, t in day_of.items()}
    off = sorted({day_of[x.msg_id] for x in rw.report.signals})[0]
    eng.settings.update({"days": [d for d in range(7) if d != off]})
    rw.btn_run.click()
    wait_until(lambda: not rw.running, 60)
    skipped = [x for x in rw.report.signals if x.status == "ignorato: giorno non attivo"]
    expect = [x.msg_id for x in rw.report.signals if day_of[x.msg_id] == off]
    check(skipped and [x.msg_id for x in skipped] == expect,
          "segnali dei giorni non attivi saltati", [(x.status, x.t) for x in rw.report.signals])
    check(rw.report.stats["signals"] == 10, "i segnali saltati restano nell'elenco")
    eng.settings.update({"days": list(range(7))})
    rw.btn_run.click()
    wait_until(lambda: not rw.running, 60)
    check(not any(x.status.startswith("ignorato: giorno") for x in rw.report.signals), "tutti i giorni attivi")

    # seconda analisi: i prezzi sono già salvati sul disco
    calls = ea.rates_calls
    t0 = time.time()
    rw.btn_run.click()
    wait_until(lambda: not rw.running, 60)
    second_time = time.time() - t0
    check(ea.rates_calls - calls <= 3, "seconda analisi usa i prezzi salvati", ea.rates_calls - calls)
    print(f"   (prima analisi {first_time:.1f}s, seconda {second_time:.1f}s)")

    # ponte non aggiornato: il Radar funziona lo stesso con il metodo base e lo dice
    ea.precise = False
    for f in (radar.CACHE_DIR).rglob("*.txt"):
        f.unlink()
    rw.btn_run.click()
    wait_until(lambda: not rw.running, 60)
    rep_old = rw.report
    check(rep_old and not rep_old.precise and rep_old.stats["signals"] == 10, "funziona anche con il ponte vecchio")
    check("aggiorna il ponte" in rw.lbl_progress.text(), "invito ad aggiornare il ponte", rw.lbl_progress.text())
    ea.precise = True

    # report ricaricato alla riapertura (anche senza rianalizzare)
    d.reject()
    d = gui.SettingsDialog(win, eng, "Radar")
    d.show()
    rw = d.radar
    check(rw.report is not None and rw.t_sig.rowCount() == 10 and rw.nav.count() == 2,
          "risultati salvati e mostrati alla riapertura")

    # grafico di un segnale
    d.resize(1240, 840)
    QTest.qWait(100)
    d.grab().save(str(TMP / "radar.png"))
    rw._tab(1)
    QTest.qWait(50)
    d.grab().save(str(TMP / "radar_tabella.png"))
    rw._tab(0)
    sd = gui.ChannelSettingsDialog(d, eng, CH, "Canale Radar")
    sd.items[0][1].setChecked(True)
    sd.show()
    sd.resize(640, 760)
    QTest.qWait(50)
    sd.grab().save(str(TMP / "canale_impostazioni.png"))
    sd.reject()
    d.nav.setCurrentRow(0)
    QTest.qWait(50)
    d.grab().save(str(TMP / "telegram.png"))
    d.nav.setCurrentRow(d.PAGES.index("Radar"))
    mini = next(m for m in (c.findChild(radar_ui.MiniChart) for c in rw.feed_cards()) if m is not None)
    QTest.mouseClick(mini, Qt.LeftButton)
    dlg = next(c for c in rw.children() if isinstance(c, radar_ui.RadarTradeChart))
    ok = wait_until(lambda: len(dlg.chart.bars) > 10, 15)
    check(ok and dlg.chart.trade.entry_time and dlg.chart.trade.exit_time, "grafico del segnale simulato")
    dlg.resize(1100, 700)
    QTest.qWait(100)
    dlg.grab().save(str(TMP / "radar_grafico.png"))

    # MetaTrader scollegato: messaggio chiaro, nessun errore
    real = eng.mt5_connected
    eng.mt5_connected = lambda: False
    rw.btn_run.click()
    wait_until(lambda: not rw.running, 10)
    check("MetaTrader non è collegato" in rw.lbl_progress.text(), "messaggio se MetaTrader è chiuso")
    eng.mt5_connected = real

    # nessun canale nel Radar
    eng.settings.update({"radar_channels": []})
    rw.reload_channels()
    check(rw.empty.isVisible() and "Radar" in rw.empty.text(), "spiegazione se il Radar è vuoto")
    print(f"TUTTI I {len(PASSED)} TEST DEL RADAR (FINESTRA) SUPERATI")
    print("screenshot:", TMP)
    os._exit(0)


if __name__ == "__main__":
    main()
