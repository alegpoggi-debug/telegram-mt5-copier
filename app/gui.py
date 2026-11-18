"""Interfaccia grafica (PySide6): finestra principale + impostazioni essenziali."""
from __future__ import annotations

import datetime as dt
import html
import threading
import time

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTime, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QTimeEdit, QVBoxLayout, QWidget,
)

from . import updater
from .bridge import find_common_files_dir
from .mt5_install import install_ea

# ----------------------------------------------------------------------------- palette
BG, SURFACE, SURFACE2, BORDER = "#0e1116", "#151a21", "#1b222b", "#242c36"
TEXT, MUTED, FAINT = "#e6edf3", "#8b949e", "#5b6572"
GREEN, RED, AMBER, BLUE = "#3fb950", "#f85149", "#d29922", "#58a6ff"

LOG_COLORS = {"msg": "#aab3bf", "ai": BLUE, "trade": "#e3b341", "ok": GREEN, "warn": AMBER, "error": RED, "info": MUTED}

STYLE = f"""
* {{ font-size: 13px; }}
QWidget {{ color: {TEXT}; background: transparent; }}
QMainWindow, QDialog {{ background: {BG}; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#banner {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#h1 {{ font-size: 20px; font-weight: 700; color: {TEXT}; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; color: {TEXT}; }}
QLabel#section {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#muted {{ color: {MUTED}; font-size: 12px; }}
QLabel#pill {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 11px; padding: 3px 10px; font-size: 12px; }}
QTextEdit, QPlainTextEdit {{ background: transparent; border: none; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit {{
  background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px; min-height: 18px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTimeEdit:focus {{ border-color: {BLUE}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox {{ min-width: 190px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; border: none; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; selection-background-color: {BORDER}; }}
QPushButton {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {FAINT}; }}
QPushButton#primary {{ background: {GREEN}; border-color: {GREEN}; color: #0b1a0f; font-weight: 600; }}
QPushButton#primary:hover {{ background: #4cc95d; }}
QPushButton#danger {{ color: {RED}; border-color: #5a2a2a; }}
QPushButton#danger:hover {{ background: #2a1517; }}
QPushButton#ghost {{ background: transparent; border: none; color: {MUTED}; padding: 6px 8px; }}
QPushButton#ghost:hover {{ color: {TEXT}; }}
QTableWidget {{ background: transparent; border: none; gridline-color: transparent; selection-background-color: {SURFACE2}; }}
QTableWidget::item {{ padding: 4px; border-bottom: 1px solid {BORDER}; }}
QHeaderView::section {{ background: transparent; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
  padding: 6px 4px; font-size: 11px; font-weight: 600; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ padding: 9px 12px; border-radius: 8px; color: {MUTED}; margin: 1px 0; }}
QListWidget#nav::item:selected {{ background: {SURFACE2}; color: {TEXT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QSplitter::handle {{ background: transparent; width: 12px; }}
QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; }}
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


# ----------------------------------------------------------------------------- piccoli componenti
class Switch(QAbstractButton):
    """Interruttore on/off minimale."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        return QSize(40, 22)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, 38, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(GREEN if self.isChecked() else BORDER))
        p.drawRoundedRect(r, 10, 10)
        p.setBrush(QColor("#ffffff"))
        x = 20 if self.isChecked() else 3
        p.drawEllipse(QRectF(x, 3, 16, 16))


def _label(text, name=None, wrap=False):
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def _btn(text, slot, kind=None):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    return b


def _card(margins=16, spacing=10):
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    return f, lay


def _fit_to_screen(win, w, h):
    """Dimensione desiderata, ma mai più grande dello schermo del Mac."""
    scr = QApplication.primaryScreen()
    if scr is not None:
        g = scr.availableGeometry()
        w, h = min(w, int(g.width() * 0.95)), min(h, int(g.height() * 0.92))
    win.resize(w, h)


def _busy(fn):
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        return fn()
    finally:
        QApplication.restoreOverrideCursor()


def _pill(color, text):
    return f'<span style="color:{color}">●</span>&nbsp; {text}'


