"""Grafico delle operazioni: candele della moneta con frecce di entrata/uscita, SL e TP.

Finestra separata, nello stesso stile dell'app. Si apre con un doppio clic su un'operazione.
Le candele e lo storico arrivano dal ponte in MetaTrader (comandi RATES e HISTORY).
"""
from __future__ import annotations

import asyncio
import bisect
import datetime as dt
import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QButtonGroup, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import gui

TIMEFRAMES = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
OLD_BRIDGE = ("Per vedere il grafico aggiorna il ponte una volta: Impostazioni → MetaTrader → «Installa il ponte», "
              "poi in MetaEditor apri TelegramBridge e premi «Compila».")


# ============================================================================ dati
@dataclass
class Trade:
    key: int                      # id della posizione in MetaTrader
    symbol: str
    side: str                     # "buy" / "sell"
    volume: float
    entry_time: int | None        # orario del broker (secondi)
    entry_price: float
    exit_time: int | None = None
    exit_price: float | None = None
    profit: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    is_open: bool = False
    is_order: bool = False
    exits: list = field(default_factory=list)   # chiusure parziali: (tempo, prezzo)
    unit: str = ""                # "punti" per i segnali simulati del Radar (risultato in punti, non in denaro)

    def label(self) -> str:
        side = self.side.upper()
        if self.is_order:
            return f"{self.symbol}  ·  ordine {side} a {self.entry_price:g}"
        if self.is_open:
            return f"● {self.symbol}  ·  {side} {self.volume:g}  ·  aperta"
        when = dt.datetime.fromtimestamp(self.exit_time or 0, dt.timezone.utc).strftime("%d/%m %H:%M")
        return f"{self.symbol}  ·  {side} {self.volume:g}  ·  {self.profit:+.2f}  ·  {when}"


def parse_rates(text: str) -> list[tuple]:
    out = []
    for line in text.splitlines():
        p = line.strip().split("|")
        if len(p) >= 5:
            try:
                out.append((int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])))
            except ValueError:
                continue
    out.sort(key=lambda r: r[0])
    return out


