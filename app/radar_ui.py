"""Finestra del Radar: classifica e dettaglio dei canali osservati (nessuna operazione aperta)."""
from __future__ import annotations

import asyncio
import datetime as dt

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
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


OUTCOME_COLOR = {"TP": gui.GREEN, "TP + pareggio": gui.GREEN, "SL": gui.RED, "Pareggio": gui.MUTED,
                 "Chiuso dal canale": gui.BLUE, "TP + SL": gui.AMBER, "Aperto": gui.BLUE, "Chiuso": gui.MUTED}
OUTCOME_ICON = {"TP": "✓", "TP + pareggio": "✓", "SL": "✗", "Pareggio": "=", "Chiuso dal canale": "■",
                "TP + SL": "±", "Aperto": "●", "Chiuso": "■"}


def _outcome(sig):
    """Esito leggibile di un segnale (anche per i report salvati prima di questa versione)."""
    if sig.exit_reason:
        return sig.exit_reason
    return {"vinto": "TP", "perso": "SL", "pareggio": "Pareggio", "aperto": "Aperto"}.get(sig.status, "")


# ============================================================================ elementi grafici
class ChannelItem(QWidget):
    """Riga di un canale nell'elenco a sinistra: nome e guadagno accanto."""

    def __init__(self, name):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        left = QVBoxLayout()
        left.setSpacing(1)
        self.lbl_name = gui._label(name)
        self.lbl_name.setTextFormat(Qt.PlainText)
        self.lbl_sub = gui._label("", "muted")
        left.addWidget(self.lbl_name)
        left.addWidget(self.lbl_sub)
        self.lbl_gain = gui._label("")
        self.lbl_gain.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(left, 1)
        lay.addWidget(self.lbl_gain)

    def set_result(self, gain_text, color, sub):
        self.lbl_gain.setText(f'<span style="color:{color}; font-weight:600">{gain_text}</span>')
        self.lbl_sub.setText(sub)


class MiniChart(QWidget):
    """Miniatura del grafico di un segnale: prezzo, entrata, uscita, SL e TP."""

    def __init__(self, sig, on_click=None):
        super().__init__()
        self.sig = sig
        self.on_click = on_click
        self.setFixedSize(190, 92)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Clicca per aprire il grafico")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.on_click:
            self.on_click(self.sig)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QPainterPath()
        box.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 8, 8)
        p.fillPath(box, QColor(gui.BG))
        p.setPen(QPen(QColor(gui.BORDER), 1))
        p.drawPath(box)
        sp = self.sig.spark
        if len(sp) < 2:
            p.setPen(QColor(gui.FAINT))
            p.drawText(self.rect(), Qt.AlignCenter, "grafico non disponibile")
            return
        r = QRectF(8, 8, self.width() - 16, self.height() - 16)
        t0, t1 = sp[0][0], sp[-1][0]
        lo = min(x[3] if len(x) > 3 else x[1] for x in sp)
        hi = max(x[2] if len(x) > 2 else x[1] for x in sp)
        sig = self.sig
        for v in (sig.entry_p, sig.exit_p):
            if v:
                lo, hi = min(lo, v), max(hi, v)
        span = (hi - lo) or abs(hi) * 0.001 or 1
        lo, hi = lo - span * 0.12, hi + span * 0.12

        def X(t):
            return r.left() + (t - t0) / max(t1 - t0, 1) * r.width()

        def Y(v):
            return r.top() + (hi - v) / (hi - lo) * r.height()
        win = sig.points >= 0
        res_col = QColor(gui.GREEN if win else gui.RED)
        if sig.entry_t:
            xe = X(sig.entry_t)
            xo = X(sig.exit_t) if sig.exit_t else r.right()
            band = QColor(res_col)
            band.setAlpha(28)
            p.fillRect(QRectF(min(xe, xo), r.top(), max(2, abs(xo - xe)), r.height()), band)
        for lv, col in ((sig.sl_now or sig.sl, gui.RED), (sig.tp_first, gui.GREEN)):
            if lv and lo <= lv <= hi:
                c = QColor(col)
                c.setAlpha(170)
                p.setPen(QPen(c, 1, Qt.DashLine))
                p.drawLine(QPointF(r.left(), Y(lv)), QPointF(r.right(), Y(lv)))
        path = QPainterPath(QPointF(X(sp[0][0]), Y(sp[0][1])))
        for x in sp[1:]:
            path.lineTo(QPointF(X(x[0]), Y(x[1])))
        p.setPen(QPen(QColor(gui.TEXT), 1.3))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        if sig.entry_t and sig.entry_p:
            xe, ye = X(sig.entry_t), Y(sig.entry_p)
            buy = sig.side == "buy"
            col = QColor(gui.GREEN if buy else gui.RED)
            s6 = 6
            tri = ([QPointF(xe, ye + 2), QPointF(xe - s6, ye + 2 + s6 * 1.4), QPointF(xe + s6, ye + 2 + s6 * 1.4)] if buy else
                   [QPointF(xe, ye - 2), QPointF(xe - s6, ye - 2 - s6 * 1.4), QPointF(xe + s6, ye - 2 - s6 * 1.4)])
            from PySide6.QtGui import QPolygonF
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawPolygon(QPolygonF(tri))
            if sig.exit_t and sig.exit_p and sig.status != "aperto":
                xo, yo = X(sig.exit_t), Y(sig.exit_p)
                line = QColor(res_col)
                line.setAlpha(200)
                p.setPen(QPen(line, 1.2, Qt.DashLine))
                p.drawLine(QPointF(xe, ye), QPointF(xo, yo))
                p.setPen(QPen(res_col, 2))
                p.setBrush(QColor(gui.BG))
                p.drawEllipse(QPointF(xo, yo), 4, 4)


