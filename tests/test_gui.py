"""Test dell'interfaccia (senza schermo, Telegram simulato).   QT_QPA_PLATFORM=offscreen python -m tests.test_gui"""
import asyncio
import json
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = Path(tempfile.mkdtemp())
os.environ["TGMT5_DATA"] = str(TMP / "data")
os.environ["HOME"] = str(TMP / "home")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import telethon  # noqa: E402

CHATS = [(-1001234567890123, "Segnali VIP & Gold"), (-1009876543210, "Forex Pro"), (-4123456789, "Gruppo Amici")]


class FakeClient:
    def __init__(self, *a, **k):
        self.connected, self.auth, self.handlers = False, False, []

    async def connect(self):
        self.connected = True

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.auth

    async def send_code_request(self, phone):
        self.phone = phone

    async def sign_in(self, phone=None, code=None, password=None):
        self.auth = True

    async def get_me(self):
        return types.SimpleNamespace(first_name="Ale", username="ale")

    def add_event_handler(self, cb, ev):
        self.handlers.append(cb)

    async def iter_dialogs(self):
        for cid, name in CHATS:
            yield types.SimpleNamespace(id=cid, name=name, is_channel=True, is_group=False, entity=None)

    async def disconnect(self):
        self.connected = False

    async def log_out(self):
        self.auth = False


telethon.TelegramClient = FakeClient

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from app import gui  # noqa: E402
from app.engine import Engine  # noqa: E402

MSGS = []
QMessageBox.information = staticmethod(lambda *a, **k: MSGS.append(("info", a[2] if len(a) > 2 else "")))
QMessageBox.warning = staticmethod(lambda *a, **k: MSGS.append(("warn", a[2] if len(a) > 2 else "")))
QMessageBox.critical = staticmethod(lambda *a, **k: MSGS.append(("crit", a[2] if len(a) > 2 else "")))
ANSWER = {"q": QMessageBox.Yes}
QMessageBox.question = staticmethod(lambda *a, **k: ANSWER["q"])
INPUTS = []
QInputDialog.getText = staticmethod(lambda *a, **k: (INPUTS.pop(0), True))

PASSED = []


def check(cond, name, detail=""):
    if not cond:
        raise AssertionError(f"FALLITO: {name} {detail}")
    PASSED.append(name)


