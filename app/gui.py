"""Interfaccia grafica (PySide6): finestra principale + impostazioni."""
from __future__ import annotations

import html
import threading
import time

from PySide6.QtCore import QObject, Qt, QTime, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QTimeEdit, QVBoxLayout, QWidget,
)

from . import updater
from .bridge import find_common_files_dir
from .mt5_install import install_ea
from .settings import DEFAULTS, SCHEMA

COLORS = {"msg": "#9aa4b2", "ai": "#7cc4ff", "trade": "#ffd27c", "ok": "#7ee2a8",
          "warn": "#ffb36b", "error": "#ff7b7b", "info": "#c9d1d9"}

STYLE = """
QWidget { background: #0f1419; color: #e6edf3; font-size: 13px; }
QTextEdit, QPlainTextEdit, QLineEdit, QTableWidget, QListWidget, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit {
  background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 4px; }
QPushButton { background: #30363d; border: none; border-radius: 6px; padding: 7px 12px; font-weight: 600; }
QPushButton:hover { background: #3d444d; }
QPushButton#primary { background: #238636; } QPushButton#primary:hover { background: #2ea043; }
QPushButton#danger { background: #8e1519; } QPushButton#danger:hover { background: #b62324; }
QHeaderView::section { background: #21262d; border: none; padding: 4px; }
QTabWidget::pane { border: 1px solid #30363d; border-radius: 6px; }
QTabBar::tab { background: #161b22; padding: 7px 12px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { background: #30363d; }
QLabel#title { font-size: 12px; color: #8b949e; font-weight: 600; }
QLabel#help { font-size: 11px; color: #8b949e; }
QFrame#setup { background: #1c2330; border: 1px solid #2f81f7; border-radius: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; }
"""


class UiBridge(QObject):
    """Riceve gli eventi dal motore (altro thread) e li passa all'interfaccia."""
    log_sig = Signal(str, str)
    status_sig = Signal(dict)
    update_sig = Signal(dict)

    def on_log(self, text, level="info"):
        self.log_sig.emit(text, level)

    def on_status(self, st):
        self.status_sig.emit(st)


def _dot(ok, label):
    return f'<span style="color:{"#3fb950" if ok else "#f85149"}">●</span> {label}'


def _title(text):
    lbl = QLabel(text)
    lbl.setObjectName("title")
    return lbl


def _btn(text, slot, kind=None):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.clicked.connect(slot)
    return b


def _fit_to_screen(win, w, h):
    """Dimensione desiderata, ma mai più grande dello schermo del Mac."""
    scr = QApplication.primaryScreen()
    if scr is not None:
        g = scr.availableGeometry()
        w, h = min(w, int(g.width() * 0.95)), min(h, int(g.height() * 0.92))
    win.resize(w, h)


def _busy(fn):
    """Esegue fn con il cursore di attesa e mostra gli errori in modo comprensibile."""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        return fn()
    finally:
        QApplication.restoreOverrideCursor()