def parse_deals(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        p = line.strip().split("|")
        if len(p) >= 12 and p[0] == "deal":
            try:
                out.append({"ticket": int(p[1]), "pos": int(p[2]), "time": int(p[3]), "type": int(p[4]),
                            "entry": int(p[5]), "price": float(p[6]), "volume": float(p[7]), "profit": float(p[8]),
                            "symbol": p[9], "magic": int(p[10]), "comment": p[11]})
            except ValueError:
                continue
    return out


def build_trades(deals: list[dict], magic: int, positions: list[dict], orders: list[dict]) -> list[Trade]:
    """Raggruppa le transazioni in operazioni (entrata + uscite) e aggiunge quelle ancora aperte."""
    ours = {d["pos"] for d in deals if d["entry"] == 0 and d["magic"] == magic}
    ours |= {p["ticket"] for p in positions}
    by_pos: dict[int, list[dict]] = {}
    for d in deals:
        if d["pos"] in ours:
            by_pos.setdefault(d["pos"], []).append(d)
    open_by_ticket = {p["ticket"]: p for p in positions}
    trades = []
    for pos, ds in by_pos.items():
        ds.sort(key=lambda d: (d["time"], d["ticket"]))
        ins = [d for d in ds if d["entry"] == 0]
        outs = [d for d in ds if d["entry"] in (1, 3)]
        if not ins:
            continue
        first = ins[0]
        t = Trade(key=pos, symbol=first["symbol"], side="buy" if first["type"] == 0 else "sell",
                  volume=sum(d["volume"] for d in ins), entry_time=first["time"], entry_price=first["price"],
                  profit=sum(d["profit"] for d in ds))
        t.exits = [(d["time"], d["price"]) for d in outs]
        p = open_by_ticket.get(pos)
        if p:
            t.is_open, t.volume, t.sl, t.tp = True, p["volume"], p["sl"], p["tp"]
            t.profit = sum(d["profit"] for d in outs) + p["profit"]
        elif outs:
            vol = sum(d["volume"] for d in outs) or 1
            t.exit_time = outs[-1]["time"]
            t.exit_price = sum(d["price"] * d["volume"] for d in outs) / vol
        trades.append(t)
    have = {t.key for t in trades}
    for p in positions:   # aperta ma senza storico (es. aperta da più di 30 giorni)
        if p["ticket"] not in have:
            trades.append(Trade(key=p["ticket"], symbol=p["symbol"], side=p["side"], volume=p["volume"],
                                entry_time=None, entry_price=p["price_open"], profit=p["profit"],
                                sl=p["sl"], tp=p["tp"], is_open=True))
    for o in orders:
        side = "buy" if "BUY" in o["type"].upper() else "sell"
        trades.append(Trade(key=o["ticket"], symbol=o["symbol"], side=side, volume=o["volume"], entry_time=None,
                            entry_price=o["price"], sl=o["sl"], tp=o["tp"], is_open=True, is_order=True))
    trades.sort(key=lambda t: (not t.is_open, -(t.exit_time or t.entry_time or 0)))
    return trades


def auto_timeframe(span_seconds: int) -> str:
    for name, sec in TIMEFRAMES.items():
        if span_seconds / sec <= 90:
            return name
    return "D1"


def rates_range(trade: Trade, tf: str, server_now: int) -> tuple[int, int]:
    """Intervallo di tempo da chiedere a MetaTrader per vedere bene l'operazione."""
    sec = TIMEFRAMES[tf]
    if trade.entry_time is None:
        return server_now - 150 * sec, 0
    end = trade.exit_time if trade.exit_time else server_now
    span = max(end - trade.entry_time, sec)
    pad = max(int(span * 0.4), 30 * sec)
    start = trade.entry_time - pad
    stop = 0 if trade.is_open else end + pad
    return start, stop


async def fetch(bridge, cmd: dict) -> tuple[str | None, str | None, int]:
    """Esegue un comando del grafico. Ritorna (testo dati, errore, orario del broker)."""
    for attempt in range(2):
        res = await bridge.send(cmd, timeout=20)
        now = int(float(res.get("price") or 0))
        if res.get("ok") == "1":
            f = bridge.dir / res.get("msg", "")
            try:
                text = f.read_text(encoding="latin-1")
            finally:
                f.unlink(missing_ok=True)
            return text, None, now
        msg = res.get("msg", "")
        if "sconosciuta" in msg:
            return None, OLD_BRIDGE, now
        if "non ancora disponibili" in msg and attempt == 0:
            await asyncio.sleep(1.5)   # MetaTrader sta scaricando lo storico
            continue
        return None, msg or "errore sconosciuto", now
    return None, "candele non disponibili", 0


# ============================================================================ disegno
def _nice_step(span: float) -> float:
    if span <= 0:
        return 1.0
    raw = span / 5
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


class CandleChart(QWidget):
    AXIS_W, TIME_H, PAD = 78, 26, 12

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumHeight(320)
        self.bars: list[tuple] = []
        self.times: list[int] = []
        self.trade: Trade | None = None
        self.tf = "M5"
        self.digits = 2
        self.message = "Caricamento…"
        self.vs, self.vc = 0, 0          # prima candela visibile, numero di candele visibili
        self.mouse: QPointF | None = None
        self._drag = None

    # ---------------------------------------------------------------- dati
    def set_data(self, bars, trade, tf, keep_view=False):
        old_len = len(self.bars)
        old_t0 = self.times[self.vs] if keep_view and self.times and self.vs < len(self.times) else None
        self.bars, self.trade, self.tf = bars, trade, tf
        self.times = [b[0] for b in bars]
        self.message = "" if bars else self.message
        self.digits = self._guess_digits()
        if keep_view and old_len and self.vc and old_t0 is not None:
            at_end = self.vs + self.vc >= old_len - 1
            self.vc = min(self.vc, len(bars))
            # la vista resta sugli stessi orari anche se arrivano candele nuove
            self.vs = self._index(old_t0)
            if at_end:
                self.vs = max(0, len(bars) - self.vc)
            self.vs = min(self.vs, max(0, len(bars) - self.vc))
        else:
            self.fit_view()
            return
        self.update()

    def fit_view(self):
        """Vista iniziale: l'operazione in primo piano, con poche candele grandi."""
        n = len(self.bars)
        if not n:
            return
        tr = self.trade
        if tr and tr.entry_time:
            i0 = self._index(tr.entry_time)
            i1 = self._index(tr.exit_time) if tr.exit_time else n - 1
            margin = max(8, int((i1 - i0) * 0.35))
            a, b = max(0, i0 - margin), min(n - 1, i1 + margin)
            vc = max(b - a + 1, min(n, 36))
            if vc > 140:          # operazione molto lunga: parto dall'entrata
                vc = 140
            self.vc = min(n, vc)
            self.vs = max(0, min(a, n - self.vc))
        else:
            self.vc = min(n, 70)
            self.vs = n - self.vc
        self.update()

    def zoom(self, factor):
        if not self.bars:
            return
        center = self.vs + self.vc / 2
        self.vc = max(12, min(len(self.bars), int(round(self.vc * factor))))
        self.vs = int(max(0, min(len(self.bars) - self.vc, center - self.vc / 2)))
        self.update()

    def set_message(self, text):
        self.message = text
        self.bars, self.times = [], []
        self.update()

    def _guess_digits(self):
        if not self.bars:
            return 2
        p = self.bars[-1][4]
        for d in range(0, 6):
            if abs(round(p, d) - p) < 1e-9 and all(abs(round(b[4], d) - b[4]) < 1e-9 for b in self.bars[-30:]):
                return max(d, 1)
        return 5

    def _index(self, t):
        return max(0, bisect.bisect_right(self.times, t) - 1)

    # ---------------------------------------------------------------- geometria
    def _plot(self) -> QRectF:
        return QRectF(self.PAD, self.PAD, self.width() - self.PAD - self.AXIS_W, self.height() - self.PAD - self.TIME_H)

    def _bar_w(self, plot):
        return plot.width() / max(self.vc, 1)

    def _x(self, plot, idx):
        return plot.left() + (idx - self.vs + 0.5) * self._bar_w(plot)

    def _range(self):
        vis = self.bars[self.vs:self.vs + self.vc]
        lo = min(b[3] for b in vis)
        hi = max(b[2] for b in vis)
        t = self.trade
        if t and t.entry_time:
            last = self.vs + self.vc - 1
            pts = [(self._index(t.entry_time), t.entry_price)]
            if t.exit_time and t.exit_price:
                pts.append((self._index(t.exit_time), t.exit_price))
            for i, price in pts:
                if self.vs <= i <= last:
                    lo, hi = min(lo, price), max(hi, price)
        span = (hi - lo) or abs(hi) * 0.001 or 1
        room = 46 / max(self._plot().height(), 100)   # spazio per frecce ed etichette
        pad = span * (0.06 + room)
        return lo - pad, hi + pad

    # ---------------------------------------------------------------- pittura
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(gui.SURFACE))
        small = QFont(self.font())
        small.setPointSizeF(max(8.0, self.font().pointSizeF() - 1.5))
        p.setFont(small)
        if not self.bars:
            p.setPen(QColor(gui.MUTED))
            p.drawText(self.rect().adjusted(40, 0, -40, 0), Qt.AlignCenter | Qt.TextWordWrap, self.message)
            return
        plot = self._plot()
        lo, hi = self._range()

        def y(price):
            return plot.top() + (hi - price) / (hi - lo) * plot.height()

        # griglia e prezzi (senza sovrapporsi alle etichette di prezzo attuale, SL e TP)
        tags = [y(self.bars[-1][4])]
        if self.trade:
            tags += [min(max(y(v), plot.top() + 9), plot.bottom() - 9) for v in (self.trade.sl, self.trade.tp) if v]
        step = _nice_step(hi - lo)
        v = math.ceil(lo / step) * step
        while v <= hi:
            yy = y(v)
            p.setPen(QPen(QColor(gui.BORDER), 1))
            p.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            p.setPen(QColor(gui.FAINT))
            if all(abs(yy - ty) > 16 for ty in tags):
                p.drawText(QRectF(plot.right() + 8, yy - 8, self.AXIS_W - 10, 16), Qt.AlignLeft | Qt.AlignVCenter,
                           f"{v:.{self.digits}f}")
            v += step

        # orari
        bw = self._bar_w(plot)
        every = max(1, math.ceil(self.vc / 6))
        span = self.times[min(len(self.times) - 1, self.vs + self.vc - 1)] - self.times[self.vs]
        fmt = "%d/%m" if self.tf == "D1" else ("%d/%m %H:%M" if span > 86400 else "%H:%M")
        p.setPen(QColor(gui.FAINT))
        for i in range(self.vs, min(len(self.bars), self.vs + self.vc)):
            if (i - self.vs) % every == every // 2:
                x = self._x(plot, i)
                txt = dt.datetime.fromtimestamp(self.bars[i][0], dt.timezone.utc).strftime(fmt)
                p.drawText(QRectF(x - 50, plot.bottom() + 6, 100, 16), Qt.AlignCenter, txt)

        # candele
        p.save()
        p.setClipRect(plot)
        body_w = max(1.0, bw * 0.7)
        for i in range(self.vs, min(len(self.bars), self.vs + self.vc)):
            t, o, h, l_, c = self.bars[i]
            x = self._x(plot, i)
            col = QColor(gui.GREEN if c >= o else gui.RED)
            p.setPen(QPen(col, 1))
            p.drawLine(QPointF(x, y(h)), QPointF(x, y(l_)))
            top, bot = y(max(o, c)), y(min(o, c))
            p.fillRect(QRectF(x - body_w / 2, top, body_w, max(1.0, bot - top)), col)
        p.restore()

        tr = self.trade
        last_close = self.bars[-1][4]
        # prezzo attuale
        self._hline(p, plot, y(last_close), QColor(gui.MUTED), f"{last_close:.{self.digits}f}", dash=False, faint=True)
        if tr:
            if tr.sl:
                self._level(p, plot, y(tr.sl), QColor(gui.RED), "SL", tr.sl)
            if tr.tp:
                self._level(p, plot, y(tr.tp), QColor(gui.GREEN), "TP", tr.tp)
            if tr.is_order or tr.entry_time is None:
                self._hline(p, plot, y(tr.entry_price), QColor(gui.BLUE),
                            f"{'Ordine' if tr.is_order else 'Entrata'} {tr.entry_price:.{self.digits}f}")
            else:
                self._draw_trade(p, plot, y, tr, last_close)

        # mirino
        if self.mouse and plot.contains(self.mouse):
            p.setPen(QPen(QColor(gui.FAINT), 1, Qt.DashLine))
            p.drawLine(QPointF(self.mouse.x(), plot.top()), QPointF(self.mouse.x(), plot.bottom()))
            p.drawLine(QPointF(plot.left(), self.mouse.y()), QPointF(plot.right(), self.mouse.y()))
            price = hi - (self.mouse.y() - plot.top()) / plot.height() * (hi - lo)
            self._tag(p, QRectF(plot.right() + 2, self.mouse.y() - 9, self.AXIS_W - 4, 18), f"{price:.{self.digits}f}",
                      QColor(gui.SURFACE2), QColor(gui.TEXT))
            idx = int(self.vs + (self.mouse.x() - plot.left()) / bw)
            if 0 <= idx < len(self.bars):
                b = self.bars[idx]
                when = dt.datetime.fromtimestamp(b[0], dt.timezone.utc).strftime("%d/%m %H:%M")
                info = (f"{when}   A {b[1]:.{self.digits}f}   Max {b[2]:.{self.digits}f}   "
                        f"Min {b[3]:.{self.digits}f}   C {b[4]:.{self.digits}f}")
                p.setPen(QColor(gui.MUTED))
                p.drawText(QRectF(plot.left() + 6, plot.top() + 2, plot.width(), 16), Qt.AlignLeft | Qt.AlignVCenter, info)

    def _hline(self, p, plot, yy, col, text, dash=True, faint=False):
        if yy < plot.top() - 1 or yy > plot.bottom() + 1:
            return
        c = QColor(col)
        if faint:
            c.setAlpha(120)
        p.setPen(QPen(c, 1, Qt.DashLine if dash else Qt.SolidLine))
        p.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
        bg = QColor(col)
        bg.setAlpha(60 if faint else 230)
        self._tag(p, QRectF(plot.right() + 2, yy - 9, self.AXIS_W - 4, 18), text, bg,
                  QColor(gui.TEXT) if faint else QColor("#0b0f14"))

    def _level(self, p, plot, yy, col, name, price):
        """SL/TP: linea se visibile, altrimenti etichetta sul bordo con la freccia verso dove si trova."""
        text = f"{name} {price:.{self.digits}f}"
        if plot.top() <= yy <= plot.bottom():
            self._hline(p, plot, yy, col, text)
            return
        up = yy < plot.top()
        edge = plot.top() + 9 if up else plot.bottom() - 9
        bg = QColor(col)
        bg.setAlpha(230)
        self._tag(p, QRectF(plot.right() + 2, edge - 9, self.AXIS_W - 4, 18), f"{'▲' if up else '▼'} {text}", bg,
                  QColor("#0b0f14"))

    def _tag(self, p, rect, text, bg, fg):
        path = QPainterPath()
        path.addRoundedRect(rect, 4, 4)
        p.fillPath(path, bg)
        p.setPen(fg)
        p.drawText(rect, Qt.AlignCenter, text)

    def _draw_trade(self, p, plot, y, tr, last_close):
        xi = self._x(plot, self._index(tr.entry_time))
        yi = y(tr.entry_price)
        if tr.exit_time:
            xo, yo = self._x(plot, self._index(tr.exit_time)), y(tr.exit_price)
        else:
            xo, yo = self._x(plot, len(self.bars) - 1), y(last_close)
        win = tr.profit >= 0
        res_col = QColor(gui.GREEN if win else gui.RED)
        p.save()
        p.setClipRect(plot.adjusted(-2, -2, 2, 2))
        # fascia della durata dell'operazione
        bw = self._bar_w(plot)
        band = QColor(res_col)
        band.setAlpha(22)
        p.fillRect(QRectF(min(xi, xo) - bw / 2, plot.top(), abs(xo - xi) + bw, plot.height()), band)
        # prezzo di entrata
        ent = QColor(gui.TEXT)
        ent.setAlpha(90)
        p.setPen(QPen(ent, 1, Qt.DotLine))
        p.drawLine(QPointF(xi, yi), QPointF(xo, yi))
        # linea entrata → uscita
        line = QColor(res_col)
        line.setAlpha(200)
        p.setPen(QPen(line, 1.5, Qt.DashLine))
        p.drawLine(QPointF(xi, yi), QPointF(xo, yo))
        # chiusure parziali
        for (t, price) in tr.exits[:-1] if tr.exit_time else tr.exits:
            xx, yy = self._x(plot, self._index(t)), y(price)
            p.setPen(QPen(QColor(gui.TEXT), 1.5))
            p.setBrush(QColor(gui.SURFACE))
            p.drawEllipse(QPointF(xx, yy), 3.5, 3.5)
        # freccia di entrata
        buy = tr.side == "buy"
        col = QColor(gui.GREEN if buy else gui.RED)
        s = 9
        if buy:
            tri = QPolygonF([QPointF(xi, yi + 3), QPointF(xi - s, yi + 3 + s * 1.5), QPointF(xi + s, yi + 3 + s * 1.5)])
            lbl_y = yi + 3 + s * 1.5 + 2
        else:
            tri = QPolygonF([QPointF(xi, yi - 3), QPointF(xi - s, yi - 3 - s * 1.5), QPointF(xi + s, yi - 3 - s * 1.5)])
            lbl_y = yi - 3 - s * 1.5 - 18
        p.setPen(QPen(QColor(gui.SURFACE), 1.5))
        p.setBrush(col)
        p.drawPolygon(tri)
        p.setPen(col)
        p.drawText(QRectF(xi - 100, lbl_y, 200, 16), Qt.AlignCenter,
                   f"{'BUY' if buy else 'SELL'}{f' {tr.volume:g}' if tr.volume else ''}  ·  {tr.entry_price:.{self.digits}f}")
        # punto di uscita
        if tr.exit_time:
            p.setPen(QPen(res_col, 2))
            p.setBrush(QColor(gui.SURFACE))
            p.drawEllipse(QPointF(xo, yo), 6, 6)
            p.setBrush(res_col)
            p.drawEllipse(QPointF(xo, yo), 2.5, 2.5)
            p.setPen(res_col)
            above = (yo < yi) if buy else (yo <= yi)
            ty = yo - 26 if above else yo + 10
            p.drawText(QRectF(xo - 100, ty, 200, 16), Qt.AlignCenter,
                       (f"{tr.profit:+.0f} {tr.unit}" if tr.unit else f"{tr.profit:+.2f}") + f"  ·  {tr.exit_price:.{self.digits}f}")
        p.restore()

    # ---------------------------------------------------------------- mouse
    def mouseMoveEvent(self, e):
        self.mouse = e.position()
        if self._drag is not None and self.bars:
            x0, vs0 = self._drag
            shift = int(round((x0 - e.position().x()) / self._bar_w(self._plot())))
            self.vs = max(0, min(len(self.bars) - self.vc, vs0 + shift))
        self.update()

    def leaveEvent(self, _e):
        self.mouse = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = (e.position().x(), self.vs)

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def mouseDoubleClickEvent(self, _e):
        self.fit_view()

    def wheelEvent(self, e):
        if not self.bars:
            return
        plot = self._plot()
        frac = min(1.0, max(0.0, (e.position().x() - plot.left()) / max(plot.width(), 1)))
        anchor = self.vs + frac * self.vc
        new = int(self.vc * (0.85 if e.angleDelta().y() > 0 else 1.18))
        self.vc = max(15, min(len(self.bars), new))
        self.vs = int(max(0, min(len(self.bars) - self.vc, anchor - frac * self.vc)))
        self.update()


