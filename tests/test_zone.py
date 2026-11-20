"""Test: attesa del prezzo nella fascia di entrata, spread massimo, slippage.
python -m tests.test_zone
"""
import asyncio
import collections
import os
import sys
import tempfile
import time
import types
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["TGMT5_DATA"] = str(TMP / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bridge import _parse_kv  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.settings import DEFAULTS  # noqa: E402

PASSED = []


def check(cond, name, detail=""):
    if not cond:
        raise AssertionError(f"FALLITO: {name} {detail}")
    PASSED.append(name)


class FakeEA:
    """Ponte simulato: OPEN e QUOTE, prezzo dell'oro che si può muovere durante il test."""

    def __init__(self, d: Path):
        self.dir = d / "TGBridge"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "symbols.txt").write_text("XAUUSD.m\r\nEURUSD.m\r\n")
        self.bid, self.ask = 2353.00, 2353.20
        self.received, self.positions = [], {}
        self.next = 1000

    def write_state(self):
        lines = ["account|1|0|10000.00|10000.00|EUR|9000.00|Demo", "time|x"]
        for t, p in self.positions.items():
            lines.append(f"pos|{t}|{p['symbol']}|{0 if p['side'] == 'BUY' else 1}|{p['volume']}|{p['price']}|0|0|0|"
                         f"{p['magic']}|{p['comment']}|{self.bid}")
        (self.dir / "state.tmp").write_text("\r\n".join(lines) + "\r\n")
        os.replace(self.dir / "state.tmp", self.dir / "state.txt")

    async def run(self):
        while True:
            for f in sorted(self.dir.glob("cmd_*.txt")):
                kv = _parse_kv(f.read_text())
                f.unlink()
                kv["_t"] = time.monotonic()
                self.received.append(kv)
                if kv["action"] == "QUOTE":
                    res = {"ok": 1, "price": self.bid, "msg": f"ask:{self.ask:.2f} point:0.01000000"}
                elif kv["action"] == "OPEN":
                    t = self.next
                    self.next += 1
                    price = self.ask if kv["side"] == "BUY" else self.bid
                    self.positions[t] = dict(symbol=kv["symbol"], side=kv["side"], volume=float(kv["volume"]),
                                             price=price, magic=int(kv["magic"]), comment=kv["comment"])
                    res = {"ok": 1, "ticket": t, "retcode": 10009, "msg": "done", "price": price, "volume": kv["volume"]}
                else:
                    res = {"ok": 0, "msg": "?"}
                cid = f.name[4:-4]
                (self.dir / f"rtmp_{cid}").write_text("\r\n".join(f"{k}={v}" for k, v in res.items()) + "\r\n")
                os.replace(self.dir / f"rtmp_{cid}", self.dir / f"res_{cid}.txt")
            self.write_state()
            await asyncio.sleep(0.03)

    def opens(self, since):
        return [c for c in self.received[since:] if c["action"] == "OPEN"]


class UI:
    def __init__(self):
        self.logs = []

    def on_log(self, t, level="info"):
        self.logs.append(t)

    def on_status(self, st):
        pass