# ======================================================================= principale
class MainWindow(QMainWindow):
    def __init__(self, ui: UiBridge, engine):
        super().__init__()
        self.engine = engine
        self.last_status = {}
        self.setWindowTitle(f"Telegram → MT5 Copier  (versione {updater.local_version()})")
        self.ui = ui
        _fit_to_screen(self, 1280, 800)
        self.setStyleSheet(STYLE)

        # stato in alto
        self.lbl_tg, self.lbl_mt5, self.lbl_acc, self.lbl_pl, self.lbl_mode = (QLabel() for _ in range(5))
        self.btn_pause = _btn("⏸  Pausa", self.toggle_pause)
        top = QHBoxLayout()
        for w in (self.lbl_tg, self.lbl_mt5, self.lbl_acc, self.lbl_pl):
            top.addWidget(w)
            top.addSpacing(16)
        top.addStretch(1)
        top.addWidget(self.lbl_mode)
        top.addSpacing(10)
        top.addWidget(self.btn_pause)
        top.addWidget(_btn("🧪  Prova un messaggio", lambda: self.open_settings("Prova un messaggio")))
        top.addWidget(_btn("⚙  Impostazioni", lambda: self.open_settings(None), "primary"))

        # riquadro "configurazione iniziale"
        self.setup_box = QFrame()
        self.setup_box.setObjectName("setup")
        sb = QHBoxLayout(self.setup_box)
        self.setup_lbl = QLabel()
        self.setup_lbl.setTextFormat(Qt.RichText)
        self.setup_lbl.setWordWrap(True)
        sb.addWidget(self.setup_lbl, 1)
        sb.addWidget(_btn("Configura Telegram", lambda: self.open_settings("Telegram"), "primary"))
        sb.addWidget(_btn("Configura MetaTrader", lambda: self.open_settings("MetaTrader"), "primary"))

        # attività
        self.log = QTextEdit(readOnly=True)
        self.log.setFont(QFont("Menlo", 12))
        left = QVBoxLayout()
        left.addWidget(_title("ATTIVITÀ — messaggi ricevuti, come sono stati letti, operazioni eseguite"))
        left.addWidget(self.log)
        lw = QWidget()
        lw.setLayout(left)

        # posizioni
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["Ticket", "Simbolo", "Tipo", "Lotti", "Apertura", "SL", "TP", "Prezzo", "Profitto"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        row1 = QHBoxLayout()
        row1.addWidget(_title("Selezionate:"))
        row1.addWidget(_btn("Chiudi", lambda: self.do_button("close", True, 100)))
        row1.addWidget(_btn("Chiudi 50%", lambda: self.do_button("close", True, 50)))
        row1.addWidget(_btn("SL a pareggio", lambda: self.do_button("breakeven", True)))
        row1.addStretch(1)
        row2 = QHBoxLayout()
        row2.addWidget(_title("Tutte:"))
        row2.addWidget(_btn("SL a pareggio su tutte", lambda: self.do_button("breakeven", False)))
        row2.addWidget(_btn("Chiudi 50% di tutte", lambda: self.do_button("close", False, 50)))
        row2.addWidget(_btn("CHIUDI TUTTO", lambda: self.do_button("close", False, 100), "danger"))
        row2.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(_title("OPERAZIONI APERTE DAL PROGRAMMA (seleziona le righe per agire solo su quelle)"))
        right.addWidget(self.table, 1)
        right.addLayout(row1)
        right.addLayout(row2)
        rw = QWidget()
        rw.setLayout(right)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(lw)
        split.addWidget(rw)
        split.setSizes([600, 680])

        root = QVBoxLayout()
        root.addLayout(top)
        root.addWidget(self.setup_box)
        # riquadro "aggiornamento disponibile"
        self.update_box = QFrame()
        self.update_box.setObjectName("setup")
        ub = QHBoxLayout(self.update_box)
        self.update_lbl = QLabel()
        self.update_lbl.setWordWrap(True)
        ub.addWidget(self.update_lbl, 1)
        ub.addWidget(_btn("🔄  Aggiorna ora", self.do_update, "primary"))
        self.update_box.hide()
        root.addWidget(self.update_box)
        root.addWidget(split, 1)
        cw = QWidget()
        cw.setLayout(root)
        self.setCentralWidget(cw)

        ui.log_sig.connect(self.add_log)
        ui.status_sig.connect(self.update_status)
        ui.update_sig.connect(self.show_update)
        threading.Thread(target=self._update_loop, daemon=True).start()
        self.update_status({})

    # ------------------------------------------------------------------ slot
    def add_log(self, text, level):
        safe = html.escape(text).replace("\n", "<br>")
        self.log.append(f'<span style="color:{COLORS.get(level, "#c9d1d9")}">{safe}</span>')

    def update_status(self, st):
        self.last_status = st
        tg_ok, mt_ok = st.get("telegram"), st.get("mt5")
        if tg_ok:
            self.lbl_tg.setText(_dot(True, f"Telegram ({len(st.get('channels', []))} canali)"))
        elif st.get("telegram_authorized"):
            self.lbl_tg.setText('<span style="color:#d29922">●</span> Telegram (non collegato)')
        else:
            self.lbl_tg.setText(_dot(False, "Telegram"))
        self.lbl_mt5.setText(_dot(mt_ok, "MetaTrader 5"))
        acc = st.get("account")
        if acc and mt_ok:
            kind = {0: "DEMO", 1: "CONTEST", 2: "REALE"}.get(acc["trade_mode"], "?")
            self.lbl_acc.setText(f"Conto {acc['login']} ({kind}) · {acc['equity']:.2f} {acc['currency']}")
            self.lbl_acc.setToolTip(f"Saldo {acc['balance']:.2f} · Equity {acc['equity']:.2f} {acc['currency']}")
        else:
            self.lbl_acc.setText("Conto: —")
        pl = st.get("today_pl")
        self.lbl_pl.setText("" if pl is None else f'Oggi: <span style="color:{"#3fb950" if pl >= 0 else "#f85149"}">{pl:+.2f}</span>')
        if st.get("paused"):
            mode = '<span style="color:#ff7b7b">⏸ IN PAUSA</span>'
        elif st.get("dry_run", True):
            mode = '<span style="color:#ffb36b">MODALITÀ PROVA</span>'
        else:
            mode = '<span style="color:#3fb950">● ATTIVO</span>'
        self.lbl_mode.setText(mode)
        self.btn_pause.setText("▶  Riprendi" if st.get("paused") else "⏸  Pausa")

        steps = [
            (st.get("telegram_authorized"), "Accedi a Telegram"),
            (bool(st.get("channels")), "Scegli i canali"),
            (st.get("mt5_found"), "Trova MetaTrader"),
            (mt_ok, "Avvia il ponte in MetaTrader"),
        ]
        if all(ok for ok, _ in steps):
            self.setup_box.hide()
        else:
            self.setup_box.show()
            parts = [f'{"✅" if ok else "⬜"} {i + 1}. {t}' for i, (ok, t) in enumerate(steps)]
            self.setup_lbl.setText("<b>Configurazione:</b>&nbsp;&nbsp;" + "&nbsp;&nbsp;&nbsp;".join(parts))

        rows = st.get("positions", []) + [dict(o, side=o["type"].replace("ORDER_TYPE_", "").lower(), price_open=o["price"],
                                                price_current=0, profit=0) for o in st.get("orders", [])]
        selected = {self.table.item(i.row(), 0).text() for i in self.table.selectionModel().selectedRows()
                    if self.table.item(i.row(), 0)}
        self.table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            vals = [p["ticket"], p["symbol"], p["side"], p["volume"], p["price_open"], p["sl"] or "-",
                    p["tp"] or "-", p["price_current"] or "-", f"{p['profit']:.2f}"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 8:
                    item.setForeground(Qt.green if p["profit"] >= 0 else Qt.red)
                self.table.setItem(r, c, item)
            if str(p["ticket"]) in selected:
                self.table.selectRow(r)

    def selected_tickets(self):
        return sorted({int(self.table.item(i.row(), 0).text()) for i in self.table.selectionModel().selectedRows()})

    def do_button(self, action, only_selected, percent=100):
        tickets = self.selected_tickets() if only_selected else None
        if only_selected and not tickets:
            QMessageBox.information(self, "Nessuna selezione", "Seleziona prima una o più righe nella tabella.")
            return
        if not only_selected and action == "close" and percent >= 100:
            if QMessageBox.question(self, "Conferma", "Chiudere TUTTE le posizioni e cancellare gli ordini pendenti?") != QMessageBox.Yes:
                return
        try:
            res = _busy(lambda: self.engine.call(self.engine.button(action, tickets, percent)))
        except Exception as e:
            res = [f"errore: {e}"]
        for r in res:
            self.add_log(f"  → {r}", "trade")

    # ------------------------------------------------------------ aggiornamenti
    def _update_loop(self):
        time.sleep(5)
        while True:
            try:
                info = updater.check()
                if info:
                    self.ui.update_sig.emit(info)
            except Exception:
                pass  # senza internet o GitHub non raggiungibile: riprova più tardi
            time.sleep(6 * 3600)

    def show_update(self, info):
        self.pending_update = info
        notes = html.escape(info.get("notes") or "").replace("\n", "<br>")
        self.update_lbl.setText(f"<b>🔄 Aggiornamento disponibile: versione {info['version']}</b>"
                                f"{'<br>' + notes if notes else ''}<br>"
                                "<span style='color:#8b949e'>Impostazioni, canali e collegamenti restano invariati.</span>")
        self.update_box.show()

    def do_update(self):
        if QMessageBox.question(self, "Aggiorna",
                                "Installare l'aggiornamento? L'app si riavvierà da sola in pochi secondi.\n"
                                "Le operazioni aperte restano aperte su MetaTrader.") != QMessageBox.Yes:
            return
        try:
            version = _busy(updater.apply)
        except Exception as e:
            QMessageBox.critical(self, "Aggiornamento", f"Aggiornamento non riuscito, l'app resta com'era:\n{e}")
            return
        QMessageBox.information(self, "Aggiornamento", f"Aggiornato alla versione {version}. L'app ora si riavvia.")
        updater.restart()
        QApplication.quit()

    def toggle_pause(self):
        new = not self.engine.settings["paused"]
        self.engine.settings.update({"paused": new})
        self.add_log("Programma in PAUSA: i nuovi segnali vengono ignorati (la gestione continua)." if new
                     else "Programma RIPRESO.", "warn" if new else "ok")
        self.update_status(dict(self.last_status, paused=new))

    def open_settings(self, tab):
        dlg = SettingsDialog(self, self.engine, tab)
        dlg.exec()

    def closeEvent(self, e):
        try:
            if self.engine.tg is not None:
                self.engine.call(self.engine.tg.disconnect(), timeout=3)
        except Exception:
            pass
        super().closeEvent(e)


# ======================================================================= impostazioni
class SettingsDialog(QDialog):
    def __init__(self, parent, engine, start_tab=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Impostazioni")
        _fit_to_screen(self, 1080, 760)
        self.setStyleSheet(STYLE)
        self.widgets = {}
        self.s = engine.settings.get()

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_telegram(), "Telegram")
        self.tabs.addTab(self._tab_mt5(), "MetaTrader")
        for name, fields in SCHEMA.items():
            self.tabs.addTab(self._tab_schema(name, fields), name)
        self.tabs.addTab(self._tab_test(), "Prova un messaggio")
        if start_tab:
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == start_tab:
                    self.tabs.setCurrentIndex(i)

        bottom = QHBoxLayout()
        bottom.addWidget(_btn("Ripristina predefiniti di questa scheda", self.reset_tab))
        bottom.addStretch(1)
        bottom.addWidget(_btn("Annulla", self.reject))
        bottom.addWidget(_btn("Salva", self.save, "primary"))
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs, 1)
        lay.addLayout(bottom)

    # ------------------------------------------------------------ Telegram
    def _tab_telegram(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        intro = QLabel(
            "<b>Passo 1 — Codici API (una volta sola, gratis)</b><br>"
            "Apri my.telegram.org → accedi con il tuo numero → <i>API development tools</i> → crea un'app "
            "(nome a piacere) → copia qui <b>api_id</b> e <b>api_hash</b>.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        lay.addWidget(_btn("🌐  Apri my.telegram.org", lambda: QDesktopServices.openUrl(QUrl("https://my.telegram.org/apps"))))
        form = QFormLayout()
        self.ed_api_id = QLineEdit(str(self.s["tg_api_id"]))
        self.ed_api_id.setPlaceholderText("es. 1234567")
        self.ed_api_hash = QLineEdit(self.s["tg_api_hash"])
        self.ed_api_hash.setPlaceholderText("es. 0123456789abcdef0123456789abcdef")
        form.addRow("api_id", self.ed_api_id)
        form.addRow("api_hash", self.ed_api_hash)
        lay.addLayout(form)

        lay.addWidget(QLabel("<b>Passo 2 — Accedi al tuo account</b> (ti arriva un codice nell'app Telegram)"))
        row = QHBoxLayout()
        self.lbl_tg_user = QLabel("Non collegato")
        row.addWidget(self.lbl_tg_user, 1)
        row.addWidget(_btn("Accedi a Telegram", self.tg_login, "primary"))
        row.addWidget(_btn("Esci", self.tg_logout))
        lay.addLayout(row)

        lay.addWidget(QLabel("<b>Passo 3 — Spunta i canali da copiare</b>"))
        row = QHBoxLayout()
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("Cerca canale…")
        self.ed_filter.textChanged.connect(self.filter_channels)
        row.addWidget(self.ed_filter, 1)
        row.addWidget(_btn("Carica i miei canali", self.load_channels))
        lay.addLayout(row)
        self.list_channels = QListWidget()
        lay.addWidget(self.list_channels, 1)
        hint = QLabel("I canali spuntati si salvano da soli e il programma inizia subito ad ascoltarli.")
        hint.setObjectName("help")
        lay.addWidget(hint)
        for c in self.s["tg_channels"]:
            self._add_channel_item(c["id"], c["name"], True)
        self.list_channels.itemChanged.connect(self._channels_changed)
        try:
            me = self.engine.call(self.engine.tg_me(), timeout=10) if self.engine.tg_authorized else None
        except Exception:
            me = None
        if me:
            self.lbl_tg_user.setText(f"✅ Collegato come {me}")
        return w

    def _add_channel_item(self, cid, name, checked):
        for i in range(self.list_channels.count()):
            if self.list_channels.item(i).data(Qt.UserRole) == cid:
                return
        it = QListWidgetItem(name)
        it.setData(Qt.UserRole, cid)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.list_channels.addItem(it)

    def _checked_channels(self):
        out = []
        for i in range(self.list_channels.count()):
            it = self.list_channels.item(i)
            if it.checkState() == Qt.Checked:
                out.append({"id": it.data(Qt.UserRole), "name": it.text()})
        return out

    def _channels_changed(self, _item):
        """Salva subito i canali spuntati e li mette in ascolto."""
        chans = self._checked_channels()
        self.engine.settings.update({"tg_channels": chans})
        self.s["tg_channels"] = chans
        try:
            self.engine.call(self.engine.reload_channels(), timeout=30)
        except Exception:
            pass

    def filter_channels(self, text):
        for i in range(self.list_channels.count()):
            it = self.list_channels.item(i)
            it.setHidden(text.lower() not in it.text().lower())

    def _save_api(self) -> bool:
        api_id, api_hash = self.ed_api_id.text().strip(), self.ed_api_hash.text().strip()
        if not api_id.isdigit() or len(api_hash) < 20:
            QMessageBox.warning(self, "Codici mancanti", "Inserisci prima api_id (solo numeri) e api_hash presi da my.telegram.org.")
            return False
        self.engine.settings.update({"tg_api_id": api_id, "tg_api_hash": api_hash})
        return True

    def tg_login(self):
        if not self._save_api():
            return
        phone, ok = QInputDialog.getText(self, "Accesso a Telegram", "Numero di telefono con prefisso (es. +39 333 1234567):")
        if not ok or not phone.strip():
            return
        try:
            _busy(lambda: self.engine.call(self.engine.tg_send_code(phone)))
        except Exception as e:
            QMessageBox.critical(self, "Errore", f"Invio del codice non riuscito:\n{e}")
            return
        code, ok = QInputDialog.getText(self, "Codice", "Inserisci il codice che hai ricevuto nell'app Telegram:")
        if not ok or not code.strip():
            return
        try:
            res = _busy(lambda: self.engine.call(self.engine.tg_sign_in(code)))
            if res == "password":
                pw, ok = QInputDialog.getText(self, "Verifica in due passaggi",
                                              "Il tuo account ha una password. Inseriscila:", QLineEdit.Password)
                if not ok:
                    return
                res = _busy(lambda: self.engine.call(self.engine.tg_password(pw)))
        except Exception as e:
            QMessageBox.critical(self, "Errore", f"Accesso non riuscito:\n{e}")
            return
        self.lbl_tg_user.setText(f"✅ Collegato come {res}")
        self.load_channels()
        QMessageBox.information(self, "Collegato", f"Collegato come {res}.\n\nOra SPUNTA i canali da copiare "
                                "nell'elenco qui sotto: si salvano da soli e il pallino Telegram diventa verde.")

    def tg_logout(self):
        if QMessageBox.question(self, "Esci", "Scollegare l'account Telegram da questo programma?") == QMessageBox.Yes:
            try:
                _busy(lambda: self.engine.call(self.engine.tg_logout()))
            except Exception:
                pass
            self.lbl_tg_user.setText("Non collegato")

    def load_channels(self):
        if not self.engine.tg_authorized:
            QMessageBox.information(self, "Telegram", "Prima accedi a Telegram (Passo 2).")
            return
        try:
            chats = _busy(lambda: self.engine.call(self.engine.tg_list_chats()))
        except Exception as e:
            QMessageBox.critical(self, "Errore", f"Impossibile leggere i canali:\n{e}")
            return
        self.list_channels.blockSignals(True)
        for c in chats:
            self._add_channel_item(c["id"], c["name"], False)
        self.list_channels.blockSignals(False)

    # ------------------------------------------------------------ MetaTrader
    def _tab_mt5(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        t = QLabel(
            "<b>Passo 1 — Installa il ponte</b> (MetaTrader 5 deve essere installato e aperto almeno una volta)<br>"
            "Premi il pulsante: il file viene copiato da solo nella cartella giusta di MetaTrader.")
        t.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(_btn("🔌  Installa il ponte in MetaTrader", self.install_bridge, "primary"))
        t2 = QLabel(
            "<b>Passo 2 — Attivalo in MetaTrader</b><br>"
            "1) <b>Chiudi e riapri MetaTrader 5</b>.<br>"
            "2) Nel <i>Navigatore</i> (a sinistra) apri <i>Expert Advisors</i> e <b>trascina “TelegramBridge” su un grafico</b> qualsiasi.<br>"
            "3) Nella finestra che si apre, scheda <i>Comuni</i>: spunta <b>Consenti Algo Trading</b> → OK.<br>"
            "4) Premi il pulsante <b>Algo Trading</b> in alto in MetaTrader: deve diventare <b>verde</b>.<br>"
            "Se “TelegramBridge” non compare: tasto destro su <i>Expert Advisors</i> → <i>Aggiorna</i>; "
            "oppure aprilo con MetaEditor e premi <i>Compila</i>.<br>"
            "Quando funziona, qui sotto e nella finestra principale il pallino MetaTrader diventa verde.")
        t2.setWordWrap(True)
        lay.addWidget(t2)
        self.lbl_mt5_state = QLabel()
        lay.addWidget(self.lbl_mt5_state)
        self._refresh_mt5_state()

        lay.addWidget(QLabel("<b>Avanzate</b> (di solito non servono)"))
        form = QFormLayout()
        row = QHBoxLayout()
        self.ed_mt5_dir = QLineEdit(self.s["mt5_dir"])
        self.ed_mt5_dir.setPlaceholderText("vuoto = trova automaticamente")
        row.addWidget(self.ed_mt5_dir, 1)
        row.addWidget(_btn("Trova", self.find_mt5))
        row.addWidget(_btn("Sfoglia…", self.browse_mt5))
        form.addRow("Cartella Common/Files di MT5", row)
        self.sp_magic = QSpinBox()
        self.sp_magic.setRange(1, 2_000_000_000)
        self.sp_magic.setValue(int(self.s["magic"]))
        form.addRow("Numero magico (identifica le operazioni del programma)", self.sp_magic)
        self.sp_slip = QSpinBox()
        self.sp_slip.setRange(0, 100000)
        self.sp_slip.setValue(int(self.s["slippage_points"]))
        form.addRow("Slippage massimo (punti)", self.sp_slip)
        lay.addLayout(form)
        lay.addStretch(1)
        return w

    def _refresh_mt5_state(self):
        st = self.engine.status_snapshot()
        if st["mt5"]:
            acc = st.get("account") or {}
            self.lbl_mt5_state.setText(f"✅ <b>MetaTrader collegato</b> — conto {acc.get('login')} su {acc.get('server')}")
        elif st["mt5_found"]:
            self.lbl_mt5_state.setText("⬜ MetaTrader trovato, ma il ponte non è attivo (vedi Passo 2).")
        else:
            self.lbl_mt5_state.setText("❌ MetaTrader 5 non trovato su questo Mac. Installalo e aprilo una volta, poi premi Installa.")

    def install_bridge(self):
        done = install_ea()
        if not done:
            QMessageBox.warning(self, "MetaTrader non trovato",
                                "Non ho trovato MetaTrader 5.\n\nInstallalo (dal sito del broker o metatrader5.com), "
                                "aprilo almeno una volta, poi riprova.\n\nIn alternativa copia a mano il file "
                                "mt5/TelegramBridge.mq5 in MetaTrader: File → Apri cartella dati → MQL5 → Experts.")
            return
        self.find_mt5(silent=True)
        QMessageBox.information(self, "Fatto",
                                "Ponte installato in:\n" + "\n".join(str(p) for p in done) +
                                "\n\nOra segui il Passo 2: chiudi e riapri MetaTrader e trascina TelegramBridge su un grafico.")

    def find_mt5(self, silent=False):
        p = find_common_files_dir()
        if p:
            self.ed_mt5_dir.setText("")
            self.engine.setup_bridge(str(p))
            if not silent:
                QMessageBox.information(self, "Trovato", f"Cartella di MetaTrader trovata:\n{p}")
        elif not silent:
            QMessageBox.warning(self, "Non trovato", "MetaTrader 5 non trovato. Aprilo almeno una volta e riprova, "
                                                     "oppure indica la cartella con Sfoglia.")
        self._refresh_mt5_state()

    def browse_mt5(self):
        d = QFileDialog.getExistingDirectory(self, "Scegli la cartella Common/Files di MetaTrader 5")
        if d:
            self.ed_mt5_dir.setText(d)

    # ------------------------------------------------------------ schede generiche
    def _tab_schema(self, name, fields):
        inner = QWidget()
        form = QFormLayout(inner)
        if name in ("Ingresso", "Gestione automatica"):
            note = QLabel("ℹ️ Le distanze sono in <b>punti</b> di MetaTrader: nella maggior parte delle coppie forex "
                          "1 pip = 10 punti; sull'oro (2 decimali) 1 dollaro = 100 punti.")
            note.setObjectName("help")
            note.setWordWrap(True)
            form.addRow(note)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setLabelAlignment(Qt.AlignLeft)
        for key, label, typ, opts, help_ in fields:
            wdg = self._make_widget(typ, opts, self.s[key])
            self.widgets[key] = (typ, wdg)
            if help_:
                wdg.setToolTip(help_)
                box = QVBoxLayout()
                box.setSpacing(2)
                box.addWidget(wdg)
                h = QLabel(help_)
                h.setObjectName("help")
                h.setWordWrap(True)
                box.addWidget(h)
                holder = QWidget()
                holder.setLayout(box)
                form.addRow(label, holder)
            else:
                form.addRow(label, wdg)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setProperty("schema_tab", name)
        return scroll

    def _make_widget(self, typ, opts, value):
        if typ == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
        elif typ == "choice":
            w = QComboBox()
            for v, text in opts:
                w.addItem(text, v)
            w.setCurrentIndex(max(0, w.findData(value)))
        elif typ == "int":
            w = QSpinBox()
            w.setRange(int(opts[0]), int(opts[1]))
            w.setValue(int(value))
        elif typ == "float":
            w = QDoubleSpinBox()
            w.setRange(opts[0], opts[1])
            w.setDecimals(opts[2])
            w.setSingleStep(10 ** -opts[2] if opts[2] else 1)
            w.setValue(float(value))
        elif typ == "time":
            w = QTimeEdit()
            w.setDisplayFormat("HH:mm")
            w.setTime(QTime.fromString(value, "HH:mm"))
        elif typ == "days":
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(0, 0, 0, 0)
            w.boxes = []
            for i, d in enumerate(["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]):
                cb = QCheckBox(d)
                cb.setChecked(i in value)
                row.addWidget(cb)
                w.boxes.append(cb)
        elif typ == "text":
            w = QPlainTextEdit(value)
            w.setMinimumHeight(140)
        else:
            w = QLineEdit(str(value))
        return w

    def _read_widget(self, typ, w):
        if typ == "bool":
            return w.isChecked()
        if typ == "choice":
            return w.currentData()
        if typ == "int":
            return w.value()
        if typ == "float":
            return round(w.value(), 4)
        if typ == "time":
            return w.time().toString("HH:mm")
        if typ == "days":
            return [i for i, cb in enumerate(w.boxes) if cb.isChecked()]
        if typ == "text":
            return w.toPlainText()
        return w.text()

    def _set_widget(self, typ, w, v):
        if typ == "bool":
            w.setChecked(bool(v))
        elif typ == "choice":
            w.setCurrentIndex(max(0, w.findData(v)))
        elif typ in ("int", "float"):
            w.setValue(v)
        elif typ == "time":
            w.setTime(QTime.fromString(v, "HH:mm"))
        elif typ == "days":
            for i, cb in enumerate(w.boxes):
                cb.setChecked(i in v)
        elif typ == "text":
            w.setPlainText(v)
        else:
            w.setText(str(v))

    def reset_tab(self):
        name = self.tabs.tabText(self.tabs.currentIndex())
        if name not in SCHEMA:
            QMessageBox.information(self, "Ripristina", "Questa scheda non ha valori predefiniti da ripristinare.")
            return
        for key, _label, typ, _o, _h in SCHEMA[name]:
            self._set_widget(typ, self.widgets[key][1], DEFAULTS[key])

    # ------------------------------------------------------------ prova messaggio
    def _tab_test(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        t = QLabel("Incolla qui un messaggio del canale e premi <b>Analizza</b>: vedi come viene letto e quali ordini "
                   "verrebbero inviati con le impostazioni attuali (anche quelle non ancora salvate). "
                   "Non viene inviato nulla. Se qualcosa non viene riconosciuto, aggiungi la parola nella scheda "
                   "<i>Parole chiave</i> o un nome nella scheda <i>Orari e simboli</i>.")
        t.setWordWrap(True)
        lay.addWidget(t)
        self.test_in = QPlainTextEdit()
        self.test_in.setPlaceholderText("es.\nXAUUSD BUY NOW 2350\nSL 2340\nTP1 2355\nTP2 2360")
        lay.addWidget(self.test_in, 1)
        lay.addWidget(_btn("Analizza", self.run_test, "primary"))
        self.test_out = QPlainTextEdit(readOnly=True)
        lay.addWidget(self.test_out, 1)
        return w

    def current_values(self) -> dict:
        vals = self.engine.settings.get()
        for key, (typ, w) in self.widgets.items():
            vals[key] = self._read_widget(typ, w)
        return vals

    def run_test(self):
        text = self.test_in.toPlainText().strip()
        if not text:
            return
        try:
            self.test_out.setPlainText(self.engine.test_message(text, self.current_values()))
        except Exception as e:
            self.test_out.setPlainText(f"Errore: {e}")

    # ------------------------------------------------------------ salva
    def save(self):
        vals = {key: self._read_widget(typ, w) for key, (typ, w) in self.widgets.items()}
        if self.ed_api_id.text().strip():
            vals["tg_api_id"] = self.ed_api_id.text().strip()
            vals["tg_api_hash"] = self.ed_api_hash.text().strip()
        chans = []
        for i in range(self.list_channels.count()):
            it = self.list_channels.item(i)
            if it.checkState() == Qt.Checked:
                chans.append({"id": it.data(Qt.UserRole), "name": it.text()})
        vals["tg_channels"] = chans
        vals["mt5_dir"] = self.ed_mt5_dir.text().strip()
        vals["magic"] = self.sp_magic.value()
        vals["slippage_points"] = self.sp_slip.value()
        if vals.get("allow_real") and not self.s["allow_real"] and not vals.get("dry_run"):
            if QMessageBox.question(self, "Conto reale",
                                    "Hai attivato le operazioni sul conto REALE. Confermi?") != QMessageBox.Yes:
                return
        old = self.s
        self.engine.settings.update(vals)
        if vals["mt5_dir"] != old["mt5_dir"]:
            self.engine.setup_bridge()
        try:
            if (vals.get("tg_api_id"), vals.get("tg_api_hash")) != (old["tg_api_id"], old["tg_api_hash"]) or \
                    vals["tg_channels"] != old["tg_channels"]:
                self.engine.call(self.engine.start_telegram(), timeout=30)
        except Exception as e:
            QMessageBox.warning(self, "Telegram", f"Impostazioni salvate, ma Telegram non si è collegato:\n{e}")
        self.accept()

