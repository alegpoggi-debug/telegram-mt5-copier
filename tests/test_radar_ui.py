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
        for cid, n in ((CH, "Canale Radar"), (-1009, "Canale Copia")):
            yield types.SimpleNamespace(id=cid, name=n, is_channel=True, is_group=False, entity=None)

    async def iter_messages(self, cid, limit=None):
        if not self.cache_ok:
            raise ValueError("Could not find the input entity")   # come Telethon se il canale non è in memoria
        for m in reversed(MESSAGES):       # dal più recente
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

    def run(self):
        while True:
            for f in sorted(self.dir.glob("cmd_*.txt")):
                kv = _parse_kv(f.read_text())
                f.unlink()
                cid = f.name[4:-4]
                now_server = int(time.time()) + OFFSET
                res = {"ok": 0, "msg": "?", "price": now_server}
                if kv["action"] == "RATES":
                    self.rates_calls += 1
                    sec = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}[kv["tf"]]
                    start = int(kv["from"]) // sec * sec
                    stop = min(int(kv["to"]) or now_server, now_server)
                    lines, t = [], start
                    while t <= stop:
                        if dt.datetime.fromtimestamp(t, dt.timezone.utc).weekday() < 5 or True:
                            o, c = price(t), price(t + sec)
                            lines.append(f"{t}|{o:.2f}|{max(o, c) + .3:.2f}|{min(o, c) - .3:.2f}|{c:.2f}")
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
    eng.settings.update({"mt5_dir": str(TMP / "Common/Files"), "tg_api_id": "123", "tg_api_hash": "h" * 32})
    win = gui.MainWindow(ui, eng)
    win.show()
    eng.start()
    wait_until(lambda: eng.mt5_connected() and eng.tg_authorized, 15)
    check(eng.mt5_connected() and eng.tg_authorized, "Telegram e MetaTrader simulati collegati")

    # ---------------------------------------------------- interruttore Radar accanto ai canali
    d = gui.SettingsDialog(win, eng, "Telegram")
    d.show()
    wait_until(lambda: len(d.rows) == 2, 5)
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

    # ---------------------------------------------------- finestra Radar e analisi
    d.open_radar()
    rw = radar_ui.RadarWindow._instance
    check(rw is not None and rw.isVisible() and rw.nav.count() == 1, "Apri Radar mostra il canale")
    check("Aggiorna" in rw.lbl_progress.text(), "invito ad analizzare")
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
    check(rw.t_sig.rowCount() == 10 and rw.t_msg.rowCount() == len(MESSAGES), "tabelle segnali e messaggi",
          (rw.t_sig.rowCount(), rw.t_msg.rowCount()))
    check(all(p.isVisible() for p in rw.pills) and "punti" in rw.pills[2].text(), "riepilogo in alto")
    check("punti" in rw.nav.item(0).text(), "classifica a sinistra aggiornata", rw.nav.item(0).text())
    notes = [rw.t_msg.item(r, 3).text() for r in range(rw.t_msg.rowCount())]
    check(notes.count("doppio: ignorato") == 4 and "gestione" in notes, "nota dei messaggi", notes)
    # coerenza: i punti in alto sono la somma dei segnali
    check(abs(sum(s.points for s in rep.signals if s.status in ("vinto", "perso", "pareggio", "aperto")) - st["points"]) < 0.5,
          "totale coerente")

    # seconda analisi: i prezzi sono già salvati sul disco
    calls = ea.rates_calls
    t0 = time.time()
    rw.btn_run.click()
    wait_until(lambda: not rw.running, 60)
    second_time = time.time() - t0
    check(ea.rates_calls - calls <= 3, "seconda analisi usa i prezzi salvati", ea.rates_calls - calls)
    print(f"   (prima analisi {first_time:.1f}s, seconda {second_time:.1f}s)")

    # report ricaricato alla riapertura (anche senza rianalizzare)
    rw.close()
    radar_ui.RadarWindow._instance = None
    rw = radar_ui.RadarWindow.show_for(win, eng)
    check(rw.report is not None and rw.t_sig.rowCount() == 10, "risultati salvati e mostrati alla riapertura")

    # grafico di un segnale
    rw.resize(1200, 800)
    QTest.qWait(100)
    rw.grab().save(str(TMP / "radar.png"))
    rw._tab(1)
    QTest.qWait(50)
    rw.grab().save(str(TMP / "radar_messaggi.png"))
    rw._tab(0)
    row = next(r for r in range(rw.t_sig.rowCount()) if rw.t_sig.item(r, 6).text() in ("vinto", "perso"))
    rw.t_sig.cellDoubleClicked.emit(row, 0)
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
