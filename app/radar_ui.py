"""Finestra del Radar: classifica e dettaglio dei canali osservati (nessuna operazione aperta)."""
from __future__ import annotations

import asyncio
import datetime as dt

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QHeaderView, QListWidget, QListWidgetItem,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import chart, gui, radar

STATUS_COLOR = {"vinto": gui.GREEN, "perso": gui.RED, "pareggio": gui.MUTED, "aperto": gui.BLUE}


def _when(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%d/%m %H:%M") if t else "–"


def _pts(v):
    return f"{v:+,.0f}".replace(",", ".")


SYMBOL_CCY = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥", "CHF": "CHF", "AUD": "A$", "NZD": "NZ$", "CAD": "C$"}


def _money(v, ccy="USD"):
    """Importo in formato italiano: +1.234,56 €"""
    txt = f"{v:+,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{txt} {SYMBOL_CCY.get(ccy, ccy)}"


class EquityChart(QWidget):
    """Curva dei risultati cumulati (in punti)."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(130)
        self.points = []
        self.fmt = lambda v: f"{_pts(v)} punti"

    def set_points(self, pts):
        self.points = pts
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(8, 10, self.width() - 16, self.height() - 20)
        if len(self.points) < 2:
            p.setPen(QColor(gui.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "La curva compare quando ci sono almeno due segnali chiusi")
            return
        vals = [0.0] + [v for _t, v in self.points]
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            hi += 1
            lo -= 1
        pad = (hi - lo) * 0.1
        lo, hi = lo - pad, hi + pad

        def xy(i, v):
            return QPointF(r.left() + i / (len(vals) - 1) * r.width(), r.top() + (hi - v) / (hi - lo) * r.height())
        zero_y = xy(0, 0).y()
        p.setPen(QPen(QColor(gui.BORDER), 1, Qt.DashLine))
        p.drawLine(QPointF(r.left(), zero_y), QPointF(r.right(), zero_y))
        col = QColor(gui.GREEN if vals[-1] >= 0 else gui.RED)
        path = QPainterPath(xy(0, vals[0]))
        for i, v in enumerate(vals[1:], 1):
            path.lineTo(xy(i, v))
        fill = QPainterPath(path)
        fill.lineTo(QPointF(r.right(), zero_y))
        fill.lineTo(QPointF(r.left(), zero_y))
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        c1 = QColor(col)
        c1.setAlpha(60)
        c2 = QColor(col)
        c2.setAlpha(0)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        p.fillPath(fill, grad)
        p.setPen(QPen(col, 2))
        p.drawPath(path)
        p.setPen(QColor(gui.FAINT))
        p.drawText(QRectF(r.left(), r.top() - 8, 260, 16), Qt.AlignLeft, f"massimo {self.fmt(max(vals[1:]))}")
        p.drawText(QRectF(r.right() - 260, r.bottom() - 8, 260, 16), Qt.AlignRight, self.fmt(vals[-1]))


class _Progress(QObject):
    text = Signal(str)


class RadarWindow(QDialog):
    _instance = None

    @classmethod
    def show_for(cls, parent, engine):
        if cls._instance is None or not cls._instance.isVisible():
            cls._instance = RadarWindow(parent, engine)
        w = cls._instance
        w.reload_channels()
        w.show()
        w.raise_()
        w.activateWindow()
        return w

    def __init__(self, parent, engine):
        super().__init__(parent)
        self.engine = engine
        self.report: radar.Report | None = None
        self.running = False
        self.setWindowTitle("Radar")
        self.setStyleSheet(gui.STYLE + f"""
            QPushButton#seg {{ background: transparent; border: none; color: {gui.MUTED}; padding: 5px 10px; border-radius: 6px; }}
            QPushButton#seg:checked {{ background: {gui.SURFACE2}; color: {gui.TEXT}; }}""")
        self.setMinimumSize(900, 600)
        gui._fit_to_screen(self, 1200, 800)
        self._prog = _Progress()
        self._prog.text.connect(lambda t: self.lbl_progress.setText(t))

        # ---- sinistra: canali osservati
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(250)
        self.nav.currentRowChanged.connect(lambda _r: self.show_saved())
        left = QVBoxLayout()
        left.setContentsMargins(14, 18, 6, 14)
        left.addWidget(gui._label("Radar", "h1"))
        left.addWidget(gui._label("Come sarebbero andati i canali che osservi, con le tue impostazioni. "
                                  "Non apre nessuna operazione.", "muted", wrap=True))
        left.addSpacing(8)
        left.addWidget(self.nav, 1)

        # ---- destra: dettaglio
        self.lbl_name = gui._label("", "h2")
        self.cb_days = QComboBox()
        for d in (7, 30, 60, 90):
            self.cb_days.addItem(f"Ultimi {d} giorni", d)
        self.cb_days.setCurrentIndex(max(0, self.cb_days.findData(int(engine.settings["radar_days"]))))
        self.cb_days.setMinimumWidth(170)
        self.btn_run = gui._btn("Aggiorna", self.run_scan, "primary")
        self.sp_lot = QDoubleSpinBox()
        self.sp_lot.setRange(0.01, 100)
        self.sp_lot.setDecimals(2)
        self.sp_lot.setSingleStep(0.01)
        self.sp_lot.setValue(float(radar.prefs().get("lot", engine.settings["fixed_lot"])))
        gui._clean_spin(self.sp_lot, 90)
        self.sp_lot.setToolTip("Con quanti lotti faresti ogni operazione: la stima in denaro si aggiorna subito")
        self.sp_lot.valueChanged.connect(self._lot_changed)
        head = QHBoxLayout()
        head.addWidget(self.lbl_name, 1)
        head.addWidget(gui._label("Lotto", "muted"))
        head.addWidget(self.sp_lot)
        head.addSpacing(8)
        head.addWidget(self.cb_days)
        head.addWidget(self.btn_run)
        self.lbl_progress = gui._label("", "muted", wrap=True)

        self.pills = [gui._label("", "pill") for _ in range(7)]
        pills = QHBoxLayout()
        pills.setSpacing(8)
        for x in self.pills:
            pills.addWidget(x)
        pills.addStretch(1)

        eq_card, eq = gui._card(margins=10)
        eq.addWidget(gui._label("RISULTATO NEL TEMPO", "section"))
        self.equity = EquityChart()
        eq.addWidget(self.equity)

        tab_row = QHBoxLayout()
        self.btn_sig = QPushButton("Segnali")
        self.btn_msg = QPushButton("Messaggi del canale")
        for i, b in enumerate((self.btn_sig, self.btn_msg)):
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c, k=i: self._tab(k))
            tab_row.addWidget(b)
        tab_row.addStretch(1)
        tab_row.addWidget(gui._label("Doppio clic su un segnale: grafico", "muted"))
        self.btn_sig.setChecked(True)

        self.t_sig = self._table(["DATA", "SIMBOLO", "TIPO", "ENTRATA", "SL", "TP", "ESITO", "RISULTATO"])
        self.t_sig.cellDoubleClicked.connect(self.open_signal_chart)
        self.t_msg = self._table(["DATA", "MESSAGGIO", "COME È STATO LETTO", "NOTA"])
        self.t_msg.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.t_msg.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tabs = QStackedWidget()
        self.tabs.addWidget(self.t_sig)
        self.tabs.addWidget(self.t_msg)
        list_card, lc = gui._card(margins=10)
        lc.addLayout(tab_row)
        lc.addWidget(self.tabs, 1)

        self.empty = gui._label("", "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)

        self.detail = QWidget()
        d = QVBoxLayout(self.detail)
        d.setContentsMargins(0, 0, 0, 0)
        d.setSpacing(10)
        d.addLayout(head)
        d.addWidget(self.lbl_progress)
        d.addLayout(pills)
        d.addWidget(eq_card)
        d.addWidget(list_card, 1)

        right = QVBoxLayout()
        right.setContentsMargins(6, 16, 18, 14)
        right.addWidget(self.detail, 1)
        right.addWidget(self.empty, 1)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(left)
        root.addLayout(right, 1)

    def _table(self, headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        t.setFocusPolicy(Qt.NoFocus)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setWordWrap(False)
        return t

    def _tab(self, k):
        self.tabs.setCurrentIndex(k)
        self.btn_sig.setChecked(k == 0)
        self.btn_msg.setChecked(k == 1)

    # ---------------------------------------------------------------- canali
    def reload_channels(self):
        chans = self.engine.settings["radar_channels"]
        cur = self.nav.currentItem().data(Qt.UserRole) if self.nav.currentItem() else None
        self.nav.blockSignals(True)
        self.nav.clear()
        for c in chans:
            rep = radar.load_report(int(c["id"]))
            sub = "non ancora analizzato"
            if rep and rep.stats:
                st, _eq = self._stats(rep)
                wr = st.get("winrate")
                res = _money(st["money"], rep.currency) if st["in_money"] else f"{_pts(st['points'])} punti"
                sub = res + (f"  ·  {wr:.0f}% vinti" if wr is not None else "")
            it = QListWidgetItem(f"{c['name']}\n{sub}")
            it.setData(Qt.UserRole, int(c["id"]))
            it.setData(Qt.UserRole + 1, c["name"])
            self.nav.addItem(it)
        self.nav.blockSignals(False)
        if not chans:
            self.detail.hide()
            self.empty.show()
            self.empty.setText("Nessun canale nel Radar.\n\nVai in Impostazioni → Telegram e accendi l'interruttore "
                               "«Radar» accanto ai canali che vuoi osservare.")
            return
        self.detail.show()
        self.empty.hide()
        idx = next((i for i in range(self.nav.count()) if self.nav.item(i).data(Qt.UserRole) == cur), 0)
        self.nav.setCurrentRow(idx)
        self.show_saved()

    def _stats(self, rep):
        return radar.summarize(rep.signals, rep.messages, rep.stats.get("duplicates", 0), self.sp_lot.value(),
                               getattr(rep, "usd_to_acc", None))

    def _lot_changed(self, v):
        radar.save_prefs(lot=round(v, 2))
        if self.report:
            self._fill(self.report)
        for i in range(self.nav.count()):
            it = self.nav.item(i)
            rep = radar.load_report(it.data(Qt.UserRole))
            if rep and rep.stats:
                st, _eq = self._stats(rep)
                wr = st.get("winrate")
                res = _money(st["money"], rep.currency) if st["in_money"] else f"{_pts(st['points'])} punti"
                it.setText(f"{it.data(Qt.UserRole + 1)}\n{res}" + (f"  ·  {wr:.0f}% vinti" if wr is not None else ""))

    def current(self):
        it = self.nav.currentItem()
        return (it.data(Qt.UserRole), it.data(Qt.UserRole + 1)) if it else (None, None)

    def show_saved(self):
        cid, name = self.current()
        if cid is None:
            return
        self.lbl_name.setText(name)
        rep = radar.load_report(cid)
        if rep is None:
            self.report = None
            self._fill(None)
            self.lbl_progress.setText("Premi «Aggiorna» per analizzare il canale. Rilegge i messaggi passati: "
                                      "non serve che il computer sia rimasto acceso.")
            return
        self._fill(rep)
        spreads = ", ".join(f"{k} {v} punti" for k, v in (rep.spreads or {}).items())
        self.lbl_progress.setText(f"Analisi del {rep.generated} · ultimi {rep.days} giorni"
                                  + (f" · spread considerato: {spreads}" if spreads else "")
                                  + (f" · {rep.note}" if rep.note else "")
                                  + (f" · {rep.error}" if rep.error else ""))

    # ---------------------------------------------------------------- analisi
    def run_scan(self):
        cid, name = self.current()
        if cid is None or self.running:
            return
        days = self.cb_days.currentData()
        self.engine.settings.update({"radar_days": days})
        self.running = True
        self.btn_run.setEnabled(False)
        self.lbl_progress.setText("Inizio l'analisi…")
        fut = asyncio.run_coroutine_threadsafe(
            radar.scan(self.engine, cid, name, days, progress=self._prog.text.emit), self.engine.loop)
        poll = QTimer(self)

        def check():
            if not fut.done():
                return
            poll.stop()
            poll.deleteLater()
            self.running = False
            self.btn_run.setEnabled(True)
            try:
                rep = fut.result()
            except Exception as e:
                self.lbl_progress.setText(f"Analisi non riuscita: {e}")
                return
            if rep.error:
                self.lbl_progress.setText(rep.error)
                return
            self.reload_channels()
        poll.timeout.connect(check)
        poll.start(100)

    def _fill(self, rep):
        self.report = rep
        st = rep.stats if rep else {}
        if not st:
            for x in self.pills:
                x.hide()
            self.equity.set_points([])
            self.t_sig.setRowCount(0)
            self.t_msg.setRowCount(0)
            return
        st, equity = self._stats(rep)
        lot = self.sp_lot.value()
        ccy = rep.currency
        wr = st.get("winrate")
        if st["in_money"]:
            col = gui.GREEN if st["money"] >= 0 else gui.RED
            result = f'Risultato <span style="color:{col}">{_money(st["money"], ccy)}</span>'
            if st["single_symbol"]:
                result += f"  ({_pts(st['points'])} punti)"
            drawdown = f"Calo massimo {_money(-st['max_drawdown'], ccy)}"
            self.equity.fmt = lambda v: _money(v, ccy)
        else:
            col = gui.GREEN if st["points"] >= 0 else gui.RED
            result = f'Risultato <span style="color:{col}">{_pts(st["points"])} punti</span>'
            drawdown = f"Calo massimo {_pts(-st['max_drawdown'])} punti"
            self.equity.fmt = lambda v: f"{_pts(v)} punti"
        texts = [
            f"Segnali {st['executed']}" + (f" / {st['signals']}" if st["signals"] != st["executed"] else ""),
            f"Vinti {wr:.0f}%" if wr is not None else "Vinti –",
            result,
            f"Serie peggiore {st['worst_streak']}",
            drawdown,
            f"Doppioni {st['duplicates']}",
            f"Modificati {st['edited']}",
        ]
        for x, t in zip(self.pills, texts):
            x.setText(t)
            x.show()
        self.pills[6].setToolTip("Messaggi che il canale ha modificato dopo averli inviati")
        self.pills[5].setToolTip("Messaggi ripetuti ignorati (come fa la protezione dai doppi)")
        self.pills[2].setToolTip(f"Con {lot:g} lotti per operazione" + ("" if st["in_money"] else
                                 " · importo non stimabile per questi strumenti: risultato in punti"))
        self.equity.set_points(equity)

        sigs = sorted(rep.signals, key=lambda s: -s.t)
        self.t_sig.setRowCount(len(sigs))
        for r, s in enumerate(sigs):
            entry = s.entry_p if s.entry_p is not None else s.entry
            vals = [_when(s.t), s.symbol, f"{s.side.upper()}{'' if s.otype == 'market' else ' ' + s.otype.upper()}",
                    f"{entry:g}" if entry is not None else "mercato", f"{s.sl:g}" if s.sl else "–",
                    ", ".join(f"{t:g}" for t in s.tps) or "–", s.status + ("  ✎" if s.edited else ""),
                    (_pts(s.points) + (f"  ({_money(s.points * s.vpp * lot * (rep.usd_to_acc or 1), ccy if rep.usd_to_acc else 'USD')})"
                                       if s.vpp is not None else ""))
                    if s.status in STATUS_COLOR else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setForeground(QColor(gui.GREEN if s.side == "buy" else gui.RED))
                if c in (6, 7):
                    it.setForeground(QColor(STATUS_COLOR.get(s.status, gui.FAINT)))
                if c == 0:
                    it.setData(Qt.UserRole, s.msg_id)
                    it.setToolTip(s.text)
                if c == 6 and s.edited:
                    it.setToolTip("Il canale ha modificato questo segnale dopo averlo inviato: "
                                  "la stima usa la versione finale")
                self.t_sig.setItem(r, c, it)

        msgs = sorted(rep.messages, key=lambda m: -m.t)
        self.t_msg.setRowCount(len(msgs))
        for r, m in enumerate(msgs):
            first = m.text.strip().splitlines()[0] if m.text.strip() else ""
            vals = [_when(m.t), first + ("  ✎" if m.edited else ""), m.reading, m.note]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 1:
                    it.setToolTip(m.text + ("\n\n(messaggio modificato dal canale)" if m.edited else ""))
                if c == 3 and "doppio" in v:
                    it.setForeground(QColor(gui.AMBER))
                if c == 2:
                    it.setForeground(QColor(gui.BLUE if v.startswith("SEGNALE") else gui.MUTED))
                self.t_msg.setItem(r, c, it)

    # ---------------------------------------------------------------- grafico di un segnale
    def open_signal_chart(self, row, _col=0):
        if not self.report:
            return
        mid = self.t_sig.item(row, 0).data(Qt.UserRole)
        sig = next((s for s in self.report.signals if s.msg_id == mid), None)
        if sig is None or sig.entry_t is None:
            self.lbl_progress.setText("Questo segnale non è stato eseguito: niente da mostrare sul grafico.")
            return
        RadarTradeChart(self, self.engine, sig).show()


class RadarTradeChart(QDialog):
    """Grafico di un segnale simulato, nello stesso stile del grafico delle operazioni."""

    def __init__(self, parent, engine, sig: radar.RSignal):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle(f"Radar · {sig.symbol} {_when(sig.t)}")
        self.setStyleSheet(gui.STYLE)
        gui._fit_to_screen(self, 1100, 700)
        self.trade = chart.Trade(key=sig.msg_id, symbol=sig.symbol, side=sig.side, volume=0, entry_time=sig.entry_t,
                                 entry_price=sig.entry_p, exit_time=None if sig.status == "aperto" else sig.exit_t,
                                 exit_price=None if sig.status == "aperto" else sig.exit_p, profit=sig.points,
                                 sl=sig.sl_now or sig.sl or 0, tp=sig.tp_first or 0, is_open=sig.status == "aperto",
                                 exits=list(sig.exits), unit="punti")
        col = gui.GREEN if sig.points >= 0 else gui.RED
        info = QHBoxLayout()
        for t in (f'<span style="color:{gui.GREEN if sig.side == "buy" else gui.RED}">'
                  f'{"▲ BUY" if sig.side == "buy" else "▼ SELL"}</span>&nbsp; {sig.symbol}',
                  f"Entrata {sig.entry_p:g}", f"Uscita {sig.exit_p:g}" if sig.exit_p else "Aperta",
                  f'Risultato <span style="color:{col}">{_pts(sig.points)} punti</span>', f"Simulato · {sig.status}"):
            info.addWidget(gui._label(t, "pill"))
        info.addStretch(1)
        card, cl = gui._card(margins=8)
        self.chart = chart.CandleChart()
        cl.addWidget(self.chart)
        msg = gui._label("Messaggio del canale: " + sig.text.replace("\n", "  ·  "), "muted", wrap=True)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.addLayout(info)
        root.addWidget(card, 1)
        root.addWidget(msg)
        end = (sig.exit_t or sig.entry_t)
        tf = chart.auto_timeframe(max(end - sig.entry_t, 60) * 2)
        start, stop = chart.rates_range(self.trade, tf, end + 3600)
        if self.trade.is_open:
            stop = 0
        cmd = {"action": "RATES", "symbol": sig.symbol, "tf": tf, "from": max(0, start), "to": stop}
        if engine.bridge is None or not engine.mt5_connected():
            self.chart.set_message("MetaTrader non è collegato: aprilo per vedere il grafico.")
            return
        fut = asyncio.run_coroutine_threadsafe(chart.fetch(engine.bridge, cmd), engine.loop)
        poll = QTimer(self)

        def check():
            if not fut.done():
                return
            poll.stop()
            try:
                text, err, _now = fut.result()
            except Exception as e:
                self.chart.set_message(f"Errore: {e}")
                return
            if err:
                self.chart.set_message(err)
                return
            self.chart.set_data(chart.parse_rates(text or ""), self.trade, tf)
        poll.timeout.connect(check)
        poll.start(80)

