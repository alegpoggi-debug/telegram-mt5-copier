"""Test del grafico (senza schermo, MetaTrader simulato con lo stesso protocollo a file).
QT_QPA_PLATFORM=offscreen python -m tests.test_chart
"""
import asyncio
import math
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = Path(tempfile.mkdtemp())
os.environ["TGMT5_DATA"] = str(TMP / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import telethon  # noqa: E402,F401  (caricato subito, come fa l'app all'avvio)
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import chart, gui  # noqa: E402
from app.bridge import _parse_kv  # noqa: E402
from app.engine import Engine  # noqa: E402

NOW = 1763550000          # "ora del broker"
TF = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
PASSED = []


def check(cond, name, detail=""):
    if not cond:
        raise AssertionError(f"FALLITO: {name} {detail}")
    PASSED.append(name)


def wait_until(cond, timeout=8.0):
    """Aspetta che una condizione diventi vera (MetaTrader simulato risponde in modo asincrono)."""
    end = time.time() + timeout
    while time.time() < end:
        QTest.qWait(50)
        if cond():
            return True
    return False


def price_at(t):
    return 2000 + 8 * math.sin(t / 5000) + 3 * math.sin(t / 700)


class FakeEA(threading.Thread):
    """Simula il ponte: stato, RATES e HISTORY."""

    def __init__(self, d: Path):
        super().__init__(daemon=True)
        self.dir = d / "TGBridge"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.old_bridge = False
        self.positions = {}
        self.deals = []
        self.received = []

    def state(self):
        lines = ["account|555|0|10000.00|10000.00|EUR|9000.00|Demo-Server", "time|x"]
        for t, p in self.positions.items():
            lines.append(f"pos|{t}|{p['symbol']}|{0 if p['side'] == 'buy' else 1}|{p['volume']}|{p['price']}|"
                         f"{p['sl']}|{p['tp']}|{p['profit']}|{p['magic']}|TG#1|{price_at(NOW):.2f}")
        tmp = self.dir / "state.tmp"
        tmp.write_text("\r\n".join(lines) + "\r\n")
        os.replace(tmp, self.dir / "state.txt")

    def run(self):
        while True:
            for f in sorted(self.dir.glob("cmd_*.txt")):
                kv = _parse_kv(f.read_text())
                f.unlink()
                self.received.append(kv)
                cid = f.name[4:-4]
                res = self.handle(kv, cid)
                tmp = self.dir / f"rtmp_{cid}"
                tmp.write_text("\r\n".join(f"{k}={v}" for k, v in res.items()) + "\r\n")
                os.replace(tmp, self.dir / f"res_{cid}.txt")
            self.state()
            time.sleep(0.03)

    def handle(self, kv, cid):
        a = kv["action"]
        if self.old_bridge and a in ("RATES", "HISTORY"):
            return {"ok": 0, "msg": f"azione sconosciuta: {a}", "price": 0}
        if a == "RATES":
            sec = TF[kv["tf"]]
            start = int(kv["from"]) // sec * sec
            stop = int(kv["to"]) or NOW
            lines = []
            t = start
            while t <= stop and len(lines) < 3000:
                o, c = price_at(t), price_at(t + sec)
                lines.append(f"{t}|{o:.2f}|{max(o, c) + 0.6:.2f}|{min(o, c) - 0.6:.2f}|{c:.2f}")
                t += sec
            (self.dir / f"data_{cid}.txt").write_text("\r\n".join(lines) + "\r\n")
            return {"ok": 1, "msg": f"data_{cid}.txt", "price": NOW, "volume": len(lines)}
        if a == "HISTORY":
            lines = [f"deal|{d[0]}|{d[1]}|{d[2]}|{d[3]}|{d[4]}|{d[5]}|{d[6]}|{d[7]}|{d[8]}|{d[9]}|x" for d in self.deals]
            (self.dir / f"data_{cid}.txt").write_text("\r\n".join(lines) + "\r\n")
            return {"ok": 1, "msg": f"data_{cid}.txt", "price": NOW}
        return {"ok": 0, "msg": "?"}


def main():
    # ------------------------------------------------ 1) raggruppamento delle transazioni in operazioni
    M = 770077
    deals = [  # ticket, pos, time, type(0 buy/1 sell), entry(0 in/1 out), price, vol, profit, symbol, magic
        (1, 100, NOW - 7200, 0, 0, 1990.0, 0.02, 0.0, "XAUUSD", M),      # BUY 0.02 aperto 2 ore fa
        (2, 100, NOW - 3600, 1, 1, 1995.0, 0.01, 5.0, "XAUUSD", 0),      # chiusura parziale (magic diverso!)
        (3, 100, NOW - 1800, 1, 1, 2001.0, 0.01, 11.0, "XAUUSD", 0),     # chiusura finale
        (4, 200, NOW - 900, 1, 0, 2003.0, 0.01, 0.0, "XAUUSD", M),       # SELL ancora aperto
        (5, 300, NOW - 600, 0, 0, 1.0800, 0.10, 0.0, "EURUSD", 123),     # operazione manuale: NON nostra
    ]
    positions = [{"ticket": 200, "symbol": "XAUUSD", "side": "sell", "volume": 0.01, "price_open": 2003.0,
                  "sl": 2010.0, "tp": 1990.0, "profit": 2.4, "magic": M, "comment": "TG#2", "price_current": 2000.6}]
    orders = [{"ticket": 400, "symbol": "EURUSD", "type": "ORDER_TYPE_BUY_LIMIT", "volume": 0.05, "price": 1.0750,
               "sl": 1.07, "tp": 1.09, "magic": M, "comment": "TG#3"}]
    dl = chart.parse_deals("\n".join(f"deal|{'|'.join(map(str, d))}|x" for d in deals))
    trades = chart.build_trades(dl, M, positions, orders)
    keys = [t.key for t in trades]
    check(300 not in keys, "le operazioni manuali non compaiono")
    check(set(keys) == {100, 200, 400} and trades[-1].key == 100, "operazioni aperte prima, chiuse dopo", keys)
    closed = next(t for t in trades if t.key == 100)
    check(closed.exit_time == NOW - 1800 and abs(closed.exit_price - 1998.0) < 1e-9 and closed.profit == 16.0,
          "operazione chiusa: uscita media e risultato", (closed.exit_time, closed.exit_price, closed.profit))
    check(len(closed.exits) == 2 and closed.side == "buy" and closed.volume == 0.02, "chiusure parziali")
    op = next(t for t in trades if t.key == 200)
    check(op.is_open and op.sl == 2010.0 and op.tp == 1990.0 and op.entry_time == NOW - 900, "operazione aperta con SL/TP")
    check(next(t for t in trades if t.key == 400).is_order, "ordine pendente")
    check(chart.auto_timeframe(2 * 3600) == "M5" and chart.auto_timeframe(60 * 40) == "M1", "scelta automatica del timeframe")
    s, e = chart.rates_range(closed, "M5", NOW)
    check(s < closed.entry_time and e > closed.exit_time, "intervallo candele contiene entrata e uscita")

    # ------------------------------------------------ 2) finestra del grafico con MetaTrader simulato
    app = QApplication([])
    ea = FakeEA(TMP / "Common/Files")
    ea.deals = list(deals)
    ea.positions = {200: {"symbol": "XAUUSD", "side": "sell", "volume": 0.01, "price": 2003.0, "sl": 2010.0,
                          "tp": 1990.0, "profit": 2.4, "magic": M}}
    ea.start()
    ui = gui.UiBridge()
    eng = Engine(ui)
    eng.settings.update({"mt5_dir": str(TMP / "Common/Files")})
    win = gui.MainWindow(ui, eng)
    win.show()
    eng.start()
    for _ in range(100):
        QTest.qWait(100)
        if eng.mt5_connected() and eng.my_positions():
            break
    check(eng.mt5_connected(), "MetaTrader simulato collegato")
    win.update_status(eng.status_snapshot())
    check(win.table.rowCount() == 1, "operazione aperta in tabella")

    # doppio clic sulla riga
    win.table.cellDoubleClicked.emit(0, 1)
    cw = chart.ChartWindow._instance
    wait_until(lambda: cw.chart.bars and cw.chart.trade is not None)
    check(cw is not None and cw.isVisible(), "doppio clic apre il grafico")
    check(cw.cb.currentData() == 200 and cw.cb.count() == 2, "selezionata l'operazione cliccata",
          (cw.cb.currentData(), cw.cb.count(), cw.chart.message, cw._busy, [c["action"] for c in ea.received]))
    check(len(cw.chart.bars) > 20 and cw.chart.trade.key == 200, "candele caricate", len(cw.chart.bars))
    check("SELL" in cw.info[0].text() and "Aperta" in cw.info[3].text(), "intestazione operazione")
    cw.resize(1080, 680)
    QTest.qWait(100)
    cw.chart.mouse = QPointF(500, 200)
    cw.grab().save(str(TMP / "grafico_aperta.png"))

    # cambio timeframe
    n0 = len(ea.received)
    for b in cw.tf_group.buttons():
        if b.text() == "M15":
            b.click()
    wait_until(lambda: cw.chart.tf == "M15" and cw.chart.bars)
    tfs = [c.get("tf") for c in ea.received[n0:] if c["action"] == "RATES"]
    check("M15" in tfs and cw.chart.tf == "M15", "cambio timeframe", tfs)
    for b in cw.tf_group.buttons():
        if b.text() == "Auto":
            b.click()
    wait_until(lambda: cw.chart.tf != "M15")

    # zoom e trascinamento
    vc = cw.chart.vc
    ev = QWheelEvent(QPointF(400, 200), cw.chart.mapToGlobal(QPointF(400, 200)), QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    cw.chart.wheelEvent(ev)
    check(cw.chart.vc < vc, "zoom con la rotella", (vc, cw.chart.vc))
    QTest.mousePress(cw.chart, Qt.LeftButton, pos=QPoint(400, 200))
    QTest.mouseMove(cw.chart, QPoint(300, 200))
    QTest.mouseRelease(cw.chart, Qt.LeftButton, pos=QPoint(300, 200))
    cw.chart.repaint()
    PASSED.append("trascinamento senza errori")

    # operazione chiusa (dal menu)
    cw.cb.setCurrentIndex(cw.cb.findData(100))
    wait_until(lambda: cw.chart.trade and cw.chart.trade.key == 100 and "+16" in cw.info[4].text())
    check(cw.chart.trade.key == 100 and cw.chart.trade.exit_time and len(cw.chart.bars) > 10, "grafico operazione chiusa")
    check("+16.00" in cw.info[4].text() and "Uscita" in cw.info[3].text(), "risultato operazione chiusa", cw.info[4].text())
    cw.chart.mouse = None
    cw.grab().save(str(TMP / "grafico_chiusa.png"))

    # in diretta: l'operazione aperta si chiude → compare l'uscita
    cw.cb.setCurrentIndex(cw.cb.findData(200))
    wait_until(lambda: cw.chart.trade and cw.chart.trade.key == 200)
    ea.positions.clear()
    ea.deals.append((6, 200, NOW - 60, 0, 1, 1998.5, 0.01, 4.5, "XAUUSD", 0))
    wait_until(lambda: not eng.my_positions())
    cw._tick()
    wait_until(lambda: cw.selected() is not None and not cw.selected().is_open and cw.chart.trade.exit_time)
    tr = cw.selected()
    check(tr and tr.key == 200 and not tr.is_open and tr.exit_price == 1998.5 and tr.profit == 4.5,
          "chiusura in diretta mostrata", tr)
    cw.grab().save(str(TMP / "grafico_chiusa_live.png"))
    cw.close()

    # ------------------------------------------------ 3) ponte vecchio e MetaTrader scollegato
    ea.old_bridge = True
    win.open_chart(-1)
    cw = chart.ChartWindow._instance
    wait_until(lambda: "Installa il ponte" in cw.chart.message)
    check("Installa il ponte" in cw.chart.message and not cw.chart.bars, "messaggio se il ponte va aggiornato")
    cw.grab().save(str(TMP / "grafico_ponte_vecchio.png"))
    cw.close()
    ea.old_bridge = False
    real = eng.mt5_connected
    eng.mt5_connected = lambda: False
    win.open_chart(-1)
    QTest.qWait(300)
    check("MetaTrader non è collegato" in chart.ChartWindow._instance.chart.message, "messaggio se MetaTrader è chiuso")
    eng.mt5_connected = real
    chart.ChartWindow._instance.close()

    # nessuna operazione negli ultimi 30 giorni
    ea.deals = []
    win.open_chart(-1)
    wait_until(lambda: "Nessuna operazione" in chart.ChartWindow._instance.chart.message)
    check("Nessuna operazione" in chart.ChartWindow._instance.chart.message, "messaggio se non ci sono operazioni")

    print(f"TUTTI I {len(PASSED)} TEST DEL GRAFICO SUPERATI")
    print("screenshot:", TMP)
    os._exit(0)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
