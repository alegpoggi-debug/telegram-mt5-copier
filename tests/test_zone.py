"""Test: attesa del prezzo nella fascia di entrata, spread massimo, avviso di slippage.
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
        self.fill_offset = 0.0
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
                if kv["action"] == "QUOTE" and kv.get("symbol", "").startswith("EURUSD"):
                    res = {"ok": 1, "price": 1.08500, "msg": "ask:1.08502 point:0.00001000"}
                elif kv["action"] == "QUOTE":
                    res = {"ok": 1, "price": self.bid, "msg": f"ask:{self.ask:.2f} point:0.01000000"}
                elif kv["action"] == "OPEN":
                    t = self.next
                    self.next += 1
                    price = round((self.ask if kv["side"] == "BUY" else self.bid) + self.fill_offset, 2)
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
                     "max_total_lots": 99, "ignore_duplicates": False, "days": list(range(7))})
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

    # G2) prezzo già migliore di quello del segnale: entra subito (sempre attivo)
    BUY2 = "BUY GOLD 2410-2412 SL 2395 TP 2425"
    SELL2 = "SELL GOLD 2410-2412 SL 2425 TP 2395"
    ea.bid, ea.ask = 2405.80, 2406.00          # 400 punti sotto la fascia
    opens, el, logs = await signal(BUY2, zone_wait=True, zone_wait_seconds=3, zone_margin_points=300)
    check(len(opens) == 1 and el < 1.5 and any("migliore di quello del segnale" in x for x in logs),
          "BUY con prezzo più basso della fascia: entra subito", (el, logs))
    opens, el, logs = await signal(SELL2, zone_wait=True, zone_wait_seconds=2, zone_margin_points=300)
    check(not opens and el > 1.8, "SELL con prezzo più basso (guadagno minore): aspetta e non entra", el)
    ea.bid, ea.ask = 2416.00, 2416.20          # 400 punti sopra la fascia
    opens, el, _ = await signal(SELL2, zone_wait=True, zone_wait_seconds=3, zone_margin_points=300)
    check(len(opens) == 1 and el < 1.5, "SELL con prezzo più alto della fascia: entra subito", el)
    opens, el, _ = await signal(BUY2, zone_wait=True, zone_wait_seconds=2, zone_margin_points=300)
    check(not opens and el > 1.8, "BUY con prezzo più alto (guadagno minore): aspetta e non entra", el)
    ea.bid, ea.ask = 2393.80, 2394.00          # BUY: prezzo già sotto lo SL (2395)
    opens, el, _ = await signal(BUY2, zone_wait=True, zone_wait_seconds=2, zone_margin_points=300)
    check(not opens, "prezzo oltre lo Stop Loss: non entra")
    ea.bid, ea.ask = 2405.80, 2406.00
    opens, el, _ = await signal("BUY GOLD NOW 2412 SL 2395 TP 2425", zone_wait=True, zone_wait_seconds=3,
                                zone_margin_points=300)
    check(len(opens) == 1 and el < 1.5, "un solo prezzo, BUY più basso: entra subito", el)

    # G3) troppo vicino allo Stop Loss (40 pips = 4 $ sull'oro): non entra
    ea.bid, ea.ask = 2402.80, 2403.00          # BUY, SL 2400: a 30 pips dallo SL
    opens, el, logs = await signal("BUY GOLD NOW SL 2400 TP 2420", zone_wait=False, zone_wait_seconds=2,
                                   min_sl_distance_pips=40)
    check(not opens and any("a 30 pips dallo Stop Loss (minimo 40)" in x for x in logs),
          "BUY a 30 pips dallo SL: non entra", logs)
    ea.bid, ea.ask = 2404.80, 2405.00          # a 50 pips
    opens, el, _ = await signal("BUY GOLD NOW SL 2400 TP 2420", zone_wait=False, min_sl_distance_pips=40)
    check(len(opens) == 1 and el < 1.5, "BUY a 50 pips dallo SL: entra", el)
    ea.bid, ea.ask = 2403.80, 2404.00          # esattamente 40 pips: «o meno» → non entra
    opens, el, _ = await signal("BUY GOLD NOW SL 2400 TP 2420", zone_wait=False, zone_wait_seconds=1,
                                min_sl_distance_pips=40)
    check(not opens, "BUY a 40 pips esatti dallo SL: non entra")
    ea.bid, ea.ask = 2417.00, 2417.20          # SELL, SL 2420: a 30 pips
    opens, el, _ = await signal("SELL GOLD NOW SL 2420 TP 2400", zone_wait=False, zone_wait_seconds=1,
                                min_sl_distance_pips=40)
    check(not opens, "SELL a 30 pips dallo SL: non entra")
    opens, el, _ = await signal("SELL GOLD NOW SL 2420 TP 2400", zone_wait=False, min_sl_distance_pips=0)
    check(len(opens) == 1, "distanza 0 = nessun controllo: entra")
    ea.bid, ea.ask = 2402.80, 2403.00

    async def away():
        await asyncio.sleep(1)
        ea.bid, ea.ask = 2405.80, 2406.00
    asyncio.create_task(away())
    opens, el, logs = await signal("BUY GOLD NOW SL 2400 TP 2420", zone_wait=False, zone_wait_seconds=6,
                                   min_sl_distance_pips=40)
    check(len(opens) == 1 and 0.8 < el < 3.5, "il prezzo si allontana dallo SL entro il tempo: entra", (el, logs))
    ea.bid, ea.ask = 2402.80, 2403.00          # prezzo migliore della fascia ma troppo vicino allo SL
    opens, el, _ = await signal("BUY GOLD 2410-2412 SL 2400 TP 2425", zone_wait=True, zone_wait_seconds=1,
                                zone_margin_points=300, min_sl_distance_pips=40)
    check(not opens, "prezzo migliore ma vicino allo SL: non entra")

    # G4) prezzo non disponibile e solo il controllo dello SL: entra lo stesso (non si perde il segnale)
    real_quote = eng._quote

    async def no_quote(_sym):
        return None
    eng._quote = no_quote
    opens, el, logs = await signal("BUY GOLD NOW SL 2400 TP 2420", zone_wait=False, zone_wait_seconds=5,
                                   min_sl_distance_pips=40)
    check(len(opens) == 1 and el < 1.5 and any("non controllata" in x for x in logs),
          "prezzo non disponibile: entra e lo scrive", (el, logs))
    eng._quote = real_quote

    # H) segnale con un solo prezzo (BUY NOW 2348): aspetta che il prezzo sia vicino a quello del segnale
    ea.bid, ea.ask = 2353.00, 2353.20
    opens, el, logs = await signal("BUY GOLD NOW 2348 SL 2340 TP 2360", zone_wait=True, zone_wait_seconds=2,
                                   zone_margin_points=50)
    check(not opens and 1.8 < el < 4.0 and any("lontano da quello del segnale (2348)" in x for x in logs),
          "un solo prezzo lontano: aspetta e poi lascia perdere", (el, logs))
    ea.bid, ea.ask = 2347.90, 2348.10
    opens, el, _ = await signal("BUY GOLD NOW 2348 SL 2340 TP 2360", zone_wait=True, zone_wait_seconds=2,
                                zone_margin_points=50)
    check(len(opens) == 1 and el < 1.5, "un solo prezzo vicino: entra subito", el)
    ea.bid, ea.ask = 2353.00, 2353.20

    async def comes_back():
        await asyncio.sleep(1)
        ea.bid, ea.ask = 2348.20, 2348.40
    asyncio.create_task(comes_back())
    opens, el, logs = await signal("BUY GOLD NOW 2348 SL 2340 TP 2360", zone_wait=True, zone_wait_seconds=6,
                                   zone_margin_points=50)
    check(len(opens) == 1 and 0.8 < el < 3.5 and any("vicino a quello del segnale: entro" in x for x in logs),
          "un solo prezzo: entra quando il prezzo arriva", (el, logs))
    ea.bid, ea.ask = 2353.00, 2353.20
    opens, el, _ = await signal("BUY GOLD NOW 2348 SL 2340 TP 2360", zone_wait=True, zone_wait_seconds=2,
                                zone_margin_points=600)
    check(len(opens) == 1 and el < 1.5, "margine ampio: entra", el)
    opens, el, _ = await signal("BUY GOLD NOW 2348 SL 2340 TP 2360", zone_wait=False)
    check(len(opens) == 1 and el < 1.5, "opzione spenta: entra subito come prima", el)
    opens, el, _ = await signal("BUY GOLD NOW SL 2340 TP 2360", zone_wait=True, zone_wait_seconds=2)
    check(len(opens) == 1 and el < 1.5, "segnale senza prezzo: entra subito", el)

    # I) più TP: un solo controllo, poi tutte le posizioni
    ea.bid, ea.ask = 2347.80, 2348.00
    opens, el, _ = await signal("BUY GOLD 2345-2350 SL 2340 TP 2355 TP 2360 TP 2370", zone_wait=True)
    check(len(opens) == 3, "più TP: apre tutte le posizioni", len(opens))

    # J) slippage: nessun limite (l'ordine non viene mai rifiutato), solo un avviso se l'esecuzione è peggiore
    ea.bid, ea.ask = 2347.80, 2348.00
    opens, el, logs = await signal(BUY)
    check(opens and opens[0]["deviation"] == "100000" and not any("slippage" in x for x in logs),
          "nessun limite di slippage, nessun avviso se il prezzo è quello previsto", (opens[0].get("deviation"), logs))
    ea.fill_offset = 0.60                      # il broker esegue 60 punti più in alto
    opens, el, logs = await signal(BUY)
    ea.fill_offset = 0.0
    check(len(opens) == 1 and any("60 punti peggio del previsto" in x and "resta aperta" in x for x in logs),
          "avviso di slippage, operazione aperta lo stesso", logs)
    ea.fill_offset = -0.60                     # eseguito a un prezzo migliore: nessun avviso
    opens, el, logs = await signal(BUY)
    ea.fill_offset = 0.0
    check(len(opens) == 1 and not any("slippage" in x for x in logs), "prezzo migliore: nessun avviso", logs)

    # K) modalità prova: aspetta ma non invia ordini
    ea.bid, ea.ask = 2353.00, 2353.20
    opens, el, logs = await signal(BUY, zone_wait=True, zone_wait_seconds=1, dry_run=True)
    check(not opens and any("lasciato perdere" in x for x in logs), "modalità prova: nessun ordine", logs)

    # L) ordini pendenti (limit): la fascia non si applica
    opens, el, _ = await signal("BUY LIMIT GOLD 2345-2350 SL 2340 TP 2360", zone_wait=True, zone_wait_seconds=1)
    check(len(opens) == 1 and opens[0]["otype"] == "LIMIT" and el < 1.5, "ordini limit: nessuna attesa")

    # M) impostazioni del singolo canale: lotto e pareggio diversi solo per quel canale
    ea.bid, ea.ask = 2347.80, 2348.00
    over = {"channel_overrides": {"-1": {"lot_mode": "fixed", "fixed_lot": 0.07, "be_offset_points": 44,
                                         "zone_wait": False}}}
    opens, el, _ = await signal(BUY, lot_mode="fixed", fixed_lot=0.02, zone_wait=True, zone_wait_seconds=5,
                                be_offset_points=5, **over)
    check(len(opens) == 1 and float(opens[0]["volume"]) == 0.07, "lotto del canale usato al posto del generale",
          opens and opens[0]["volume"])
    await asyncio.sleep(0.1)
    eng.state = eng.bridge.read_state()
    pos = eng.my_positions()
    check(pos and eng.pos_settings(pos[0])["be_offset_points"] == 44, "pareggio: margine del canale della posizione")
    check(eng.settings_for(-2)["fixed_lot"] == 0.02 and eng.settings_for(-2)["be_offset_points"] == 5,
          "altri canali: impostazioni generali")
    ea.bid, ea.ask = 2353.00, 2353.20          # fuori fascia: il canale ha l'attesa spenta → entra subito
    opens, el, _ = await signal(BUY, zone_wait=True, zone_wait_seconds=3, **over)
    check(len(opens) == 1 and el < 1.5, "attesa della fascia spenta solo per questo canale", el)
    opens, el, _ = await signal(BUY, lot_mode="fixed", fixed_lot=0.02, channel_overrides={})
    check(len(opens) == 1 and float(opens[0]["volume"]) == 0.02, "senza personalizzazioni: lotto generale")

    # N) mentre un segnale aspetta il prezzo, gli altri messaggi vengono eseguiti subito
    #    e non toccano l'operazione che non è ancora aperta
    base = {k: v for k, v in DEFAULTS.items() if not k.startswith("tg_")}
    base.update({"mt5_dir": str(common), "dry_run": False, "max_trades_per_symbol": 99, "max_open_trades": 99,
                 "max_total_lots": 99, "ignore_duplicates": False, "days": list(range(7)), "zone_wait": True,
                 "zone_wait_seconds": 6, "zone_margin_points": 50, "min_sl_distance_pips": 0, "follow_close": True})
    eng.settings.update(base)
    ea.positions.clear()
    ea.bid, ea.ask = 2347.80, 2348.00
    await asyncio.sleep(0.1)
    eng.state = eng.bridge.read_state()
    await eng._handle(types.SimpleNamespace(id=900, message=BUY, reply_to_msg_id=None), -1, False)
    first_ticket = max(ea.positions)
    await asyncio.sleep(0.15)
    eng.state = eng.bridge.read_state()
    ea.bid, ea.ask = 2360.00, 2360.20               # il prossimo segnale dovrà aspettare
    n0 = len(ea.received)
    waiting = asyncio.create_task(eng._handle(types.SimpleNamespace(
        id=901, message="BUY GOLD 2345-2350 SL 2340 TP 2370", reply_to_msg_id=None), -1, False))
    await asyncio.sleep(1.0)
    t0 = time.monotonic()
    await asyncio.wait_for(eng._handle(types.SimpleNamespace(id=902, message="chiudete", reply_to_msg_id=900), -1, False), 3)
    fast = time.monotonic() - t0
    closes = [c for c in ea.received[n0:] if c["action"] == "CLOSE"]
    check(fast < 1.5 and [int(c["ticket"]) for c in closes] == [first_ticket] and not waiting.done(),
          "durante l'attesa: «chiudete» eseguito subito sull'operazione già aperta", (fast, closes, waiting.done()))
    ea.bid, ea.ask = 2348.20, 2348.40               # il prezzo torna: il segnale in attesa entra
    await asyncio.wait_for(waiting, 10)
    opened = [c for c in ea.received[n0:] if c["action"] == "OPEN"]
    check(len(opened) == 1 and len([c for c in ea.received[n0:] if c["action"] == "CLOSE"]) == 1,
          "il segnale in attesa entra dopo e il «chiudete» di prima non lo chiude", (opened, closes))
    check(not eng.lock.locked(), "turno dei messaggi libero alla fine")

    print(f"TUTTI I {len(PASSED)} TEST DELLA FASCIA DI ENTRATA SUPERATI")


if __name__ == "__main__":
    asyncio.run(main())
