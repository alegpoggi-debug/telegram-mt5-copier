"""Finestra «Migliora impostazioni» del Radar: ricerca, confronto con grafico e applicazione al canale."""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QHeaderView, QMessageBox, QProgressBar, QScrollArea, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import gui, optimizer, radar
from .settings import CHANNEL_KEYS

# colori delle serie (tavolozza validata per il tema scuro); «le tue impostazioni» in grigio, come riferimento
SERIES = [
    ("attuali", "Le tue impostazioni", "#898781"),
    ("consigliata", "Consigliata", "#3987e5"),
    ("guadagno", "Più guadagno", "#d95926"),
    ("tranquilla", "Più tranquilla", "#199e70"),
]
WEEKS_PER_MONTH = 4.345


def _eur(v):
    return ("+" if v >= 0 else "−") + f"{abs(v):,.0f} €".replace(",", ".")


class CompareChart(QWidget):
    """Guadagno cumulato nel tempo di più combinazioni, con linea e valori al passaggio del mouse."""

    def __init__(self):
        super().__init__()
        self.series = []            # (nome, colore, [(t, valore)])
        self.hover = None
        self.setMouseTracking(True)
        self.setMinimumHeight(260)

    def set_series(self, series):
        self.series = [s for s in series if s[2]]
        self.update()

    def _geom(self):
        L, R, T, B = 64, 120, 14, 28
        vals = [v for _n, _c, pts in self.series for _t, v in pts] + [0.0]
        lo, hi = min(vals), max(vals)
        pad = (hi - lo) * 0.06 or 10
        lo, hi = lo - pad, hi + pad
        raw = (hi - lo) / 5
        mag = 10 ** len(str(int(max(1, raw)))) / 10
        step = next(x * mag for x in (1, 2, 2.5, 5, 10) if x * mag >= raw)
        lo, hi = (lo // step) * step, -((-hi) // step) * step
        n = max(len(pts) for _n, _c, pts in self.series)
        w, h = self.width(), self.height()

        def x(i):
            return L + i * (w - L - R) / max(1, n - 1)

        def y(v):
            return T + (hi - v) * (h - T - B) / (hi - lo or 1)
        return L, R, T, B, lo, hi, step, n, x, y

    def mouseMoveEvent(self, e):
        if not self.series:
            return
        L, R, *_rest, n, _x, _y = self._geom()
        frac = (e.position().x() - L) / max(1, self.width() - L - R)
        self.hover = max(0, min(n - 1, round(frac * (n - 1))))
        self.update()

    def leaveEvent(self, _e):
        self.hover = None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(gui.SURFACE))
        if not self.series:
            return
        L, R, T, B, lo, hi, step, n, x, y = self._geom()
        f = p.font()
        f.setPointSizeF(max(8.0, f.pointSizeF() - 2))
        p.setFont(f)
        v = lo
        while v <= hi + 1e-9:
            p.setPen(QPen(QColor(gui.BORDER if abs(v) > 1e-9 else gui.MUTED), 1))
            p.drawLine(QPointF(L, y(v)), QPointF(self.width() - R, y(v)))
            p.setPen(QColor(gui.MUTED))
            p.drawText(QRectF(0, y(v) - 8, L - 8, 16), Qt.AlignRight | Qt.AlignVCenter, _eur(v))
            v += step
        pts0 = self.series[0][2]
        every = max(1, n // 6)
        for i in range(0, n, every):
            if i < len(pts0) and (n - 1 - i) >= every * 0.6:
                lab = dt.datetime.fromtimestamp(pts0[i][0], dt.timezone.utc).strftime("%d/%m/%y")
                p.drawText(QRectF(x(i) - 40, self.height() - B + 6, 80, 16), Qt.AlignCenter, lab)
        ends = []
        for _name, col, pts in self.series:
            path = QPainterPath()
            for i, (_t, val) in enumerate(pts):
                (path.moveTo if i == 0 else path.lineTo)(QPointF(x(i), y(val)))
            p.setPen(QPen(QColor(col), 2))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            ends.append([y(pts[-1][1]), _name, col, pts[-1][1], len(pts) - 1])
        ends.sort()
        for i in range(1, len(ends)):
            ends[i][0] = max(ends[i][0], ends[i - 1][0] + 30)
        for yy, name, col, val, last in ends:
            p.setPen(QPen(QColor(gui.SURFACE), 2))
            p.setBrush(QColor(col))
            p.drawEllipse(QPointF(x(last), y(val)), 4, 4)
            p.setPen(QColor(gui.TEXT))
            p.drawText(QRectF(self.width() - R + 10, yy - 15, R - 10, 15), Qt.AlignLeft | Qt.AlignBottom, name)
            p.setPen(QColor(gui.MUTED))
            p.drawText(QRectF(self.width() - R + 10, yy, R - 10, 15), Qt.AlignLeft | Qt.AlignTop, _eur(val))
        if self.hover is not None:
            i = self.hover
            p.setPen(QPen(QColor(gui.MUTED), 1, Qt.DashLine))
            p.drawLine(QPointF(x(i), T), QPointF(x(i), self.height() - B))
            lines = [dt.datetime.fromtimestamp(pts0[min(i, len(pts0) - 1)][0], dt.timezone.utc).strftime("%d/%m/%Y")]
            for name, col, pts in self.series:
                val = pts[min(i, len(pts) - 1)][1]
                p.setPen(QPen(QColor(gui.SURFACE), 2))
                p.setBrush(QColor(col))
                p.drawEllipse(QPointF(x(i), y(val)), 4, 4)
                lines.append(f"{name}: {_eur(val)}")
            bw, bh = 190, 18 * len(lines) + 10
            bx = min(x(i) + 12, self.width() - R - bw)
            p.setPen(QColor(gui.BORDER))
            p.setBrush(QColor(gui.SURFACE2))
            p.drawRoundedRect(QRectF(bx, T + 4, bw, bh), 8, 8)
            p.setPen(QColor(gui.TEXT))
            for k, t in enumerate(lines):
                p.drawText(QRectF(bx + 10, T + 9 + 18 * k, bw - 20, 18), Qt.AlignLeft | Qt.AlignVCenter, t)


class OptimizeDialog(QDialog):
    """Prova migliaia di combinazioni e propone quelle più costanti, con il pulsante per applicarle."""

    def __init__(self, parent, engine, cid, name, lot, on_applied=None, workers=None, plan=None, embedded=False,
                 on_back=None):
        super().__init__(parent)
        self.engine, self.cid, self.name, self.lot = engine, cid, name, lot
        self.on_applied = on_applied
        self.embedded, self.on_back = embedded, on_back
        self._workers, self._plan = workers, plan
        self.setWindowTitle(f"Migliora impostazioni · {name}")
        self.setStyleSheet(gui.STYLE)
        if embedded:
            self.setWindowFlags(Qt.Widget)       # pagina dentro la finestra dell'app, non una finestra nuova
        else:
            self.setMinimumSize(900, 640)
            gui._fit_to_screen(self, 1180, 860)
        self.run = None
        self._thread = None
        self.applied = None
        self.saved_meta = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(*((0, 0, 0, 0) if embedded else (20, 18, 20, 16)))
        lay.setSpacing(10)
        top = QHBoxLayout()
        if embedded:
            self.btn_back = gui._btn("←  Torna al Radar", self.go_back, "ghost")
            top.addWidget(self.btn_back)
        top.addWidget(gui._label(f"Migliora impostazioni · {name}", "h1"), 1)
        self.btn_redo = gui._btn("Rifai il calcolo", lambda: self.start(force=True))
        self.btn_redo.setToolTip("Rifà tutte le simulazioni con i dati dell'ultima analisi del Radar")
        self.btn_redo.hide()
        top.addWidget(self.btn_redo)
        lay.addLayout(top)
        self.intro_text = (
            "Rifà il Radar con migliaia di combinazioni di impostazioni, usando i messaggi e i prezzi già salvati. "
            "Per non premiare le combinazioni fortunate sceglie guardando i mesi più vecchi, controlla come sarebbero "
            "andati i mesi più recenti (mai usati per scegliere) e scarta quelle che crollano se si cambia di poco "
            "un'impostazione. Non apre nessuna operazione. Il risultato resta salvato anche chiudendo l'app.")
        self.lbl_intro = gui._label(self.intro_text, "muted", wrap=True)
        lay.addWidget(self.lbl_intro)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)

        # pagina 1: avanzamento
        pw = QWidget()
        pl = QVBoxLayout(pw)
        pl.addStretch(1)
        self.lbl_phase = gui._label("Preparo i dati…", "h2")
        self.lbl_phase.setAlignment(Qt.AlignCenter)
        pl.addWidget(self.lbl_phase)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setStyleSheet(f"QProgressBar {{ background: {gui.SURFACE2}; border: none; border-radius: 4px; }}"
                               f"QProgressBar::chunk {{ background: {gui.BLUE}; border-radius: 4px; }}")
        pl.addWidget(self.bar)
        self.lbl_count = gui._label("", "muted")
        self.lbl_count.setAlignment(Qt.AlignCenter)
        pl.addWidget(self.lbl_count)
        pl.addStretch(1)
        self.stack.addWidget(pw)

        # pagina 2: risultati
        self.res_scroll = QScrollArea()
        self.res_scroll.setWidgetResizable(True)
        self.res_box = QWidget()
        self.res_lay = QVBoxLayout(self.res_box)
        self.res_lay.setContentsMargins(0, 0, 6, 0)
        self.res_lay.setSpacing(12)
        self.res_scroll.setWidget(self.res_box)
        self.stack.addWidget(self.res_scroll)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_stop = gui._btn("Ferma", self.stop)
        self.btn_close = gui._btn("Chiudi", self.reject)
        bottom.addWidget(self.btn_stop)
        bottom.addWidget(self.btn_close)
        self.btn_close.setVisible(not embedded)
        lay.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        QTimer.singleShot(0, self.start)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def go_back(self):
        """← Torna al Radar: un calcolo in corso continua in background."""
        if self.on_back:
            self.on_back()

    # ---------------------------------------------------------------- ricerca
    def start(self, force=False):
        if self.is_running():
            return
        active = optimizer.ACTIVE.get(self.cid)
        if active is not None and active.alive():           # calcolo già in corso (pagina riaperta)
            self.current = radar.effective_settings(self.engine.settings.get(), self.cid)
            self._rep_generated = getattr(active, "rep_generated", None)
            self.run, self._thread = active, active.thread
            self.stack.setCurrentIndex(0)
            self._timer.start(200)
            return
        rep = radar.load_report(self.cid)
        if rep is None:
            self._fail("Prima premi «Aggiorna» nel Radar: servono i messaggi e i prezzi dell'ultima analisi.")
            return
        self.current = radar.effective_settings(self.engine.settings.get(), self.cid)
        self._rep_generated = rep.generated
        if not force:
            saved = optimizer.load_result(self.cid)
            if saved:
                self.show_results(saved["result"], meta=saved)
                return
        self._clear_results()
        self.stack.setCurrentIndex(0)
        self.btn_redo.hide()
        self.bar.show()
        self.lbl_count.show()
        self.btn_stop.show()
        self.lbl_phase.setText("Preparo i dati…")
        payload = optimizer.prepare(rep, self.engine.settings.get(), self.cid, self.lot)
        why = optimizer.check(payload)
        if why:
            self._fail(why)
            return
        days = (payload["end"] - payload["week0"]) // 86400
        self.lbl_intro.setText(self.intro_text + f"\n\nDati: {len(payload['msgs'])} messaggi, circa {days} giorni "
                               f"· confronto con {self.lot:g} lotti per segnale.")
        meta = {"report_generated": rep.generated, "lot": self.lot, "settings_sig": radar.settings_sig(self.current)}
        cid = self.cid

        def save(result):                                     # anche se la pagina è stata chiusa nel frattempo
            m = dict(meta, saved_at=dt.datetime.now().strftime("%d/%m/%Y %H:%M"))
            try:
                optimizer.save_result(cid, result, m)
            except OSError:
                pass
            run.meta = m
        run = optimizer.Run(payload, workers=self._workers, plan=self._plan, on_done=save)
        run.rep_generated = rep.generated
        self.run = run
        self._thread = run.start(key=self.cid)
        self._timer.start(200)

    def _poll(self):
        r = self.run
        if r is None:
            return
        self.lbl_phase.setText(r.phase or "Preparo i dati…")
        if r.total:
            self.bar.setMaximum(r.total)
            self.bar.setValue(r.done)
            self.lbl_count.setText(f"{r.done} combinazioni provate su {r.total}")
        if self._thread.is_alive():
            return
        self._timer.stop()
        self.btn_stop.hide()
        if r.cancelled:
            if not self.embedded:
                self.reject()
                return
            saved = optimizer.load_result(self.cid)
            if saved:
                self.show_results(saved["result"], meta=saved)
            else:
                self._fail("Calcolo fermato. Premi «Rifai il calcolo» per ricominciare.")
        elif r.error:
            self._fail(f"Il miglioramento non è riuscito: {r.error}")
        else:
            self.show_results(r.result, meta=getattr(r, "meta", None))

    def stop(self):
        if self.run:
            self.run.cancel()
            self.lbl_phase.setText("Mi fermo…")

    def reject(self):
        if self.embedded:                       # come pagina: «indietro», il calcolo continua
            self.go_back()
            return
        if self.is_running():
            self.run.cancel()
        super().reject()

    def _fail(self, text):
        self.stack.setCurrentIndex(0)
        self.lbl_phase.setText(text)
        self.lbl_phase.setWordWrap(True)
        self.bar.hide()
        self.lbl_count.hide()
        self.btn_stop.hide()
        self.btn_redo.setVisible(radar.load_report(self.cid) is not None)

    def _clear_results(self):
        while self.res_lay.count():
            it = self.res_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                lay = it.layout()
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

    # ---------------------------------------------------------------- risultati
    def _per_month(self, weeks_total, nweeks):
        return weeks_total / max(1e-9, nweeks / WEEKS_PER_MONTH)

    def show_results(self, res, meta=None):
        self._clear_results()
        self.result = res
        self.saved_meta = meta
        self.btn_redo.show()
        self.current = radar.effective_settings(self.engine.settings.get(), self.cid)
        if meta:
            notes = [f"Calcolo del {meta.get('saved_at', '?')} sui dati del Radar del {meta.get('report_generated', '?')}."]
            if meta.get("report_generated") != getattr(self, "_rep_generated", meta.get("report_generated")):
                notes.append("⚠ Il Radar è stato aggiornato dopo questo calcolo: premi «Rifai il calcolo» per usare i dati nuovi.")
            if meta.get("settings_sig") and meta["settings_sig"] != radar.settings_sig(self.current):
                notes.append("Le impostazioni del canale sono cambiate dopo questo calcolo (per esempio hai applicato una "
                             "proposta): «Le tue impostazioni» qui sotto sono quelle di allora.")
            if meta.get("lot") is not None and abs(meta["lot"] - self.lot) > 1e-9:
                notes.append(f"Calcolato con {meta['lot']:g} lotti per segnale (adesso nel Radar: {self.lot:g}).")
            lbl = gui._label("\n".join(notes), "muted", wrap=True)
            self.res_lay.addWidget(lbl)
            self.lbl_saved = lbl
        lot = (meta or {}).get("lot", self.lot)
        picks = res["picks"]
        nweeks = res["weeks"]
        test_weeks = nweeks - max(1, round(nweeks * 2 / 3))
        if not picks:
            self.res_lay.addWidget(gui._label(
                "Le tue impostazioni vanno già bene: nessuna combinazione è migliore e supera anche le prove di "
                "costanza. Con più dati (nel Radar «Ultimi 6 mesi» o «Ultimo anno») il risultato è più affidabile.",
                "h2", wrap=True))
        rows = [("attuali", res["current"])] + [(k, picks[k]) for k in ("consigliata", "guadagno", "tranquilla") if k in picks]
        names = {k: n for k, n, _c in SERIES}
        colors = {k: c for k, _n, c in SERIES}
        self.res_lay.addWidget(gui._label(
            f"Provate {res['tested']} combinazioni · confronto con {lot:g} lotti per segnale · le ultime "
            f"{test_weeks} settimane non sono state usate per scegliere («mesi mai visti»).", "muted", wrap=True))

        chart_card, cl = gui._card(margins=12)
        cl.addWidget(gui._label("GUADAGNO NEL TEMPO", "section"))
        self.chart = CompareChart()
        self.chart.set_series([(names[k], colors[k], res["series"][k]) for k, _r in rows])
        cl.addWidget(self.chart)
        self.res_lay.addWidget(chart_card)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.apply_buttons = {}
        for k, r in rows:
            cards.addWidget(self._card(k, r, names[k], colors[k], nweeks, test_weeks), 1)
        self.res_lay.addLayout(cards)

        mcard, ml = gui._card(margins=12)
        ml.addWidget(gui._label("MESE PER MESE", "section"))
        months = sorted({m for k, _r in rows for m in res["months"][k]})
        t = QTableWidget(len(months), len(rows))
        t.setHorizontalHeaderLabels([names[k] for k, _r in rows])
        t.setVerticalHeaderLabels([dt.datetime.strptime(m, "%Y-%m").strftime("%m/%Y") for m in months])
        for j, (k, _r) in enumerate(rows):
            for i, m in enumerate(months):
                v = res["months"][k].get(m, 0.0)
                it = QTableWidgetItem(_eur(v))
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                it.setForeground(QColor(gui.TEXT if v >= 0 else gui.RED))
                t.setItem(i, j, it)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setMinimumHeight(min(420, 32 * (len(months) + 1)))
        self.table = t
        ml.addWidget(t)
        self.res_lay.addWidget(mcard)
        self.res_lay.addWidget(gui._label(
            "Sono stime sul passato: il futuro può andare diversamente. Le proposte sono scelte per essere costanti, "
            "non solo per il guadagno più alto. Prova le nuove impostazioni prima con un lotto piccolo.", "muted", wrap=True))
        self.stack.setCurrentIndex(1)

    def _card(self, key, r, title, color, nweeks, test_weeks):
        m = r["m"]
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        head = QHBoxLayout()
        sw = QFrame()
        sw.setFixedSize(14, 4)
        sw.setStyleSheet(f"background: {color}; border-radius: 2px;")
        head.addWidget(sw)
        head.addWidget(gui._label(title, "h2"), 1)
        lay.addLayout(head)
        big = gui._label(_eur(self._per_month(m["tot"], nweeks)) + " al mese", "h1")
        lay.addWidget(big)
        lay.addWidget(gui._label(
            f"In guadagno {r['pos']} {'periodo' if r['pos'] == 1 else 'periodi'} di 4 settimane su {r['nblocks']}\n"
            f"Mesi mai visti: {_eur(self._per_month(r['test'], test_weeks))} al mese\n"
            f"Calo massimo: {_eur(-m['dd'])}\nOperazioni: {m['n']}", "muted", wrap=True))
        if key == "attuali":
            lay.addWidget(gui._label("Le impostazioni che usi adesso per questo canale.", "muted", wrap=True))
        else:
            ch = optimizer.changes(self.current, r["cfg"])
            if ch:
                lay.addWidget(gui._label("CAMBIA", "section"))
                for lab, old, new in ch:
                    lay.addWidget(gui._label(f"{lab}: {new}  (adesso: {old})", wrap=True))
            else:
                lay.addWidget(gui._label("Uguale a quella che usi già.", "muted", wrap=True))
            if ch:
                b = gui._btn("Applica a questo canale", lambda _c=False, c=r["cfg"], n=title: self.apply(c, n),
                             "primary" if key == "consigliata" else None)
                self.apply_buttons[key] = b
                lay.addWidget(b)
        lay.addStretch(1)
        return card

    def apply(self, cfg, title):
        if QMessageBox.question(self, "Applica", f"Mettere le impostazioni «{title}» per il canale «{self.name}»?\n\n"
                                "Valgono sia per la copia vera sia per il Radar. Le altre impostazioni non cambiano.") \
                != QMessageBox.Yes:
            return
        all_over = dict(self.engine.settings.get().get("channel_overrides") or {})
        mine = dict(all_over.get(str(self.cid)) or {})
        for k in optimizer.KEYS:
            if k in CHANNEL_KEYS and k in cfg:
                if k == "tp_index" and cfg.get("multi_tp_mode") != "index":
                    continue
                mine[k] = cfg[k]
        all_over[str(self.cid)] = mine
        self.engine.settings.update({"channel_overrides": all_over})
        self.applied = cfg
        QMessageBox.information(self, "Fatto", f"Impostazioni «{title}» messe per il canale «{self.name}».\n\n"
                                "Le trovi nel ⚙ accanto al canale (pagina Telegram). Ora il Radar si aggiorna.")
        if self.embedded:
            self.show_results(self.result, meta=self.saved_meta)     # ora le proposte si confrontano con le nuove
        if self.on_applied:
            self.on_applied()
        if not self.embedded:
            self.accept()