def main():
    app = QApplication([])
    ui = gui.UiBridge()
    eng = Engine(ui)
    win = gui.MainWindow(ui, eng)
    win.show()
    eng.start()
    QTest.qWait(1500)

    # ---------------------------------------------------- 1) apri e salva senza toccare niente: tutto identico
    custom = {"lot_mode": "risk_money", "fixed_lot": 0.03, "risk_percent": 1.5, "risk_money": 25.0,
              "multi_tp_mode": "index", "tp_index": 2, "no_sl_mode": "default", "default_sl_points": 450,
              "tp_hit_action": "partial_be", "hours_enabled": True, "hours_start": "09:15", "hours_end": "21:45",
              "dry_run": False, "allow_real": True, "max_lot_per_trade": 0.2, "max_open_trades": 4,
              "be_on_tp1": False, "max_total_lots": 0.5, "max_trades_per_symbol": 3, "lot_split": "50,30,20",
              "kw_buy": "BUY, COMPRA", "trailing_points": 120, "symbol_aliases": "GOLD=XAUUSD"}
    eng.settings.update(custom)
    before = eng.settings.get()
    d = gui.SettingsDialog(win, eng, "Trading")
    d.show()
    QTest.qWait(100)
    d.save()
    after = eng.settings.get()
    diff = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    check(not diff, "aprire e salvare non cambia nessun valore", diff)

    # ---------------------------------------------------- 2) ogni comando della pagina Trading salva la chiave giusta
    d = gui.SettingsDialog(win, eng, "Trading")
    d.cb_lot_mode.setCurrentIndex(d.cb_lot_mode.findData("fixed"))
    check(abs(d.sp_lot.value() - 0.03) < 1e-9, "lotto fisso mostrato correttamente", d.sp_lot.value())
    d.sp_lot.setValue(0.07)
    d.cb_lot_mode.setCurrentIndex(d.cb_lot_mode.findData("risk_percent"))
    check(abs(d.sp_lot.value() - 1.5) < 1e-9 and d.sp_lot.suffix() == " %", "cambio modalità mostra il rischio %")
    d.sp_lot.setValue(2.0)
    d.cb_lot_mode.setCurrentIndex(d.cb_lot_mode.findData("fixed"))
    d.sw_dry.setChecked(True)
    d.sw_real.setChecked(False)
    d.sp_maxlot.setValue(0.5)
    d.sp_maxpos.setValue(8)
    d.cb_tp.setCurrentIndex(d.cb_tp.findData("first"))
    d.cb_nosl.setCurrentIndex(d.cb_nosl.findData("ignore"))
    d.sw_be.setChecked(True)
    d.cb_tphit.setCurrentIndex(d.cb_tphit.findData("breakeven"))
    d.sw_hours.setChecked(False)
    check(d.zone_wrap.isHidden(), "opzioni della fascia nascoste se spenta")
    d.sw_zone.setChecked(True)
    check(not d.zone_wrap.isHidden(), "opzioni della fascia visibili se accesa")
    d.sp_zone_sec.setValue(20)
    d.sp_zone_margin.setValue(15)
    d.sp_spread.setValue(40)
    d.sp_slip.setValue(25)
    check(not d.dup_wrap.isHidden(), "protezione doppi accesa di base")
    d.sp_dup.setValue(5)
    d.save()
    check(eng.settings["ignore_duplicates"] is True and eng.settings["duplicate_window_min"] == 5, "impostazione doppi salvata")
    s2 = eng.settings.get()
    check((s2["zone_wait"], s2["zone_wait_seconds"], s2["zone_margin_points"], s2["max_spread_points"],
           s2["slippage_points"]) == (True, 20, 15, 40, 25), "impostazioni ENTRATA salvate")
    d = gui.SettingsDialog(win, eng, "Trading")
    s = eng.settings.get()
    exp = {"lot_mode": "fixed", "fixed_lot": 0.07, "risk_percent": 2.0, "risk_money": 25.0, "dry_run": True,
           "allow_real": False, "max_lot_per_trade": 0.5, "max_open_trades": 8, "multi_tp_mode": "first",
           "no_sl_mode": "ignore", "be_on_tp1": True, "tp_hit_action": "breakeven", "hours_enabled": False}
    bad = {k: (v, s[k]) for k, v in exp.items() if s[k] != v}
    check(not bad, "ogni impostazione Trading viene salvata", bad)
    check(s["max_total_lots"] == 4.0 and s["max_trades_per_symbol"] == 8, "limiti collegati aggiornati",
          (s["max_total_lots"], s["max_trades_per_symbol"]))
    check(s["lot_split"] == "50,30,20" and s["trailing_points"] == 120 and s["kw_buy"] == "BUY, COMPRA",
          "impostazioni nascoste conservate")

    # ---------------------------------------------------- 3) Telegram: accesso e scelta canali con un clic
    d = gui.SettingsDialog(win, eng, "Telegram")
    d.show()
    check(not d.login_box.isHidden(), "senza accesso si vedono i campi dei codici")
    d.ed_api_id.setText("1234567")
    d.ed_api_hash.setText("0123456789abcdef0123456789abcdef")
    INPUTS[:] = ["+39 333 1234567", "12345"]
    d.tg_login()
    QTest.qWait(200)
    check(eng.tg_authorized and d.login_box.isHidden() and "Ale" in d.lbl_tg_user.text(), "accesso a Telegram")
    check([r.name for r in d.rows] == [n for _, n in CHATS], "elenco canali caricato (anche nomi con &)",
          [r.name for r in d.rows])
    row = d.rows[0]
    QTest.mouseClick(row.lbl, Qt.LeftButton)          # clic sul NOME
    QTest.qWait(200)
    check(eng.settings["tg_channels"] == [{"id": CHATS[0][0], "name": CHATS[0][1]}], "clic sul nome attiva il canale",
          eng.settings["tg_channels"])
    check(CHATS[0][0] in eng.channels, "il programma ascolta subito il canale (id lungo)")
    QTest.mouseClick(d.rows[2].switch, Qt.LeftButton)  # clic sull'interruttore
    QTest.qWait(200)
    check([c["id"] for c in eng.settings["tg_channels"]] == [CHATS[0][0], CHATS[2][0]],
          "clic sull'interruttore attiva il canale una sola volta")
    QTest.mouseClick(row, Qt.LeftButton, pos=QPoint(5, 5))  # clic sul bordo della riga: disattiva
    QTest.qWait(200)
    check([c["id"] for c in eng.settings["tg_channels"]] == [CHATS[2][0]], "clic sulla riga disattiva il canale")
    st = eng.status_snapshot()
    check(st["telegram"] and st["channels"] == ["Gruppo Amici"], "pallino Telegram verde con canale scelto", st["channels"])
    d.ed_filter.setText("forex")
    check([r.isVisible() for r in d.rows] == [False, True, False], "ricerca canali")
    d.reject()

    # riapro: il canale scelto risulta attivo, senza doppioni
    d = gui.SettingsDialog(win, eng, "Telegram")
    d.show()
    QTest.qWait(200)
    check(len(d.rows) == 3 and [r.isChecked() for r in d.rows if r.cid == CHATS[2][0]] == [True],
          "riaprendo le impostazioni il canale è ancora attivo")

    # messaggio dal canale scelto arriva e viene letto
    n0 = len(win.log.toPlainText())
    msg = types.SimpleNamespace(id=7, message="BUY GOLD 2000 SL 1990 TP 2010", reply_to_msg_id=None)
    eng.call(eng.tg.handlers[0](types.SimpleNamespace(chat_id=CHATS[2][0], message=msg)))
    QTest.qWait(300)
    logtxt = win.log.toPlainText()[n0:]
    check("Gruppo Amici" in logtxt and "SEGNALE BUY" in logtxt and "PROVA" in logtxt, "messaggio ricevuto e letto", logtxt)

    # salvare senza canali avvisa
    for r in d.rows:
        if r.isChecked():
            r.switch.setChecked(False)
    ANSWER["q"] = QMessageBox.No
    d.save()
    check(d.isVisible() and d.nav.currentRow() == 0, "avviso se nessun canale è attivo")
    ANSWER["q"] = QMessageBox.Yes
    d.rows[2].switch.setChecked(True)
    d.save()

    # ---------------------------------------------------- 4) MetaTrader: installa il ponte
    experts = TMP / "home/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts"
    experts.mkdir(parents=True)
    common = TMP / "home/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
    common.mkdir(parents=True)
    d = gui.SettingsDialog(win, eng, "MetaTrader")
    d.install_bridge()
    check((experts / "TelegramBridge.mq5").exists(), "installa il ponte")
    check(eng.bridge is not None and "Ponte non attivo" in d.lbl_mt5_state.text(), "MetaTrader trovato, ponte non attivo")
    (eng.bridge.dir / "state.txt").write_text("account|999|0|5000.00|5100.00|EUR|4000.00|Demo\r\ntime|x\r\n")
    QTest.qWait(1300)
    d._refresh_mt5_state()
    check("Collegato" in d.lbl_mt5_state.text() and "999" in d.lbl_mt5_detail.text(), "ponte collegato", (d.lbl_mt5_state.text(), d.lbl_mt5_detail.text(), eng.status_snapshot()["mt5"], eng.bridge.state_age(), eng.state))
    d.reject()

    # ---------------------------------------------------- 5) prova messaggio
    d = gui.SettingsDialog(win, eng, "Prova un messaggio")
    d.test_in.setPlainText("XAUUSD BUY 2350\nSL 2340\nTP1 2355\nTP2 2360")
    d.run_test()
    check("SEGNALE BUY" in d.test_out.text() and "Verrebbe inviato" in d.test_out.text(), "prova un messaggio", d.test_out.text())
    d.reject()

    # ---------------------------------------------------- 6) finestra principale
    win.update_status(eng.status_snapshot())
    check("1 canale" in win.lbl_tg.text() and "MetaTrader" in win.lbl_mt5.text(), "stato in alto")
    check("Demo 999" in win.lbl_acc.text(), "conto in alto", win.lbl_acc.text())
    check(win.setup_box.isHidden(), "riquadro configurazione nascosto quando tutto è collegato")
    win.toggle_pause()
    check(eng.settings["paused"] and "pausa" in win.lbl_mode.text().lower(), "pausa")
    win.toggle_pause()
    check(not eng.settings["paused"], "riprendi")
    fake_pos = [{"ticket": 1, "symbol": "XAUUSD", "side": "buy", "volume": 0.01, "price_open": 2000.0, "sl": 1990.0,
                 "tp": 2010.0, "profit": 3.5, "magic": 770077, "comment": "TG#1", "price_current": 2003.5}]
    win.update_status(dict(eng.status_snapshot(), positions=fake_pos))
    check(win.table.rowCount() == 1 and not win.empty_lbl.isVisible() and win.table.item(0, 7).text() == "+3.50", "tabella operazioni")
    win.table.selectRow(0)
    check(win.selected_tickets() == [1], "selezione operazione")
    win.do_button("close", False, 100)
    check("→" in win.log.toPlainText(), "pulsanti operazioni")
    win.show_update({"version": "9.9", "notes": "Novità di prova"})
    check(win.update_box.isVisible() and "9.9" in win.update_lbl.text(), "avviso aggiornamento")

    # screenshot per controllo visivo
    win.update_status(dict(eng.status_snapshot(), positions=fake_pos, today_pl=12.4))
    win.resize(1240, 760)
    QTest.qWait(100)
    win.grab().save(str(TMP / "main.png"))
    for page in gui.SettingsDialog.PAGES:
        d = gui.SettingsDialog(win, eng, page)
        d.resize(940, 660)
        d.show()
        QTest.qWait(100)
        d.grab().save(str(TMP / f"set_{page.split()[0]}.png"))
        if page == "Trading":
            sc = d.stack.currentWidget()
            sc.verticalScrollBar().setValue(sc.verticalScrollBar().maximum() // 2 + 150)
            QTest.qWait(100)
            d.grab().save(str(TMP / "set_Trading_entrata.png"))
        d.reject()
    print(f"TUTTI I {len(PASSED)} TEST DELL'INTERFACCIA SUPERATI")
    print("screenshot:", TMP)
    json.dumps(eng.status_snapshot(), default=str)
    os._exit(0)


if __name__ == "__main__":
    main()