# ============================================================================= finestra principale
class MainWindow(QMainWindow):
    def __init__(self, ui: UiBridge, engine):
        super().__init__()
        self.engine = engine
        self.ui = ui
        self.last_status = {}
        self.setWindowTitle(f"Telegram MT5 Copier  ·  v{updater.local_version()}")
        self.setStyleSheet(STYLE)
        self.setMinimumSize(900, 560)
        _fit_to_screen(self, 1240, 780)

        # ---- intestazione
        title = _label("Copier", "h1")
        self.lbl_tg, self.lbl_mt5, self.lbl_acc, self.lbl_pl = (_label("", "pill") for _ in range(4))
        self.lbl_mode = _label("", "pill")
        self.btn_pause = _btn("Pausa", self.toggle_pause)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(title)
        header.addSpacing(10)
        for w in (self.lbl_tg, self.lbl_mt5, self.lbl_acc, self.lbl_pl):
            header.addWidget(w)
        header.addStretch(1)
        header.addWidget(self.lbl_mode)
        header.addSpacing(6)
        header.addWidget(self.btn_pause)
        header.addWidget(_btn("Impostazioni", lambda: self.open_settings(None), "primary"))

        # ---- avvisi (configurazione / aggiornamento)
        self.setup_box = QFrame()
        self.setup_box.setObjectName("banner")
        sb = QHBoxLayout(self.setup_box)
        sb.setContentsMargins(14, 10, 10, 10)
        self.setup_lbl = _label("", wrap=True)
        self.setup_lbl.setTextFormat(Qt.RichText)
        sb.addWidget(self.setup_lbl, 1)
        sb.addWidget(_btn("Collega Telegram", lambda: self.open_settings("Telegram")))
        sb.addWidget(_btn("Collega MetaTrader", lambda: self.open_settings("MetaTrader")))

        self.update_box = QFrame()
        self.update_box.setObjectName("banner")
        ub = QHBoxLayout(self.update_box)
        ub.setContentsMargins(14, 10, 10, 10)
        self.update_lbl = _label("", wrap=True)
        ub.addWidget(self.update_lbl, 1)
        ub.addWidget(_btn("Aggiorna ora", self.do_update, "primary"))
        self.update_box.hide()

        # ---- attività
        act_card, act = _card()
        row = QHBoxLayout()
        row.addWidget(_label("ATTIVITÀ", "section"))
        row.addStretch(1)
        row.addWidget(_btn("Prova un messaggio", lambda: self.open_settings("Prova un messaggio"), "ghost"))
        act.addLayout(row)
        self.log = QTextEdit(readOnly=True)
        act.addWidget(self.log, 1)

        # ---- operazioni aperte
        pos_card, pos = _card()
        pos.addWidget(_label("OPERAZIONI APERTE", "section"))
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["TICKET", "SIMBOLO", "TIPO", "LOTTI", "APERTURA", "SL", "TP", "PROFITTO"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        pos.addWidget(self.table, 1)
        self.empty_lbl = _label("Nessuna operazione aperta", "muted")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        pos.addWidget(self.empty_lbl, 1)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addWidget(_label("Selezionate", "muted"))
        actions.addWidget(_btn("Chiudi", lambda: self.do_button("close", True, 100)))
        actions.addWidget(_btn("Chiudi 50%", lambda: self.do_button("close", True, 50)))
        actions.addWidget(_btn("Pareggio", lambda: self.do_button("breakeven", True)))
        actions.addStretch(1)
        actions.addWidget(_label("Tutte", "muted"))
        actions.addWidget(_btn("Pareggio", lambda: self.do_button("breakeven", False)))
        actions.addWidget(_btn("Chiudi 50%", lambda: self.do_button("close", False, 50)))
        actions.addWidget(_btn("Chiudi tutto", lambda: self.do_button("close", False, 100), "danger"))
        pos.addLayout(actions)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(act_card)
        split.addWidget(pos_card)
        split.setSizes([520, 700])
        split.setChildrenCollapsible(False)

        root = QVBoxLayout()
        root.setContentsMargins(18, 14, 18, 18)
        root.setSpacing(12)
        root.addLayout(header)
        root.addWidget(self.setup_box)
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
        now = dt.datetime.now().strftime("%H:%M")
        safe = html.escape(text).replace("\n", "<br>")
        self.log.append(f'<div style="margin-bottom:4px"><span style="color:{FAINT}">{now}</span>&nbsp;&nbsp;'
                        f'<span style="color:{LOG_COLORS.get(level, TEXT)}">{safe}</span></div>')

    def update_status(self, st):
        self.last_status = st
        tg_ok, mt_ok = st.get("telegram"), st.get("mt5")
        if tg_ok:
            n = len(st.get("channels", []))
            self.lbl_tg.setText(_pill(GREEN, f"Telegram · {n} {'canale' if n == 1 else 'canali'}"))
        elif st.get("telegram_authorized"):
            self.lbl_tg.setText(_pill(AMBER, "Telegram · nessun canale"))
        else:
            self.lbl_tg.setText(_pill(RED, "Telegram"))
        self.lbl_mt5.setText(_pill(GREEN if mt_ok else RED, "MetaTrader"))
        acc = st.get("account")
        if acc and mt_ok:
            kind = {0: "Demo", 1: "Contest", 2: "Reale"}.get(acc["trade_mode"], "?")
            self.lbl_acc.setText(f"{kind} {acc['login']} · {acc['equity']:.2f} {acc['currency']}")
            self.lbl_acc.setToolTip(f"Saldo {acc['balance']:.2f} · Equity {acc['equity']:.2f} {acc['currency']}")
            self.lbl_acc.show()
        else:
            self.lbl_acc.hide()
        pl = st.get("today_pl")
        if pl is None:
            self.lbl_pl.hide()
        else:
            self.lbl_pl.setText(f'Oggi <span style="color:{GREEN if pl >= 0 else RED}">{pl:+.2f}</span>')
            self.lbl_pl.show()
        if st.get("paused"):
            self.lbl_mode.setText(_pill(RED, "In pausa"))
        elif st.get("dry_run", True):
            self.lbl_mode.setText(_pill(AMBER, "Modalità prova"))
        else:
            self.lbl_mode.setText(_pill(GREEN, "Attivo"))
        self.btn_pause.setText("Riprendi" if st.get("paused") else "Pausa")

        steps = [
            (st.get("telegram_authorized"), "Accedi a Telegram"),
            (bool(st.get("channels")), "Scegli i canali"),
            (st.get("mt5_found"), "Trova MetaTrader"),
            (mt_ok, "Avvia il ponte"),
        ]
        if all(ok for ok, _ in steps):
            self.setup_box.hide()
        else:
            self.setup_box.show()
            parts = [f'<span style="color:{GREEN if ok else FAINT}">{"✓" if ok else "○"}</span>&nbsp;{t}'
                     for ok, t in steps]
            self.setup_lbl.setText("<b>Configurazione</b>&nbsp;&nbsp;&nbsp;" + "&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))

        rows = st.get("positions", []) + [dict(o, side=o["type"].replace("ORDER_TYPE_", "").lower(), price_open=o["price"],
                                                price_current=0, profit=0) for o in st.get("orders", [])]
        selected = {self.table.item(i.row(), 0).text() for i in self.table.selectionModel().selectedRows()
                    if self.table.item(i.row(), 0)}
        self.table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            vals = [p["ticket"], p["symbol"], p["side"].upper(), p["volume"], p["price_open"], p["sl"] or "–",
                    p["tp"] or "–", f"{p['profit']:+.2f}"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 7:
                    item.setForeground(QColor(GREEN if p["profit"] >= 0 else RED))
                if c == 2:
                    item.setForeground(QColor(GREEN if p["side"].startswith("buy") else RED))
                self.table.setItem(r, c, item)
            if str(p["ticket"]) in selected:
                self.table.selectRow(r)
        self.empty_lbl.setVisible(not rows)
        self.table.setVisible(bool(rows))

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
            self.add_log(f"→ {r}", "trade")

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
        self.update_lbl.setText(f"<b>Aggiornamento disponibile · versione {info['version']}</b>"
                                f"{'<br><span style=color:' + MUTED + '>' + notes + '</span>' if notes else ''}")
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
        self.add_log("In pausa: i nuovi segnali vengono ignorati, la gestione delle operazioni continua." if new
                     else "Ripreso.", "warn" if new else "ok")
        self.update_status(dict(self.last_status, paused=new))

    def open_settings(self, page):
        dlg = SettingsDialog(self, self.engine, page)
        dlg.exec()

    def closeEvent(self, e):
        try:
            if self.engine.tg is not None:
                self.engine.call(self.engine.tg.disconnect(), timeout=3)
        except Exception:
            pass
        super().closeEvent(e)


# ============================================================================= impostazioni
class ChannelRow(QFrame):
    """Riga di un canale: si clicca ovunque per sceglierlo."""

    def __init__(self, cid, name, checked, on_change):
        super().__init__()
        self.cid, self.name = cid, name
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        self.lbl = QLabel(name)
        self.lbl.setTextFormat(Qt.PlainText)
        self.switch = Switch(checked)
        lay.addWidget(self.lbl, 1)
        lay.addWidget(self.switch)
        self.switch.toggled.connect(lambda _c: (self._paint(), on_change()))
        self._paint()

    def _paint(self):
        on = self.switch.isChecked()
        self.setStyleSheet(f"ChannelRow {{ background: {SURFACE2 if on else 'transparent'}; border-radius: 8px; }}"
                           f"QLabel {{ color: {TEXT if on else MUTED}; }}")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.switch.toggle()
        super().mouseReleaseEvent(e)

    def isChecked(self):
        return self.switch.isChecked()


def _clean_spin(sp, width=130):
    sp.setFixedWidth(width)
    sp.setAlignment(Qt.AlignRight)
    return sp


class SettingsDialog(QDialog):
    PAGES = ["Telegram", "MetaTrader", "Trading", "Prova un messaggio"]

    def __init__(self, parent, engine, start_page=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Impostazioni")
        self.setStyleSheet(STYLE)
        self.setMinimumSize(760, 520)
        _fit_to_screen(self, 940, 660)
        self.s = engine.settings.get()
        self.rows: list[ChannelRow] = []
        self._loaded_chats = False

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(180)
        self.stack = QStackedWidget()
        for name, page in (("Telegram", self._page_telegram()), ("MetaTrader", self._page_mt5()),
                           ("Trading", self._page_trading()), ("Prova un messaggio", self._page_test())):
            QListWidgetItem(name, self.nav)
            self.stack.addWidget(self._scroll(page))
        self.nav.currentRowChanged.connect(self._go)
        idx = self.PAGES.index(start_page) if start_page in self.PAGES else 0
        self.nav.setCurrentRow(idx)

        left = QVBoxLayout()
        left.setContentsMargins(14, 18, 6, 14)
        left.addWidget(_label("Impostazioni", "h2"))
        left.addSpacing(10)
        left.addWidget(self.nav, 1)
        left.addWidget(_label(f"Versione {updater.local_version()}", "muted"))

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(_btn("Annulla", self.reject))
        bottom.addWidget(_btn("Salva", self.save, "primary"))
        right = QVBoxLayout()
        right.setContentsMargins(6, 14, 18, 14)
        right.addWidget(self.stack, 1)
        right.addLayout(bottom)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(left)
        root.addLayout(right, 1)

    def _scroll(self, page):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(page)
        return sc

    def _go(self, i):
        self.stack.setCurrentIndex(i)
        if self.PAGES[i] == "Telegram" and self.engine.tg_authorized and not self._loaded_chats:
            self._loaded_chats = True
            self.load_channels(quiet=True)

    def _page(self, title, subtitle):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 8, 4)
        lay.setSpacing(12)
        lay.addWidget(_label(title, "h1"))
        lay.addWidget(_label(subtitle, "muted", wrap=True))
        lay.addSpacing(4)
        return w, lay

    def _row(self, lay, title, desc, control):
        """Riga impostazione: titolo + spiegazione a sinistra, comando a destra."""
        r = QHBoxLayout()
        r.setSpacing(16)
        txt = QVBoxLayout()
        txt.setSpacing(2)
        txt.addWidget(_label(title))
        if desc:
            txt.addWidget(_label(desc, "muted", wrap=True))
        r.addLayout(txt, 1)
        if isinstance(control, QHBoxLayout):
            r.addLayout(control)
        else:
            r.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(r)

    # ------------------------------------------------------------ Telegram
    def _page_telegram(self):
        w, lay = self._page("Telegram", "Collega il tuo account e scegli i canali da cui copiare i segnali.")

        acc_card, acc = _card()
        acc.addWidget(_label("ACCOUNT", "section"))
        self.lbl_tg_user = _label("Non collegato", "h2")
        self.btn_logout = _btn("Esci", self.tg_logout)
        top = QHBoxLayout()
        top.addWidget(self.lbl_tg_user, 1)
        top.addWidget(self.btn_logout)
        acc.addLayout(top)

        self.login_box = QWidget()
        lb = QVBoxLayout(self.login_box)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.setSpacing(8)
        lb.addWidget(_label("Servono due codici gratuiti di Telegram (api_id e api_hash): si ottengono su "
                            "my.telegram.org → API development tools.", "muted", wrap=True))
        self.ed_api_id = QLineEdit(str(self.s["tg_api_id"]))
        self.ed_api_id.setPlaceholderText("api_id")
        self.ed_api_hash = QLineEdit(self.s["tg_api_hash"])
        self.ed_api_hash.setPlaceholderText("api_hash")
        codes = QHBoxLayout()
        codes.addWidget(self.ed_api_id, 1)
        codes.addWidget(self.ed_api_hash, 2)
        lb.addLayout(codes)
        btns = QHBoxLayout()
        btns.addWidget(_btn("Apri my.telegram.org", lambda: QDesktopServices.openUrl(QUrl("https://my.telegram.org/apps")), "ghost"))
        btns.addStretch(1)
        btns.addWidget(_btn("Accedi a Telegram", self.tg_login, "primary"))
        lb.addLayout(btns)
        acc.addWidget(self.login_box)
        lay.addWidget(acc_card)

        ch_card, ch = _card()
        head = QHBoxLayout()
        head.addWidget(_label("CANALI", "section"))
        head.addStretch(1)
        head.addWidget(_btn("Aggiorna elenco", lambda: self.load_channels(quiet=False), "ghost"))
        ch.addLayout(head)
        ch.addWidget(_label("Clicca su un canale per attivarlo. Le scelte si salvano subito.", "muted", wrap=True))
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("Cerca…")
        self.ed_filter.textChanged.connect(self.filter_channels)
        ch.addWidget(self.ed_filter)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(2)
        ch.addLayout(self.rows_box)
        self.lbl_no_rows = _label("Accedi a Telegram per vedere i tuoi canali.", "muted")
        ch.addWidget(self.lbl_no_rows)
        lay.addWidget(ch_card)
        lay.addStretch(1)

        for c in self.s["tg_channels"]:
            self._add_channel_row(c["id"], c["name"], True)
        try:
            me = self.engine.call(self.engine.tg_me(), timeout=10) if self.engine.tg_authorized else None
        except Exception:
            me = None
        self._set_account(me)
        return w

    def _set_account(self, me):
        if me:
            self.lbl_tg_user.setText(f"Collegato come {me}")
            self.login_box.hide()
            self.btn_logout.show()
        else:
            self.lbl_tg_user.setText("Non collegato")
            self.login_box.show()
            self.btn_logout.hide()

    def _add_channel_row(self, cid, name, checked):
        if any(r.cid == cid for r in self.rows):
            return
        row = ChannelRow(cid, name, checked, self._channels_changed)
        self.rows.append(row)
        self.rows_box.addWidget(row)
        self.lbl_no_rows.hide()

    def _checked_channels(self):
        return [{"id": r.cid, "name": r.name} for r in self.rows if r.isChecked()]

    def _channels_changed(self):
        """Salva subito i canali scelti e li mette in ascolto."""
        chans = self._checked_channels()
        self.engine.settings.update({"tg_channels": chans})
        self.s["tg_channels"] = chans
        try:
            self.engine.call(self.engine.reload_channels(), timeout=30)
        except Exception:
            pass

    def filter_channels(self, text):
        for r in self.rows:
            r.setVisible(text.lower() in r.name.lower())

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
        self._set_account(res)
        self._loaded_chats = True
        self.load_channels(quiet=True)
        QMessageBox.information(self, "Collegato", f"Collegato come {res}.\n\nOra clicca sui canali da copiare: "
                                "si attivano subito.")

    def tg_logout(self):
        if QMessageBox.question(self, "Esci", "Scollegare l'account Telegram da questo programma?") == QMessageBox.Yes:
            try:
                _busy(lambda: self.engine.call(self.engine.tg_logout()))
            except Exception:
                pass
            self._set_account(None)

    def load_channels(self, quiet=False):
        if not self.engine.tg_authorized:
            if not quiet:
                QMessageBox.information(self, "Telegram", "Prima accedi a Telegram.")
            return
        try:
            chats = _busy(lambda: self.engine.call(self.engine.tg_list_chats()))
        except Exception as e:
            if not quiet:
                QMessageBox.critical(self, "Errore", f"Impossibile leggere i canali:\n{e}")
            return
        for c in chats:
            self._add_channel_row(c["id"], c["name"], False)

    # ------------------------------------------------------------ MetaTrader
    def _page_mt5(self):
        w, lay = self._page("MetaTrader", "Il ponte è un piccolo programma dentro MetaTrader 5 che esegue gli ordini.")
        st_card, st = _card()
        st.addWidget(_label("STATO", "section"))
        self.lbl_mt5_state = _label("", "h2", wrap=True)
        self.lbl_mt5_detail = _label("", "muted", wrap=True)
        st.addWidget(self.lbl_mt5_state)
        st.addWidget(self.lbl_mt5_detail)
        lay.addWidget(st_card)

        how_card, how = _card()
        how.addWidget(_label("COLLEGAMENTO", "section"))
        steps = [
            "Apri MetaTrader 5 (versione per Mac dal sito metatrader5.com) ed entra nel tuo conto.",
            "Premi «Installa il ponte» qui sotto, poi chiudi e riapri MetaTrader.",
            "In MetaTrader: Navigatore → Expert Advisors → trascina «TelegramBridge» su un grafico.",
            "Nella finestra che si apre spunta «Consenti Algo Trading», premi OK e accendi «Algo Trading» in alto.",
        ]
        for i, s in enumerate(steps, 1):
            r = QHBoxLayout()
            n = _label(str(i), "muted")
            n.setFixedWidth(16)
            r.addWidget(n, 0, Qt.AlignTop)
            r.addWidget(_label(s, wrap=True), 1)
            how.addLayout(r)
        b = QHBoxLayout()
        b.addStretch(1)
        b.addWidget(_btn("Installa il ponte", self.install_bridge, "primary"))
        how.addLayout(b)
        lay.addWidget(how_card)

        adv_card, adv = _card()
        adv.addWidget(_label("CARTELLA DI METATRADER", "section"))
        self.ed_mt5_dir = QLineEdit(self.s["mt5_dir"])
        self.ed_mt5_dir.setPlaceholderText("Trovata automaticamente")
        r = QHBoxLayout()
        r.addWidget(self.ed_mt5_dir, 1)
        r.addWidget(_btn("Trova", self.find_mt5))
        r.addWidget(_btn("Scegli…", self.browse_mt5))
        adv.addLayout(r)
        adv.addWidget(_label("Lascia vuoto: di solito il programma la trova da solo.", "muted", wrap=True))
        lay.addWidget(adv_card)
        lay.addStretch(1)
        self._refresh_mt5_state()
        return w

    def _refresh_mt5_state(self):
        st = self.engine.status_snapshot()
        if st["mt5"]:
            acc = st.get("account") or ((self.engine.bridge.read_state() or {}).get("account") if self.engine.bridge else None) or {}
            self.lbl_mt5_state.setText(f'<span style="color:{GREEN}">●</span> Collegato')
            self.lbl_mt5_detail.setText(f"Conto {acc.get('login')} su {acc.get('server')}")
        elif st["mt5_found"]:
            self.lbl_mt5_state.setText(f'<span style="color:{AMBER}">●</span> Ponte non attivo')
            self.lbl_mt5_detail.setText("MetaTrader è installato ma il ponte non risponde: segui i passaggi qui sotto.")
        else:
            self.lbl_mt5_state.setText(f'<span style="color:{RED}">●</span> MetaTrader non trovato')
            self.lbl_mt5_detail.setText("Installa MetaTrader 5 e aprilo almeno una volta.")

    def install_bridge(self):
        done = install_ea()
        if not done:
            QMessageBox.warning(self, "MetaTrader non trovato",
                                "Non ho trovato MetaTrader 5.\n\nInstallalo (dal sito del broker o metatrader5.com), "
                                "aprilo almeno una volta, poi riprova.")
            return
        self.find_mt5(silent=True)
        QMessageBox.information(self, "Fatto", "Ponte installato.\n\nOra chiudi e riapri MetaTrader e trascina "
                                               "«TelegramBridge» su un grafico.")

    def find_mt5(self, silent=False):
        p = find_common_files_dir()
        if p:
            self.ed_mt5_dir.setText("")
            self.engine.setup_bridge(str(p))
            if not silent:
                QMessageBox.information(self, "Trovato", f"Cartella di MetaTrader trovata:\n{p}")
        elif not silent:
            QMessageBox.warning(self, "Non trovato", "MetaTrader 5 non trovato. Aprilo almeno una volta e riprova.")
        self._refresh_mt5_state()

    def browse_mt5(self):
        d = QFileDialog.getExistingDirectory(self, "Scegli la cartella Common/Files di MetaTrader 5")
        if d:
            self.ed_mt5_dir.setText(d)

    # ------------------------------------------------------------ Trading
    def _page_trading(self):
        s = self.s
        w, lay = self._page("Trading", "Come vengono aperte e gestite le operazioni.")

        # modalità
        c1, l1 = _card(spacing=14)
        l1.addWidget(_label("MODALITÀ", "section"))
        self.sw_dry = Switch(s["dry_run"])
        self._row(l1, "Modalità prova", "Legge i segnali e mostra cosa farebbe, senza aprire ordini.", self.sw_dry)
        self.sw_real = Switch(s["allow_real"])
        self._row(l1, "Consenti conto reale", "Se disattivato, sul conto reale non viene aperto nulla.", self.sw_real)
        lay.addWidget(c1)

        # dimensione
        c2, l2 = _card(spacing=14)
        l2.addWidget(_label("DIMENSIONE", "section"))
        self.cb_lot_mode = QComboBox()
        for v, t in (("fixed", "Lotto fisso"), ("risk_percent", "% del saldo"), ("risk_money", "Importo fisso")):
            self.cb_lot_mode.addItem(t, v)
        self._lot_vals = {"fixed": s["fixed_lot"], "risk_percent": s["risk_percent"], "risk_money": s["risk_money"]}
        self.sp_lot = QDoubleSpinBox()
        _clean_spin(self.sp_lot)
        self._lot_mode = s["lot_mode"] if s["lot_mode"] in self._lot_vals else "fixed"
        self.cb_lot_mode.setCurrentIndex(self.cb_lot_mode.findData(self._lot_mode))
        self._apply_lot_mode(self._lot_mode)
        self.cb_lot_mode.currentIndexChanged.connect(lambda _i: self._lot_mode_changed())
        self._row(l2, "Calcolo del lotto", "Lotto fisso per ogni posizione, oppure rischio per segnale "
                  "(calcolato sullo Stop Loss).", self.cb_lot_mode)
        self.lbl_lot_title = _label("")
        self.lbl_lot_desc = _label("", "muted", wrap=True)
        r = QHBoxLayout()
        t = QVBoxLayout()
        t.setSpacing(2)
        t.addWidget(self.lbl_lot_title)
        t.addWidget(self.lbl_lot_desc)
        r.addLayout(t, 1)
        r.addWidget(self.sp_lot, 0, Qt.AlignRight | Qt.AlignVCenter)
        l2.addLayout(r)
        self._update_lot_labels()
        self.sp_maxlot = QDoubleSpinBox()
        self.sp_maxlot.setRange(0.01, 100)
        self.sp_maxlot.setDecimals(2)
        self.sp_maxlot.setSingleStep(0.01)
        self.sp_maxlot.setValue(s["max_lot_per_trade"])
        _clean_spin(self.sp_maxlot)
        self._row(l2, "Lotto massimo", "Limite di sicurezza per ogni posizione.", self.sp_maxlot)
        self.sp_maxpos = QSpinBox()
        self.sp_maxpos.setRange(1, 100)
        self.sp_maxpos.setValue(s["max_open_trades"])
        _clean_spin(self.sp_maxpos)
        self._row(l2, "Posizioni aperte al massimo", "Oltre questo numero i nuovi segnali vengono ignorati.", self.sp_maxpos)
        lay.addWidget(c2)

        # segnali
        c3, l3 = _card(spacing=14)
        l3.addWidget(_label("SEGNALI", "section"))
        self.cb_tp = QComboBox()
        tp_opts = [("split", "Una posizione per ogni TP"), ("first", "Solo il primo TP"), ("last", "Solo l'ultimo TP")]
        if s["multi_tp_mode"] == "index":
            tp_opts.append(("index", f"Solo il TP n° {s['tp_index']}"))
        for v, t in tp_opts:
            self.cb_tp.addItem(t, v)
        self.cb_tp.setCurrentIndex(max(0, self.cb_tp.findData(s["multi_tp_mode"])))
        self._row(l3, "Segnali con più TP", "Con «una posizione per ogni TP» il lotto si applica a ciascuna posizione.", self.cb_tp)
        self.cb_nosl = QComboBox()
        for v, t in (("ignore", "Ignora il segnale"), ("default", "Usa uno SL predefinito"), ("none", "Apri senza SL")):
            self.cb_nosl.addItem(t, v)
        self.cb_nosl.setCurrentIndex(max(0, self.cb_nosl.findData(s["no_sl_mode"])))
        self._row(l3, "Segnali senza Stop Loss", None, self.cb_nosl)
        self.sp_slpts = QSpinBox()
        self.sp_slpts.setRange(1, 1000000)
        self.sp_slpts.setSuffix(" punti")
        self.sp_slpts.setValue(s["default_sl_points"])
        _clean_spin(self.sp_slpts)
        self.slpts_wrap = QWidget()
        sl_l = QVBoxLayout(self.slpts_wrap)
        sl_l.setContentsMargins(0, 0, 0, 0)
        self._row(sl_l, "SL predefinito", "Distanza dal prezzo di entrata. Forex: 10 punti = 1 pip. Oro: 100 punti = 1 dollaro.",
                  self.sp_slpts)
        l3.addWidget(self.slpts_wrap)
        self.cb_nosl.currentIndexChanged.connect(lambda _i: self.slpts_wrap.setVisible(self.cb_nosl.currentData() == "default"))
        self.slpts_wrap.setVisible(s["no_sl_mode"] == "default")
        lay.addWidget(c3)

        # gestione
        c4, l4 = _card(spacing=14)
        l4.addWidget(_label("GESTIONE", "section"))
        self.sw_be = Switch(s["be_on_tp1"])
        self._row(l4, "Pareggio dopo il primo TP", "Quando una posizione di un segnale chiude in TP, "
                  "lo Stop Loss delle altre va al prezzo di entrata.", self.sw_be)
        self.cb_tphit = QComboBox()
        for v, t in (("nothing", "Non fare nulla"), ("breakeven", "SL a pareggio"), ("partial", "Chiudi metà"),
                     ("partial_be", "Chiudi metà e SL a pareggio")):
            self.cb_tphit.addItem(t, v)
        self.cb_tphit.setCurrentIndex(max(0, self.cb_tphit.findData(s["tp_hit_action"])))
        self._row(l4, "Messaggio «TP raggiunto»", "Cosa fare quando il canale scrive che un TP è stato preso.", self.cb_tphit)
        lay.addWidget(c4)

        # orario
        c5, l5 = _card(spacing=14)
        l5.addWidget(_label("ORARIO", "section"))
        self.sw_hours = Switch(s["hours_enabled"])
        self._row(l5, "Opera solo in un orario", "Fuori orario i nuovi segnali vengono ignorati.", self.sw_hours)
        self.tm_start = QTimeEdit(QTime.fromString(s["hours_start"], "HH:mm"))
        self.tm_end = QTimeEdit(QTime.fromString(s["hours_end"], "HH:mm"))
        for tm in (self.tm_start, self.tm_end):
            tm.setDisplayFormat("HH:mm")
            tm.setFixedWidth(80)
            tm.setAlignment(Qt.AlignCenter)
        times = QHBoxLayout()
        times.addWidget(_label("dalle", "muted"))
        times.addWidget(self.tm_start)
        times.addWidget(_label("alle", "muted"))
        times.addWidget(self.tm_end)
        self.hours_wrap = QWidget()
        hw = QVBoxLayout(self.hours_wrap)
        hw.setContentsMargins(0, 0, 0, 0)
        self._row(hw, "Orario", "Ora del Mac.", times)
        l5.addWidget(self.hours_wrap)
        self.sw_hours.toggled.connect(self.hours_wrap.setVisible)
        self.hours_wrap.setVisible(s["hours_enabled"])
        lay.addWidget(c5)
        lay.addStretch(1)
        return w

    LOT_SPEC = {
        "fixed": ("Lotto per posizione", "Es. 0,01 = micro lotto.", 0.01, 100, 2, 0.01, ""),
        "risk_percent": ("Rischio per segnale", "Percentuale del saldo che si perde se tocca lo Stop Loss.", 0.1, 20, 1, 0.1, " %"),
        "risk_money": ("Rischio per segnale", "Importo nella valuta del conto che si perde se tocca lo Stop Loss.", 1, 1e7, 2, 1, ""),
    }

    def _apply_lot_mode(self, mode):
        _t, _d, lo, hi, dec, step, suf = self.LOT_SPEC[mode]
        self.sp_lot.setDecimals(dec)
        self.sp_lot.setRange(lo, hi)
        self.sp_lot.setSingleStep(step)
        self.sp_lot.setSuffix(suf)
        self.sp_lot.setValue(float(self._lot_vals[mode]))

    def _lot_mode_changed(self):
        self._lot_vals[self._lot_mode] = round(self.sp_lot.value(), 4)
        self._lot_mode = self.cb_lot_mode.currentData()
        self._apply_lot_mode(self._lot_mode)
        self._update_lot_labels()

    def _update_lot_labels(self):
        t, d, *_ = self.LOT_SPEC[self._lot_mode]
        self.lbl_lot_title.setText(t)
        self.lbl_lot_desc.setText(d)

    def trading_values(self) -> dict:
        """Valori della pagina Trading (solo queste chiavi vengono salvate)."""
        self._lot_vals[self._lot_mode] = round(self.sp_lot.value(), 4)
        maxlot, maxpos = round(self.sp_maxlot.value(), 2), self.sp_maxpos.value()
        return {
            "dry_run": self.sw_dry.isChecked(),
            "allow_real": self.sw_real.isChecked(),
            "lot_mode": self._lot_mode,
            "fixed_lot": self._lot_vals["fixed"],
            "risk_percent": self._lot_vals["risk_percent"],
            "risk_money": self._lot_vals["risk_money"],
            "max_lot_per_trade": maxlot,
            "max_open_trades": maxpos,
            "multi_tp_mode": self.cb_tp.currentData(),
            "no_sl_mode": self.cb_nosl.currentData(),
            "default_sl_points": self.sp_slpts.value(),
            "be_on_tp1": self.sw_be.isChecked(),
            "tp_hit_action": self.cb_tphit.currentData(),
            "hours_enabled": self.sw_hours.isChecked(),
            "hours_start": self.tm_start.time().toString("HH:mm"),
            "hours_end": self.tm_end.time().toString("HH:mm"),
        }

    # ------------------------------------------------------------ prova messaggio
    def _page_test(self):
        w, lay = self._page("Prova un messaggio", "Incolla un messaggio del canale: vedi come viene letto e cosa verrebbe "
                                                    "eseguito con le impostazioni attuali. Non viene inviato nulla.")
        c, cl = _card()
        self.test_in = QPlainTextEdit()
        self.test_in.setPlaceholderText("XAUUSD BUY 2350\nSL 2340\nTP1 2355\nTP2 2360")
        self.test_in.setMinimumHeight(120)
        self.test_in.setStyleSheet(f"background:{BG}; border:1px solid {BORDER}; border-radius:8px; padding:6px;")
        cl.addWidget(self.test_in)
        b = QHBoxLayout()
        b.addStretch(1)
        b.addWidget(_btn("Analizza", self.run_test, "primary"))
        cl.addLayout(b)
        self.test_out = _label("", wrap=True)
        self.test_out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cl.addWidget(self.test_out)
        lay.addWidget(c)
        lay.addStretch(1)
        return w

    def current_values(self) -> dict:
        vals = self.engine.settings.get()
        vals.update(self.trading_values())
        return vals

    def run_test(self):
        text = self.test_in.toPlainText().strip()
        if not text:
            return
        try:
            self.test_out.setText(self.engine.test_message(text, self.current_values()))
        except Exception as e:
            self.test_out.setText(f"Errore: {e}")

    # ------------------------------------------------------------ salva
    def save(self):
        vals = self.trading_values()
        if self.ed_api_id.text().strip():
            vals["tg_api_id"] = self.ed_api_id.text().strip()
            vals["tg_api_hash"] = self.ed_api_hash.text().strip()
        vals["tg_channels"] = self._checked_channels()
        vals["mt5_dir"] = self.ed_mt5_dir.text().strip()
        # limiti collegati, così non bloccano mai le scelte qui sopra
        if (vals["max_lot_per_trade"], vals["max_open_trades"]) != (self.s["max_lot_per_trade"], self.s["max_open_trades"]):
            vals["max_total_lots"] = max(self.s["max_total_lots"],
                                         round(vals["max_lot_per_trade"] * vals["max_open_trades"], 2))
            vals["max_trades_per_symbol"] = max(self.s["max_trades_per_symbol"], vals["max_open_trades"])
        if vals["allow_real"] and not self.s["allow_real"] and not vals["dry_run"]:
            if QMessageBox.question(self, "Conto reale", "Hai attivato le operazioni sul conto REALE. Confermi?") != QMessageBox.Yes:
                return
        if self.engine.tg_authorized and not vals["tg_channels"]:
            if QMessageBox.question(self, "Nessun canale",
                                    "Non hai attivato nessun canale: il programma non riceverà segnali.\n\n"
                                    "Chiudere lo stesso?") != QMessageBox.Yes:
                self.nav.setCurrentRow(0)
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