async def main():
    common = TMP / "Common/Files"
    ea = FakeEA(common)
    ea.write_state()
    ui = UI()
    eng = Engine(ui)
    eng.loop = asyncio.get_running_loop()
    eng.lock = asyncio.Lock()
    eng.settings.update({"mt5_dir": str(common), "dry_run": False})
    eng.setup_bridge()
    eng.channels = {-1: "Canale"}
    eng.recent_ids = {-1: collections.deque(maxlen=50)}
    asyncio.create_task(ea.run())
    await asyncio.sleep(0.2)
    eng.state = eng.bridge.read_state()
    mid = [100]

    async def signal(text, **settings):
        base = {k: v for k, v in DEFAULTS.items() if not k.startswith("tg_")}
        base.update({"mt5_dir": str(common), "dry_run": False, "max_trades_per_symbol": 99, "max_open_trades": 99,
                     "max_total_lots": 99, "ignore_duplicates": False})
        base.update(settings)
        eng.settings.update(base)
        ea.positions.clear()
        await asyncio.sleep(0.1)
        eng.state = eng.bridge.read_state()
        n0, n_log = len(ea.received), len(ui.logs)
        mid[0] += 1
        t0 = time.monotonic()
        await eng._handle(types.SimpleNamespace(id=mid[0], message=text, reply_to_msg_id=None), -1, False)
        return ea.opens(n0), time.monotonic() - t0, ui.logs[n_log:]

    BUY = "BUY GOLD 2345-2350 SL 2340 TP 2360"

    # A) opzione spenta: comportamento di prima (entra subito anche fuori fascia)
    ea.bid, ea.ask = 2353.00, 2353.20
    opens, el, _ = await signal(BUY, zone_wait=False)
    check(len(opens) == 1 and el < 1.5, "opzione spenta: entra subito come prima", (len(opens), el))

    # B) prezzo dentro la fascia: entra subito
    ea.bid, ea.ask = 2347.80, 2348.00
    opens, el, logs = await signal(BUY, zone_wait=True, zone_wait_seconds=10)
    check(len(opens) == 1 and el < 1.5 and not any("aspetto" in x for x in logs), "dentro la fascia: entra subito", (el, logs))

    # C) fuori fascia, rientra dopo 2 secondi: aspetta ed entra
    ea.bid, ea.ask = 2353.00, 2353.20

    async def back_in():
        await asyncio.sleep(2)
        ea.bid, ea.ask = 2349.30, 2349.50
    asyncio.create_task(back_in())
    opens, el, logs = await signal(BUY, zone_wait=True, zone_wait_seconds=10)
    check(len(opens) == 1 and 1.8 < el < 4.5, "fuori fascia: aspetta e entra quando rientra", (len(opens), el))
    check(any("fuori dalla fascia 2345–2350" in x and "aspetto fino a 10 secondi" in x for x in logs)
          and any("nella fascia: entro" in x for x in logs), "messaggi chiari in Attività", logs)

    # D) fuori fascia, non rientra entro il tempo: lascia perdere
    ea.bid, ea.ask = 2353.00, 2353.20
    opens, el, logs = await signal(BUY, zone_wait=True, zone_wait_seconds=2)
    check(not opens and 1.8 < el < 4.0, "non rientra in tempo: segnale ignorato", (len(opens), el))
    check(any("lasciato perdere" in x for x in logs), "spiegazione del segnale ignorato", logs)

    # E) margine fuori fascia
    opens, el, _ = await signal(BUY, zone_wait=True, zone_wait_seconds=2, zone_margin_points=400)
    check(len(opens) == 1 and el < 1.5, "margine: accetta prezzo appena fuori", el)

    # F) SELL: usa il prezzo bid
    ea.bid, ea.ask = 2362.00, 2362.30
    opens, el, _ = await signal("SELL GOLD 2360-2365 SL 2370 TP 2350", zone_wait=True, zone_wait_seconds=2)
    check(len(opens) == 1 and el < 1.5, "SELL dentro la fascia (prezzo bid)", el)
    ea.bid, ea.ask = 2358.00, 2358.30
    opens, el, _ = await signal("SELL GOLD 2360-2365 SL 2370 TP 2350", zone_wait=True, zone_wait_seconds=1)
    check(not opens, "SELL sotto la fascia: non entra")

    # G) spread massimo
    ea.bid, ea.ask = 2347.50, 2348.00          # spread 50 punti
    opens, el, logs = await signal(BUY, zone_wait=True, zone_wait_seconds=1, max_spread_points=30)
    check(not opens and any("spread 50 punti" in x for x in logs), "spread troppo alto: non entra", logs)

    async def narrow():
        await asyncio.sleep(1)
        ea.bid, ea.ask = 2347.80, 2348.00
    asyncio.create_task(narrow())
    opens, el, _ = await signal(BUY, zone_wait=True, zone_wait_seconds=5, max_spread_points=30)
    check(len(opens) == 1 and 0.8 < el < 3.5, "spread si stringe: entra", el)
    ea.bid, ea.ask = 2353.00, 2353.50
    opens, el, logs = await signal(BUY, zone_wait=False, max_spread_points=30, zone_wait_seconds=0)
    check(not opens and el < 1.5, "solo spread massimo (senza fascia): controllato anche da solo", logs)

    # H) segnale con un solo prezzo: non cambia nulla
    ea.bid, ea.ask = 2353.00, 2353.20
    opens, el, _ = await signal("BUY GOLD 2348 SL 2340 TP 2360", zone_wait=True, zone_wait_seconds=2)
    check(len(opens) == 1 and el < 1.5, "un solo prezzo: entra come prima", el)

    # I) più TP: un solo controllo, poi tutte le posizioni
    ea.bid, ea.ask = 2347.80, 2348.00
    opens, el, _ = await signal("BUY GOLD 2345-2350 SL 2340 TP 2355 TP 2360 TP 2370", zone_wait=True)
    check(len(opens) == 3, "più TP: apre tutte le posizioni", len(opens))

    # J) slippage massimo inviato a MetaTrader
    opens, el, _ = await signal(BUY, slippage_points=55)
    check(opens and opens[0]["deviation"] == "55", "slippage massimo passato a MetaTrader", opens[0].get("deviation"))

    # K) modalità prova: aspetta ma non invia ordini
    ea.bid, ea.ask = 2353.00, 2353.20
    opens, el, logs = await signal(BUY, zone_wait=True, zone_wait_seconds=1, dry_run=True)
    check(not opens and any("lasciato perdere" in x for x in logs), "modalità prova: nessun ordine", logs)

    # L) ordini pendenti (limit): la fascia non si applica
    opens, el, _ = await signal("BUY LIMIT GOLD 2345-2350 SL 2340 TP 2360", zone_wait=True, zone_wait_seconds=1)
    check(len(opens) == 1 and opens[0]["otype"] == "LIMIT" and el < 1.5, "ordini limit: nessuna attesa")

    print(f"TUTTI I {len(PASSED)} TEST DELLA FASCIA DI ENTRATA SUPERATI")


if __name__ == "__main__":
    asyncio.run(main())
