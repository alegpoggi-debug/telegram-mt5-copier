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
# ultimi messaggi di ogni chat: «Forex Pro» non manda segnali
CHAT_TEXTS = {CHATS[0][0]: ["Buongiorno a tutti", "XAUUSD BUY 2350 SL 2340 TP 2360"],
              CHATS[1][0]: ["Ciao", "Oggi mercato lento", "Analisi della settimana"],
              CHATS[2][0]: ["SELL EURUSD 1.0850 SL 1.0880 TP 1.0800"]}


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

    async def iter_messages(self, cid, limit=None, **k):
        for i, t in enumerate(CHAT_TEXTS.get(cid, [])[:limit]):
            yield types.SimpleNamespace(id=i + 1, message=t)

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
    eng.settings.update({"days": list(range(7))})   # le prove devono funzionare anche nel weekend
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
    check(not d.dup_wrap.isHidden(), "protezione doppi accesa di base")
    d.sp_dup.setValue(5)
    d.save()
    check(eng.settings["ignore_duplicates"] is True and eng.settings["duplicate_window_min"] == 5, "impostazione doppi salvata")
    d = gui.SettingsDialog(win, eng, "Trading")
    check(d.sw_follow_be.isChecked() == eng.settings["follow_breakeven"] and d.sp_be_off.value() == eng.settings["be_offset_points"],
          "pareggio del copiatore: valori attuali mostrati")
    d.cb_tphit.setCurrentIndex(d.cb_tphit.findData("nothing"))
    check(d.tphit_wrap.isHidden(), "percentuale al «TP raggiunto» nascosta se non si chiude una parte")
    d.cb_tphit.setCurrentIndex(d.cb_tphit.findData("partial_be"))
    check(not d.tphit_wrap.isHidden() and d.sp_tphit_pct.value() == eng.settings["tp_hit_partial_percent"],
          "percentuale visibile con «Chiudi una parte»")
    d.sp_tphit_pct.setValue(70)
    d.sw_follow_be.setChecked(False)
    d.sp_be_after.setValue(200)
    d.sp_be_off.setValue(15)
    d.save()
    check((eng.settings["follow_breakeven"], eng.settings["be_after_points"], eng.settings["be_offset_points"]) == (False, 200, 15),
          "pareggio del copiatore salvato")
    check(eng.settings["tp_hit_action"] == "partial_be" and eng.settings["tp_hit_partial_percent"] == 70,
          "percentuale al «TP raggiunto» salvata", (eng.settings["tp_hit_action"], eng.settings["tp_hit_partial_percent"]))
    eng.settings.update({"tp_hit_action": "breakeven"})
    s2 = eng.settings.get()
    check((s2["zone_wait"], s2["zone_wait_seconds"], s2["zone_margin_points"], s2["max_spread_points"],
           s2["slippage_points"]) == (True, 20, 15, 40, 30), "impostazioni ENTRATA salvate")
    check(not hasattr(d, "sp_slip"), "slippage tolto dalle impostazioni")
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

    # voce «Radar» nel menu delle impostazioni: si apre a destra, come le altre pagine
    from app import radar_ui
    d = gui.SettingsDialog(win, eng, "Trading")
    d.show()
    n_top = len([w for w in QApplication.topLevelWidgets() if w.isVisible()])
    d.nav.setCurrentRow(d.nav.count() - 1)
    QTest.qWait(100)
    check(d.nav.item(d.nav.count() - 1).text() == "Radar" and d.stack.currentWidget() is d.radar
          and isinstance(d.radar, radar_ui.RadarPanel) and d.radar.isVisible(), "voce Radar: pagina a destra")
    check(len([w for w in QApplication.topLevelWidgets() if w.isVisible()]) == n_top, "nessuna finestra in più")
    scr_w = QApplication.primaryScreen().availableGeometry().width()
    check(d.width() >= min(1240, int(scr_w * 0.95)), "la finestra si allarga per il Radar (senza uscire dallo schermo)",
          (d.width(), scr_w))
    d.nav.setCurrentRow(2)
    check(d.stack.currentIndex() == 2, "si torna alle altre pagine")
    d.reject()
    d = gui.SettingsDialog(win, eng, "Radar")
    check(d.stack.currentWidget() is d.radar, "si può aprire direttamente sul Radar")
    d.reject()
    d = gui.SettingsDialog(win, eng, "Telegram")
    d.show()
    d.open_radar()
    check(d.stack.currentWidget() is d.radar and d.nav.currentRow() == d.PAGES.index("Radar"),
          "«Apri Radar» porta alla pagina Radar")
    d.reject()

    # ---------------------------------------------------- 3b) impostazioni del singolo canale
    general_before = eng.settings.get()
    d = gui.SettingsDialog(win, eng, "Telegram")
    d.show()
    QTest.qWait(200)
    row = next(r for r in d.rows if r.cid == CHATS[0][0])
    check(not row.custom, "di base il canale usa le impostazioni generali")
    cd = gui.ChannelSettingsDialog(d, eng, row.cid, row.name, d.current_values())
    check(all(not sw.isChecked() and not box.isEnabled() for _k, sw, box, *_ in cd.items),
          "tutte le voci sono «generali» e bloccate")
    check(cd.values() == {}, "nessuna personalizzazione all'inizio")
    lot_item = next(it for it in cd.items if "lot_mode" in it[0])
    be_item = next(it for it in cd.items if it[0] == ["be_offset_points"])
    tp1_item = next(it for it in cd.items if it[0] == ["be_on_tp1"])
    check(lot_item[2].findChildren(gui.QDoubleSpinBox)[0].value() == general_before["fixed_lot"],
          "mostra il valore generale", lot_item[2].findChildren(gui.QDoubleSpinBox)[0].value())
    lot_item[1].setChecked(True)
    check(lot_item[2].isEnabled(), "personalizza: la voce si sblocca")
    lot_item[2].findChildren(gui.QDoubleSpinBox)[0].setValue(0.25)
    be_item[1].setChecked(True)
    be_item[2].findChildren(gui.QSpinBox)[0].setValue(33)
    tp1_item[1].setChecked(True)
    tp1_item[2].findChildren(gui.Switch)[0].setChecked(False)
    cd.save()
    over = eng.settings["channel_overrides"]
    check(over == {str(row.cid): {"lot_mode": "fixed", "fixed_lot": 0.25, "be_offset_points": 33, "be_on_tp1": False}},
          "salvate solo le voci personalizzate", over)
    d.s["channel_overrides"] = over
    row.set_custom(True)
    after = eng.settings.get()
    changed = {k for k in general_before if general_before[k] != after[k]}
    check(changed == {"channel_overrides"}, "le impostazioni generali non cambiano", changed)
    eff = eng.settings_for(row.cid)
    check(eff["fixed_lot"] == 0.25 and eff["be_offset_points"] == 33 and eff["be_on_tp1"] is False,
          "il canale usa le sue impostazioni")
    other = eng.settings_for(CHATS[1][0])
    check(other["fixed_lot"] == general_before["fixed_lot"] and other["be_on_tp1"] == general_before["be_on_tp1"],
          "gli altri canali usano le generali")
    # riapro: le scelte si vedono
    cd = gui.ChannelSettingsDialog(d, eng, row.cid, row.name, d.current_values())
    lot_item = next(it for it in cd.items if "lot_mode" in it[0])
    check(lot_item[1].isChecked() and lot_item[2].findChildren(gui.QDoubleSpinBox)[0].value() == 0.25,
          "riaprendo si vede la personalizzazione")
    lot_item[1].setChecked(False)
    check(lot_item[2].findChildren(gui.QDoubleSpinBox)[0].value() == general_before["fixed_lot"],
          "togliendo «personalizza» torna il valore generale")
    cd.reject()
    check(eng.settings["channel_overrides"][str(row.cid)]["fixed_lot"] == 0.25, "annulla non cambia niente")
    # apri e salva senza toccare: identico
    cd = gui.ChannelSettingsDialog(d, eng, row.cid, row.name, d.current_values())
    cd.save()
    check(eng.settings["channel_overrides"] == over, "aprire e salvare il canale non cambia niente")
    # «usa tutte le generali»
    cd = gui.ChannelSettingsDialog(d, eng, CHATS[2][0], CHATS[2][1], d.current_values())
    next(it for it in cd.items if it[0] == ["tp_hit_action"])[1].setChecked(True)
    cd.save()
    check(str(CHATS[2][0]) in eng.settings["channel_overrides"], "secondo canale personalizzato")
    cd = gui.ChannelSettingsDialog(d, eng, CHATS[2][0], CHATS[2][1], d.current_values())
    cd.reset()
    cd.save()
    check(str(CHATS[2][0]) not in eng.settings["channel_overrides"] and
          str(CHATS[0][0]) in eng.settings["channel_overrides"], "«usa tutte le generali» toglie solo quel canale")
    # salvare le impostazioni generali non cancella quelle dei canali
    d.save()
    check(eng.settings["channel_overrides"] == over, "salvare le generali conserva quelle dei canali")
    d = gui.SettingsDialog(win, eng, "Telegram")
    d.show()
    QTest.qWait(200)
    check({r.cid: r.custom for r in d.rows} == {CHATS[0][0]: True, CHATS[1][0]: False, CHATS[2][0]: False},
          "⚙ blu sul canale personalizzato", [(r.name, r.custom) for r in d.rows])

    def visible():
        return [next(r for r in d.rows if r.cid == c).isVisible() for c, _ in CHATS]

    d.reject()

    # ---------------------------------------------------- 3c) cestino accanto al nome
    VIP, FX, AMICI = CHATS[0][0], CHATS[1][0], CHATS[2][0]
    check(not hasattr(d, "btn_signals") and not hasattr(d, "btn_edit_list") and not hasattr(eng, "tg_signal_chats"),
          "tolta la funzione «solo canali con segnali»")
    d = gui.SettingsDialog(win, eng, "Telegram")
    d.show()
    QTest.qWait(200)

    def r_(cid):
        return next(r for r in d.rows if r.cid == cid)

    def vis():
        return {c: r_(c).isVisible() for c, _ in CHATS}
    check(all(r.trash.isVisible() for r in d.rows) and not d.btn_restore.isVisible(), "cestino su ogni canale")
    row_fx = r_(FX)
    check(row_fx.trash.geometry().left() - row_fx.lbl.geometry().right() < 30, "cestino di fianco al nome",
          (row_fx.lbl.geometry(), row_fx.trash.geometry()))
    for r in d.rows:
        r.switch.setChecked(r.cid == VIP)
        r.radar.setChecked(False)
    copy_before = eng.settings["tg_channels"]
    ANSWER["q"] = QMessageBox.No             # canale non in Copia: nessuna domanda, si toglie subito
    QTest.mouseClick(r_(FX).trash, Qt.LeftButton)
    ANSWER["q"] = QMessageBox.Yes
    check(vis() == {VIP: True, FX: False, AMICI: True} and eng.settings["removed_chats"] == [FX], "canale tolto", vis())
    check(eng.settings["tg_channels"] == copy_before, "il cestino non tocca la copia degli altri canali", (copy_before, eng.settings["tg_channels"]))
    check(d.btn_restore.isVisible() and "(1)" in d.btn_restore.text(), "«Ripristina tolti (1)»", d.btn_restore.text())
    # canale in Copia: chiede conferma
    r_(AMICI).switch.setChecked(True)
    ANSWER["q"] = QMessageBox.No
    QTest.mouseClick(r_(AMICI).trash, Qt.LeftButton)
    check(r_(AMICI).isVisible() and r_(AMICI).isChecked(), "canale in Copia: con «No» resta")
    ANSWER["q"] = QMessageBox.Yes
    QTest.mouseClick(r_(AMICI).trash, Qt.LeftButton)
    check(not r_(AMICI).isVisible() and not r_(AMICI).isChecked() and
          all(c["id"] != AMICI for c in eng.settings["tg_channels"]), "con «Sì» tolto e spenta la copia")
    d.ed_filter.setText("o")
    check(vis() == {VIP: True, FX: False, AMICI: False}, "la ricerca non fa ricomparire i tolti", vis())
    d.ed_filter.setText("")
    d.reject()
    d = gui.SettingsDialog(win, eng, "Telegram")
    d.show()
    QTest.qWait(200)
    d.load_channels()                     # anche «Aggiorna elenco» non li rimette
    check(vis() == {VIP: True, FX: False, AMICI: False}, "riaprendo restano tolti", vis())
    d.resize(940, 660)
    QTest.qWait(50)
    d.grab().save(str(TMP / "cestino.png"))
    for head, sw in zip(d.col_heads, (r_(VIP).radar, r_(VIP).switch)):
        need = head.fontMetrics().horizontalAdvance(head.text()) + 6
        hc = head.mapTo(d, head.rect().center()).x()
        sc = sw.mapTo(d, sw.rect().center()).x()
        check(need <= head.width() and abs(hc - sc) <= 2, f"intestazione {head.text()} intera e centrata",
              (need, head.width(), hc, sc))
    d.btn_restore.click()
    check(all(vis().values()) and eng.settings["removed_chats"] == [] and not d.btn_restore.isVisible(),
          "«Ripristina» li rimette tutti")
    for r in d.rows:
        r.switch.setChecked(r.cid == AMICI)        # come prima: solo «Gruppo Amici» in Copia
    before = eng.settings.get()
    d.save()
    check({k for k in before if before[k] != eng.settings.get()[k]} == set(), "salvare non cambia niente")

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

    # ---------------------------------------------------- 5b) tutto in un'unica finestra, con «← Indietro»
    tops = lambda: [w for w in QApplication.topLevelWidgets() if w.isVisible()]  # noqa: E731
    n_top = len(tops())
    v = win.open_settings("Trading")
    QTest.qWait(100)
    check(win.pages.currentWidget() is v and v.isVisible() and len(tops()) == n_top,
          "Impostazioni dentro la finestra dell'app, nessuna finestra nuova")
    check(v.btn_back.isVisible() and v.btn_back.text().startswith("←"), "freccia «← Indietro»")
    check(win.open_settings("Radar") is v and v.stack.currentWidget() is v.radar, "si riapre la stessa pagina")
    v.btn_back.click()
    QTest.qWait(100)
    check(win.pages.currentWidget() is win.home and win.settings_view is None, "«← Indietro» torna alla schermata principale")
    # modifiche non salvate: chiede cosa fare
    old_max = eng.settings["max_open_trades"]
    v = win.open_settings("Trading")
    v.sp_maxpos.setValue(old_max + 3)
    ANSWER["q"] = QMessageBox.Cancel
    v.btn_back.click()
    check(win.pages.currentWidget() is v, "«Annulla»: resta nelle impostazioni")
    ANSWER["q"] = QMessageBox.Discard
    v.btn_back.click()
    QTest.qWait(50)
    check(win.pages.currentWidget() is win.home and eng.settings["max_open_trades"] == old_max, "«Non salvare»: niente cambia")
    v = win.open_settings("Trading")
    v.sp_maxpos.setValue(old_max + 3)
    ANSWER["q"] = QMessageBox.Save
    v.btn_back.click()
    QTest.qWait(50)
    check(win.pages.currentWidget() is win.home and eng.settings["max_open_trades"] == old_max + 3, "«Salva»: salvato e torna indietro")
    v = win.open_settings("Trading")
    v.sp_maxpos.setValue(old_max)
    ANSWER["q"] = QMessageBox.Yes
    v.save()
    QTest.qWait(50)
    check(win.pages.currentWidget() is win.home and eng.settings["max_open_trades"] == old_max, "«Salva» in basso torna indietro")
    v = win.open_settings("Telegram")
    ANSWER["q"] = QMessageBox.Cancel
    v.btn_back.click()
    QTest.qWait(50)
    check(win.pages.currentWidget() is win.home, "senza modifiche torna indietro senza domande")
    ANSWER["q"] = QMessageBox.Yes

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