class MessageCard(QFrame):
    """Un messaggio del canale: data e ora, testo, come è stato letto e, per i segnali, miniatura ed esito."""

    def __init__(self, msg, sig, money_text, on_chart):
        super().__init__()
        self.setObjectName("msgcard")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(4)
        head = QHBoxLayout()
        when = dt.datetime.fromtimestamp(msg.t, dt.timezone.utc)
        head.addWidget(gui._label(f"<b>{when.strftime('%d/%m/%Y')}</b>&nbsp; {when.strftime('%H:%M')}"))
        tag, tcol = {"segnale": ("SEGNALE", gui.BLUE), "gestione": ("GESTIONE", gui.MUTED),
                     "doppio: ignorato": ("DOPPIO · IGNORATO", gui.AMBER)}.get(msg.note, ("", gui.MUTED))
        if tag:
            head.addWidget(gui._label(f'<span style="color:{tcol}; font-size:11px; font-weight:600">{tag}</span>'))
        if msg.edited:
            head.addWidget(gui._label(f'<span style="color:{gui.AMBER}; font-size:11px">✎ modificato dal canale</span>'))
        head.addStretch(1)
        left.addLayout(head)
        text = gui._label(msg.text.strip()[:600], wrap=True)
        text.setTextFormat(Qt.PlainText)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        left.addWidget(text)
        if msg.note not in ("doppio: ignorato",):
            left.addWidget(gui._label(msg.reading, "muted", wrap=True))
        lay.addLayout(left, 1)
        if sig is not None:
            right = QVBoxLayout()
            right.setSpacing(4)
            if sig.entry_t:
                right.addWidget(MiniChart(sig, on_chart))
                out = _outcome(sig)
                col = OUTCOME_COLOR.get(out, gui.MUTED)
                res = f"{OUTCOME_ICON.get(out, '')} {out}"
                detail = f"{_pts(sig.points)} pt" + (f" · {money_text}" if money_text else "")
                lbl = gui._label(f'<span style="color:{col}; font-weight:600">{res}</span>&nbsp; '
                                 f'<span style="color:{col}">{detail}</span>')
                lbl.setAlignment(Qt.AlignCenter)
                right.addWidget(lbl)
            else:
                why = sig.status.replace("ignorato: ", "Non copiato: ") if sig.status else ""
                lbl = gui._label(why, "muted", wrap=True)
                lbl.setFixedWidth(190)
                right.addWidget(lbl)
            right.addStretch(1)
            lay.addLayout(right)