# ============================================================================ finestra
class ChartWindow(QDialog):
    _instance = None

    @classmethod
    def show_for(cls, parent, engine, ticket=None):
        if cls._instance is None or not cls._instance.isVisible():
            cls._instance = ChartWindow(parent, engine)
        w = cls._instance
        w.want_key = ticket
        w.show()
        w.raise_()
        w.activateWindow()
        w.reload_trades()
        return w

    def __init__(self, parent, engine):
        super().__init__(parent)
        self.engine = engine
        self.trades: list[Trade] = []
        self.want_key = None
        self.tf_choice = "Auto"
        self.server_now = 0
        self._busy = False
        self._last_key = None
        self._last_state = None
        self.setWindowTitle("Grafico")
        self.setStyleSheet(gui.STYLE + f"""
            QPushButton#tf {{ background: transparent; border: none; color: {gui.MUTED}; padding: 5px 9px; border-radius: 6px; }}
            QPushButton#tf:checked {{ background: {gui.SURFACE2}; color: {gui.TEXT}; }}
            QPushButton#tf:hover {{ color: {gui.TEXT}; }}""")
        self.setMinimumSize(760, 480)
        gui._fit_to_screen(self, 1280, 820)

        self.cb = QComboBox()
        self.cb.setMinimumWidth(400)
        self.cb.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cb.currentIndexChanged.connect(lambda _i: self.load_chart(keep_view=False))
        self.tf_group = QButtonGroup(self)
        tf_row = QHBoxLayout()
        tf_row.setSpacing(2)
        for name in ["Auto", *TIMEFRAMES]:
            b = QPushButton(name)
            b.setObjectName("tf")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(name == "Auto")
            b.clicked.connect(lambda _c, n=name: self._set_tf(n))
            self.tf_group.addButton(b)
            tf_row.addWidget(b)
        top = QHBoxLayout()
        top.addWidget(self.cb)
        top.addStretch(1)
        top.addLayout(tf_row)
        top.addSpacing(10)
        for text, tip, fn in (("−", "Allontana", lambda: self.chart.zoom(1.35)),
                              ("+", "Avvicina", lambda: self.chart.zoom(1 / 1.35)),
                              ("Adatta", "Torna alla vista dell'operazione", lambda: self.chart.fit_view())):
            b = QPushButton(text)
            b.setObjectName("tf")
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            top.addWidget(b)

        self.info = [gui._label("", "pill") for _ in range(5)]
        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        for lbl in self.info:
            info_row.addWidget(lbl)
        info_row.addStretch(1)

        card, cl = gui._card(margins=8)
        self.chart = CandleChart()
        cl.addWidget(self.chart)
        hint = gui._label("Rotella o − / +: zoom  ·  trascina: sposta  ·  doppio clic o «Adatta»: torna all'operazione"
                          "  ·  orari del broker", "muted")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)
        root.addLayout(top)
        root.addLayout(info_row)
        root.addWidget(card, 1)
        root.addWidget(hint)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(5000)

    def _message(self, text):
        """Messaggio al posto del grafico (senza le etichette vuote in alto)."""
        self.chart.set_message(text)
        for lbl in self.info:
            lbl.hide()

    # ---------------------------------------------------------------- comandi asincroni
    def _run(self, coro, done):
        fut = asyncio.run_coroutine_threadsafe(coro, self.engine.loop)
        poll = QTimer(self)

        def check():
            if fut.done():
                poll.stop()
                poll.deleteLater()
                try:
                    done(fut.result(), None)
                except Exception as e:  # errore di comunicazione
                    done(None, e)
        poll.timeout.connect(check)
        poll.start(50)

    def _ready(self) -> str | None:
        if self.engine.bridge is None or not self.engine.mt5_connected():
            return "MetaTrader non è collegato: aprilo con TelegramBridge sul grafico e Algo Trading acceso."
        return None

    # ---------------------------------------------------------------- operazioni
    def reload_trades(self):
        why = self._ready()
        if why:
            self._message(why)
            return
        if self._busy:
            return
        self._busy = True
        self._run(fetch(self.engine.bridge, {"action": "HISTORY", "days": 30}), self._got_history)

    def _got_history(self, result, err):
        self._busy = False
        if err:
            self._message(f"Errore di comunicazione con MetaTrader: {err}")
            return
        text, msg, now = result
        if msg:
            self._message(msg)
            return
        self.server_now = now or self.server_now
        trades = build_trades(parse_deals(text or ""), int(self.engine.settings["magic"]),
                              self.engine.my_positions(), self.engine.my_orders())
        current = self.want_key if self.want_key is not None else self.cb.currentData()
        self.want_key = None
        same = [t.key for t in trades] == [t.key for t in self.trades]
        self.trades = trades
        if not trades:
            self.cb.blockSignals(True)
            self.cb.clear()
            self.cb.blockSignals(False)
            self._message("Nessuna operazione del programma negli ultimi 30 giorni.")
            for lbl in self.info:
                lbl.hide()
            return
        self.cb.blockSignals(True)
        if not same:
            self.cb.clear()
            for t in trades:
                self.cb.addItem(t.label(), t.key)
        else:
            for i, t in enumerate(trades):
                self.cb.setItemText(i, t.label())
        idx = self.cb.findData(current) if current is not None else -1
        self.cb.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb.blockSignals(False)
        sel = self.selected()
        state = (sel.key, sel.is_open) if sel else None
        self.load_chart(keep_view=(state == self._last_state))   # appena si chiude: torna sull'operazione

    def selected(self) -> Trade | None:
        key = self.cb.currentData()
        return next((t for t in self.trades if t.key == key), None)

    def _set_tf(self, name):
        self.tf_choice = name
        self.load_chart(keep_view=False)

    def _tf_for(self, tr: Trade) -> str:
        if self.tf_choice != "Auto":
            return self.tf_choice
        if tr.entry_time is None:
            return "M15"
        end = tr.exit_time or self.server_now or tr.entry_time
        return auto_timeframe(max(end - tr.entry_time, 60) * 2)

    def load_chart(self, keep_view=False):
        tr = self.selected()
        if tr is None:
            return
        why = self._ready()
        if why:
            self._message(why)
            return
        self._show_info(tr)
        tf = self._tf_for(tr)
        start, stop = rates_range(tr, tf, self.server_now or int(dt.datetime.now().timestamp()))
        cmd = {"action": "RATES", "symbol": tr.symbol, "tf": tf, "from": max(0, start), "to": stop}
        self._last_key = tr.key
        self._last_state = (tr.key, tr.is_open)
        self._run(fetch(self.engine.bridge, cmd), lambda r, e: self._got_rates(r, e, tr, tf, keep_view))

    def _got_rates(self, result, err, tr, tf, keep_view):
        if self.selected() is None or self.selected().key != tr.key:
            return
        if err:
            self._message(f"Errore di comunicazione con MetaTrader: {err}")
            return
        text, msg, now = result
        if msg:
            self._message(msg)
            return
        self.server_now = now or self.server_now
        self.chart.set_data(parse_rates(text or ""), tr, tf, keep_view=keep_view)
        self._show_info(tr)

    def _show_info(self, tr: Trade):
        buy = tr.side == "buy"
        d = self.chart.digits
        col = gui.GREEN if tr.profit >= 0 else gui.RED
        texts = [
            f'<span style="color:{gui.GREEN if buy else gui.RED}">{"▲ BUY" if buy else "▼ SELL"}</span>&nbsp; {tr.symbol}',
            f"Lotti {tr.volume:g}",
            f"Entrata {tr.entry_price:.{d}f}",
            (f"Uscita {tr.exit_price:.{d}f}" if tr.exit_price else ("Ordine in attesa" if tr.is_order else "Aperta")),
            "" if tr.is_order else f'Risultato <span style="color:{col}">{tr.profit:+.2f}</span>',
        ]
        for lbl, t in zip(self.info, texts):
            lbl.setText(t)
            lbl.setVisible(bool(t))

    def _tick(self):
        tr = self.selected()
        if self.isVisible() and (tr is None or tr.is_open):
            self.reload_trades()

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)