class _Progress(QObject):
    text = Signal(str)


# ============================================================================ impostazioni del Radar
class RadarSettingsDialog(QDialog):
    """Menu a parte con le impostazioni usate solo dal Radar (la copia vera non cambia)."""

    def __init__(self, parent, engine):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Impostazioni del Radar")
        self.setStyleSheet(gui.STYLE)
        self.setMinimumSize(620, 520)
        gui._fit_to_screen(self, 760, 760)
        s = radar.effective_settings(engine.settings.get())
        pr = radar.prefs()

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 8, 4)
        lay.setSpacing(12)
        lay.addWidget(gui._label("Impostazioni del Radar", "h1"))
        lay.addWidget(gui._label("Valgono solo per le stime del Radar: puoi provare scelte diverse senza cambiare "
                                 "la copia vera.", "muted", wrap=True))

        c1, l1 = gui._card(spacing=14)
        l1.addWidget(gui._label("STIMA", "section"))
        self.sp_lot = self._dspin(0.01, 100, 2, 0.01, float(pr.get("lot", engine.settings["fixed_lot"])))
        self._row(l1, "Lotto per operazione", "Con quanti lotti faresti ogni operazione.", self.sp_lot)
        self.sp_comm = self._dspin(0, 1000, 2, 0.5, float(pr.get("commission", 0.0)))
        self._row(l1, "Commissione per lotto", "Andata e ritorno, nella valuta del conto. 0 per i conti senza "
                  "commissioni (Standard).", self.sp_comm)
        lay.addWidget(c1)

        c2, l2 = gui._card(spacing=14)
        l2.addWidget(gui._label("SEGNALI", "section"))
        self.cb_tp = self._combo([("split", "Una posizione per ogni TP"), ("first", "Solo il primo TP"),
                                  ("last", "Solo l'ultimo TP")], s["multi_tp_mode"])
        self._row(l2, "Segnali con più TP", None, self.cb_tp)
        self.cb_nosl = self._combo([("ignore", "Ignora il segnale"), ("default", "Usa uno SL predefinito"),
                                    ("none", "Apri senza SL")], s["no_sl_mode"])
        self._row(l2, "Segnali senza Stop Loss", None, self.cb_nosl)
        self.sp_slpts = self._ispin(1, 1000000, s["default_sl_points"], " punti")
        self._row(l2, "SL predefinito", "Usato se scegli «Usa uno SL predefinito».", self.sp_slpts)
        self.sw_dup = gui.Switch(s["ignore_duplicates"])
        self._row(l2, "Ignora i messaggi doppi", "Lo stesso segnale ripetuto conta una volta sola.", self.sw_dup)
        self.sp_dup = self._ispin(1, 1440, s["duplicate_window_min"], " minuti")
        self._row(l2, "Considera doppi entro", None, self.sp_dup)
        lay.addWidget(c2)

        c3, l3 = gui._card(spacing=14)
        l3.addWidget(gui._label("PAREGGIO", "section"))
        self.sw_follow_be = gui.Switch(s["follow_breakeven"])
        self._row(l3, "Segui il pareggio del canale", "Quando il canale scrive «SL a pareggio», lo Stop Loss va "
                  "all'entrata.", self.sw_follow_be)
        self.sw_be_tp1 = gui.Switch(s["be_on_tp1"])
        self._row(l3, "Pareggio dopo il primo TP", "Quando una posizione chiude in TP, le altre vanno a pareggio.",
                  self.sw_be_tp1)
        self.sp_be_after = self._ispin(0, 1000000, s["be_after_points"], " punti")
        self._row(l3, "Pareggio automatico dopo", "Quando guadagna questi punti va a pareggio da solo. 0 = no.",
                  self.sp_be_after)
        self.sp_be_off = self._ispin(-10000, 10000, s["be_offset_points"], " punti")
        self._row(l3, "Margine del pareggio", "Entrata più questi punti.", self.sp_be_off)
        lay.addWidget(c3)

        c4, l4 = gui._card(spacing=14)
        l4.addWidget(gui._label("GESTIONE", "section"))
        self.cb_tphit = self._combo([("nothing", "Non fare nulla"), ("breakeven", "SL a pareggio"),
                                     ("partial", "Chiudi metà"), ("partial_be", "Chiudi metà e SL a pareggio")],
                                    s["tp_hit_action"])
        self._row(l4, "Messaggio «TP raggiunto»", None, self.cb_tphit)
        lay.addWidget(c4)
        lay.addStretch(1)

        from PySide6.QtWidgets import QScrollArea
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(body)
        bottom = QHBoxLayout()
        bottom.addWidget(gui._btn("Come il copiatore", self.like_copier))
        bottom.addStretch(1)
        bottom.addWidget(gui._btn("Annulla", self.reject))
        bottom.addWidget(gui._btn("Salva", self.save, "primary"))
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.addWidget(sc, 1)
        root.addLayout(bottom)

    def _row(self, lay, title, desc, control):
        r = QHBoxLayout()
        r.setSpacing(16)
        t = QVBoxLayout()
        t.setSpacing(2)
        t.addWidget(gui._label(title))
        if desc:
            t.addWidget(gui._label(desc, "muted", wrap=True))
        r.addLayout(t, 1)
        r.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(r)

    def _dspin(self, lo, hi, dec, step, val):
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(dec)
        w.setSingleStep(step)
        w.setValue(val)
        return gui._clean_spin(w)

    def _ispin(self, lo, hi, val, suffix):
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setSuffix(suffix)
        w.setValue(int(val))
        return gui._clean_spin(w)

    def _combo(self, opts, val):
        w = QComboBox()
        for v, t in opts:
            w.addItem(t, v)
        w.setCurrentIndex(max(0, w.findData(val)))
        return w

    def values(self) -> dict:
        return {"multi_tp_mode": self.cb_tp.currentData(), "no_sl_mode": self.cb_nosl.currentData(),
                "default_sl_points": self.sp_slpts.value(), "ignore_duplicates": self.sw_dup.isChecked(),
                "duplicate_window_min": self.sp_dup.value(), "follow_breakeven": self.sw_follow_be.isChecked(),
                "be_on_tp1": self.sw_be_tp1.isChecked(), "be_after_points": self.sp_be_after.value(),
                "be_offset_points": self.sp_be_off.value(), "tp_hit_action": self.cb_tphit.currentData()}

    def like_copier(self):
        c = self.engine.settings.get()
        for combo, key in ((self.cb_tp, "multi_tp_mode"), (self.cb_nosl, "no_sl_mode"), (self.cb_tphit, "tp_hit_action")):
            combo.setCurrentIndex(max(0, combo.findData(c[key])))
        self.sp_slpts.setValue(c["default_sl_points"])
        self.sw_dup.setChecked(c["ignore_duplicates"])
        self.sp_dup.setValue(c["duplicate_window_min"])
        self.sw_follow_be.setChecked(c["follow_breakeven"])
        self.sw_be_tp1.setChecked(c["be_on_tp1"])
        self.sp_be_after.setValue(c["be_after_points"])
        self.sp_be_off.setValue(c["be_offset_points"])
        self.sp_lot.setValue(c["fixed_lot"])

    def save(self):
        radar.save_prefs(lot=round(self.sp_lot.value(), 2), commission=round(self.sp_comm.value(), 2),
                         settings=self.values())
        self.accept()


# ============================================================================ finestra
class RadarWindow(QDialog):
    _instance = None
    PAGE = 150          # messaggi mostrati alla volta

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
        self.shown = self.PAGE
        self.setWindowTitle("Radar")
        self.setStyleSheet(gui.STYLE + f"""
            QPushButton#seg {{ background: transparent; border: none; color: {gui.MUTED}; padding: 5px 10px; border-radius: 6px; }}
            QPushButton#seg:checked {{ background: {gui.SURFACE2}; color: {gui.TEXT}; }}
            QFrame#msgcard {{ background: {gui.SURFACE}; border: 1px solid {gui.BORDER}; border-radius: 10px; }}
            QListWidget#nav::item {{ padding: 0; }}""")
        self.setMinimumSize(980, 640)
        gui._fit_to_screen(self, 1240, 840)
        self._prog = _Progress()
        self._prog.text.connect(lambda t: self.lbl_progress.setText(t))

        # ---- sinistra: canali osservati, ognuno con il suo guadagno
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(280)
        self.nav.currentRowChanged.connect(lambda _r: self.show_saved())
        left = QVBoxLayout()
        left.setContentsMargins(14, 18, 6, 14)
        left.addWidget(gui._label("Radar", "h1"))
        left.addWidget(gui._label("Come sarebbero andati i canali che osservi. Non apre nessuna operazione.",
                                  "muted", wrap=True))
        left.addSpacing(8)
        left.addWidget(self.nav, 1)
        self.lbl_lot = gui._label("", "muted", wrap=True)
        left.addWidget(self.lbl_lot)
        left.addWidget(gui._btn("Impostazioni del Radar", self.open_settings))

        # ---- destra: dettaglio del canale
        self.lbl_name = gui._label("", "h2")
        self.cb_days = QComboBox()
        for d in (7, 30, 60, 90):
            self.cb_days.addItem(f"Ultimi {d} giorni", d)
        self.cb_days.setCurrentIndex(max(0, self.cb_days.findData(int(engine.settings["radar_days"]))))
        self.cb_days.setMinimumWidth(170)
        self.btn_run = gui._btn("Aggiorna", self.run_scan, "primary")
        head = QHBoxLayout()
        head.addWidget(self.lbl_name, 1)
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
        self.equity.setMinimumHeight(100)
        self.equity.setMaximumHeight(130)
        eq.addWidget(self.equity)

        tab_row = QHBoxLayout()
        self.btn_msg = QPushButton("Messaggi")
        self.btn_sig = QPushButton("Tabella dei segnali")
        for i, b in enumerate((self.btn_msg, self.btn_sig)):
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c, k=i: self._tab(k))
            tab_row.addWidget(b)
        tab_row.addStretch(1)
        tab_row.addWidget(gui._label("Clicca una miniatura per il grafico grande", "muted"))
        self.btn_msg.setChecked(True)

        self.feed = QScrollArea()
        self.feed.setWidgetResizable(True)
        self.feed_box = QWidget()
        self.feed_lay = QVBoxLayout(self.feed_box)
        self.feed_lay.setContentsMargins(0, 0, 6, 0)
        self.feed_lay.setSpacing(8)
        self.feed_lay.addStretch(1)
        self.feed.setWidget(self.feed_box)
        self.t_sig = self._table(["DATA", "SIMBOLO", "TIPO", "ENTRATA", "SL", "TP", "ESITO", "RISULTATO"])
        self.t_sig.cellDoubleClicked.connect(self.open_signal_chart)
        self.tabs = QStackedWidget()
        self.tabs.addWidget(self.feed)
        self.tabs.addWidget(self.t_sig)
        list_box = QVBoxLayout()
        list_box.addLayout(tab_row)
        list_box.addWidget(self.tabs, 1)

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
        d.addLayout(list_box, 1)

        right = QVBoxLayout()
        right.setContentsMargins(6, 16, 18, 14)
        right.addWidget(self.detail, 1)
        right.addWidget(self.empty, 1)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(left)
        root.addLayout(right, 1)

    # ---------------------------------------------------------------- utilità
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
        self.btn_msg.setChecked(k == 0)
        self.btn_sig.setChecked(k == 1)

    def lot(self):
        return float(radar.prefs().get("lot", self.engine.settings["fixed_lot"]))

    def commission(self):
        return float(radar.prefs().get("commission", 0.0))

    def _stats(self, rep):
        return radar.summarize(rep.signals, rep.messages, rep.stats.get("duplicates", 0), self.lot(),
                               getattr(rep, "usd_to_acc", None), self.commission())

    def _money_text(self, sig, rep):
        mv = radar.money_of(sig, self.lot(), rep.usd_to_acc, self.commission())
        return _money(mv, rep.currency if (rep.usd_to_acc or rep.precise) else "USD") if mv is not None else ""

    # ---------------------------------------------------------------- impostazioni
    def open_settings(self):
        before = radar.settings_sig(radar.effective_settings(self.engine.settings.get()))
        dlg = RadarSettingsDialog(self, self.engine)
        if dlg.exec() != QDialog.Accepted:
            return
        after = radar.settings_sig(radar.effective_settings(self.engine.settings.get()))
        self.reload_channels()
        if before != after and self.current()[0] is not None:
            self.run_scan()        # le scelte sulla simulazione richiedono di ricalcolare

    # ---------------------------------------------------------------- canali
    def reload_channels(self):
        chans = self.engine.settings["radar_channels"]
        cur = self.nav.currentItem().data(Qt.UserRole) if self.nav.currentItem() else None
        self.nav.blockSignals(True)
        self.nav.clear()
        for c in chans:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, int(c["id"]))
            it.setData(Qt.UserRole + 1, c["name"])
            w = ChannelItem(c["name"])
            rep = radar.load_report(int(c["id"]))
            if rep and rep.stats:
                st, _eq = self._stats(rep)
                wr = st.get("winrate")
                if st["in_money"]:
                    gain, col = _money(st["money"], rep.currency), gui.GREEN if st["money"] >= 0 else gui.RED
                else:
                    gain, col = f"{_pts(st['points'])} pt", gui.GREEN if st["points"] >= 0 else gui.RED
                w.set_result(gain, col, f"{st['executed']} segnali" + (f" · {wr:.0f}% vinti" if wr is not None else ""))
            else:
                w.set_result("–", gui.FAINT, "non ancora analizzato")
            it.setSizeHint(QSize(240, 60))
            self.nav.addItem(it)
            self.nav.setItemWidget(it, w)
        self.nav.blockSignals(False)
        self.lbl_lot.setText(f"Stima con {self.lot():g} lotti" + (f", commissione {self.commission():g}"
                                                                  if self.commission() else ""))
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

    def channel_gain_text(self, row):
        w = self.nav.itemWidget(self.nav.item(row))
        return w.lbl_gain.text() if w else ""

    def current(self):
        it = self.nav.currentItem()
        return (it.data(Qt.UserRole), it.data(Qt.UserRole + 1)) if it else (None, None)

    def show_saved(self):
        cid, name = self.current()
        if cid is None:
            return
        self.lbl_name.setText(name)
        self.shown = self.PAGE
        rep = radar.load_report(cid)
        if rep is None:
            self.report = None
            self._fill(None)
            self.lbl_progress.setText("Premi «Aggiorna» per analizzare il canale. Rilegge i messaggi passati: "
                                      "non serve che il computer sia rimasto acceso.")
            return
        self._fill(rep)
        spreads = ", ".join(f"{k} {v} punti" for k, v in (rep.spreads or {}).items())
        stale = rep.settings_sig and rep.settings_sig != radar.settings_sig(
            radar.effective_settings(self.engine.settings.get()))
        self.lbl_progress.setText(f"Analisi del {rep.generated} · ultimi {rep.days} giorni"
                                  + (f" · spread considerato: {spreads}" if spreads else "")
                                  + (f" · {rep.note}" if rep.note else "")
                                  + (" · dati esatti dal ponte (spread storico, valore del punto, swap)" if rep.precise else
                                     " · per la massima precisione aggiorna il ponte: Impostazioni → MetaTrader → "
                                     "«Installa il ponte», poi «Compila» in MetaEditor")
                                  + (" · ⚠ impostazioni del Radar cambiate dopo questa analisi: premi «Aggiorna»"
                                     if stale else "")
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
            self._clear_feed()
            return
        st, equity = self._stats(rep)
        lot = self.lot()
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
        self.pills[2].setToolTip(f"Con {lot:g} lotti per operazione, commissione {self.commission():g} per lotto"
                                 + (", swap inclusi" if rep.precise else "")
                                 + ("" if st["in_money"] else " · importo non stimabile per questi strumenti: "
                                    "risultato in punti (aggiorna il ponte)"))
        lim = rep.stats.get("limited", 0)
        self.pills[0].setToolTip(f"{lim} segnali saltati per il limite di posizioni aperte delle tue impostazioni"
                                 if lim else "Segnali che la copia avrebbe eseguito")
        self.equity.set_points(equity)

        # tabella dei segnali
        sigs = sorted(rep.signals, key=lambda s: -s.t)
        self.t_sig.setRowCount(len(sigs))
        for r, s in enumerate(sigs):
            entry = s.entry_p if s.entry_p is not None else s.entry
            out = _outcome(s) if s.status in STATUS_COLOR else s.status
            money = self._money_text(s, rep) if s.status in STATUS_COLOR else ""
            vals = [_when(s.t), s.symbol, f"{s.side.upper()}{'' if s.otype == 'market' else ' ' + s.otype.upper()}",
                    f"{entry:g}" if entry is not None else "mercato", f"{s.sl:g}" if s.sl else "–",
                    ", ".join(f"{t:g}" for t in s.tps) or "–", out + ("  ✎" if s.edited else ""),
                    (_pts(s.points) + (f"  ({money})" if money else "")) if s.status in STATUS_COLOR else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setForeground(QColor(gui.GREEN if s.side == "buy" else gui.RED))
                if c in (6, 7):
                    it.setForeground(QColor(OUTCOME_COLOR.get(out, gui.FAINT)))
                if c == 0:
                    it.setData(Qt.UserRole, s.msg_id)
                    it.setToolTip(s.text)
                self.t_sig.setItem(r, c, it)
        self._fill_feed()

    def _clear_feed(self):
        while self.feed_lay.count() > 1:
            w = self.feed_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

    def _fill_feed(self):
        rep = self.report
        self._clear_feed()
        if not rep:
            return
        by_id = {s.msg_id: s for s in rep.signals}
        msgs = sorted(rep.messages, key=lambda m: -m.t)
        pos = 0
        for m in msgs[:self.shown]:
            sig = by_id.get(m.msg_id) if m.note == "segnale" else None
            card = MessageCard(m, sig, self._money_text(sig, rep) if sig and sig.entry_t else "", self.open_chart_for)
            self.feed_lay.insertWidget(pos, card)
            pos += 1
        if len(msgs) > self.shown:
            self.feed_lay.insertWidget(pos, gui._btn(f"Mostra altri messaggi ({len(msgs) - self.shown})", self._more))

    def _more(self):
        self.shown += self.PAGE
        pos = self.feed.verticalScrollBar().value()
        self._fill_feed()
        self.feed.verticalScrollBar().setValue(pos)

    def feed_cards(self):
        out = []
        for i in range(self.feed_lay.count()):
            w = self.feed_lay.itemAt(i).widget()
            if isinstance(w, MessageCard) and not w.property("_gone"):
                out.append(w)
        return out

    # ---------------------------------------------------------------- grafico di un segnale
    def open_chart_for(self, sig):
        if sig is None or sig.entry_t is None:
            return
        RadarTradeChart(self, self.engine, sig).show()

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
                  f'Risultato <span style="color:{col}">{_pts(sig.points)} punti</span>', f"Simulato · {_outcome(sig)}"):
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

