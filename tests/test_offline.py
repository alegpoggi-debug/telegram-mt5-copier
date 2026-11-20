"""Test completi senza Telegram e senza MetaTrader: simula l'EA con lo stesso protocollo a file
e verifica lettura dei messaggi, tutte le impostazioni, gestione automatica e pulsanti.

Eseguire con:  python -m tests.test_offline
"""
import asyncio
import collections
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp())
import app.config as config  # noqa: E402

config.DATA_DIR = TMP / "data"
config.DATA_DIR.mkdir()
import app.engine as engine_mod  # noqa: E402

engine_mod.DATA_DIR = config.DATA_DIR
from app.bridge import _parse_kv  # noqa: E402
from app.settings import DEFAULTS  # noqa: E402

SYMS = {"XAUUSD.m": (0.01, 2, 2000.0), "EURUSD.m": (0.00001, 5, 1.08), "US30.cash": (0.1, 1, 39000.0),
        "GBPUSD.m": (0.00001, 5, 1.27)}
PASSED = []


def check(cond, name, detail=""):
    if not cond:
        raise AssertionError(f"FALLITO: {name} {detail}")
    PASSED.append(name)


class FakeEA:
    def __init__(self, d: Path):
        self.dir = d / "TGBridge"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.positions, self.orders = {}, {}
        self.next_ticket = 1000
        self.trade_mode = 0
        self.equity = 10000.0
        self.price = {k: v[2] for k, v in SYMS.items()}
        self.received = []
        (self.dir / "symbols.txt").write_text("\r\n".join(SYMS) + "\r\n")

    def opens(self, since=0):
        return [c for c in self.received[since:] if c["action"] == "OPEN"]

    def write_state(self):
        lines = [f"account|123456|{self.trade_mode}|10000.00|{self.equity:.2f}|EUR|9000.00|Demo-Server", "time|x"]
        used = set()
        for t, p in self.positions.items():
            used.add(p["symbol"])
            pc = self.price[p["symbol"]]
            lines.append(f"pos|{t}|{p['symbol']}|{0 if p['side'] == 'BUY' else 1}|{p['volume']}|{p['price']}|{p['sl']}|{p['tp']}|0.00|{p['magic']}|{p['comment']}|{pc}")
        for t, o in self.orders.items():
            used.add(o["symbol"])
            lines.append(f"ord|{t}|{o['symbol']}|ORDER_TYPE_{o['side']}_{o['otype']}|{o['volume']}|{o['price']}|{o['sl']}|{o['tp']}|{o['magic']}|{o['comment']}")
        for s in used:
            pt, dg, _ = SYMS[s]
            lines.append(f"sym|{s}|{pt}|{dg}|{self.price[s]}|{self.price[s]}")
        tmp = self.dir / "state.tmp"
        tmp.write_text("\r\n".join(lines) + "\r\n")
        os.replace(tmp, self.dir / "state.txt")

    async def run(self):
        while True:
            for f in sorted(self.dir.glob("cmd_*.txt")):
                kv = _parse_kv(f.read_text())
                f.unlink()
                self.received.append(kv)
                res = self.handle(kv)
                cid = f.name[4:-4]
                tmp = self.dir / f"rtmp_{cid}"
                tmp.write_text("\r\n".join(f"{k}={v}" for k, v in res.items()) + "\r\n")
                os.replace(tmp, self.dir / f"res_{cid}.txt")
            self.write_state()
            await asyncio.sleep(0.03)

    def handle(self, kv):
        a = kv["action"]
        if a == "OPEN":
            sym = kv["symbol"]
            pt = SYMS[sym][0]
            buy = kv["side"] == "BUY"
            price = self.price[sym] if kv["otype"] == "MARKET" else float(kv["price"])
            if kv["otype"] == "MARKET" and float(kv["max_dist"]) > 0 and float(kv["entry_ref"]) > 0 \
                    and abs(price - float(kv["entry_ref"])) > float(kv["max_dist"]) * pt:
                return {"ok": 0, "msg": "troppo lontano", "retcode": 0}
            sl, tp = float(kv["sl"]), float(kv["tp"])
            if sl <= 0 and float(kv["sl_points"]) > 0:
                sl = price - float(kv["sl_points"]) * pt if buy else price + float(kv["sl_points"]) * pt
            if tp <= 0 and float(kv["tp_points"]) > 0:
                tp = price + float(kv["tp_points"]) * pt if buy else price - float(kv["tp_points"]) * pt
            if sl > 0 and float(kv["sl_adj"]):
                sl = sl - float(kv["sl_adj"]) * pt if buy else sl + float(kv["sl_adj"]) * pt
            if tp > 0 and float(kv["tp_adj"]):
                tp = tp - float(kv["tp_adj"]) * pt if buy else tp + float(kv["tp_adj"]) * pt
            vol = float(kv["volume"])
            if vol <= 0:
                vol = min(0.05, float(kv["max_lot"]))  # finto calcolo da rischio
            t = self.next_ticket
            self.next_ticket += 1
            rec = dict(symbol=sym, side=kv["side"], otype=kv["otype"], volume=round(vol, 2), price=price,
                       sl=round(sl, 5), tp=round(tp, 5), magic=int(kv["magic"]), comment=kv["comment"])
            (self.positions if kv["otype"] == "MARKET" else self.orders)[t] = rec
            return {"ok": 1, "ticket": t, "retcode": 10009, "msg": "done", "price": price, "volume": rec["volume"]}
        if a == "MODIFY":
            t = int(kv["ticket"])
            p = self.positions.get(t) or self.orders.get(t)
            if p is None:
                return {"ok": 0, "msg": "non trovato"}
            if float(kv["sl"]) >= 0:
                p["sl"] = round(float(kv["sl"]), 5)
            if float(kv["tp"]) >= 0:
                p["tp"] = round(float(kv["tp"]), 5)
            return {"ok": 1, "ticket": t, "retcode": 10009, "msg": "done"}
        if a == "CLOSE":
            t, v = int(kv["ticket"]), float(kv["volume"])
            if v <= 0:
                self.positions.pop(t)
            else:
                self.positions[t]["volume"] = round(self.positions[t]["volume"] - round(v, 2), 2)
            return {"ok": 1, "ticket": t, "retcode": 10009, "msg": "done"}
        if a == "CANCEL":
            self.orders.pop(int(kv["ticket"]), None)
            return {"ok": 1, "ticket": kv["ticket"], "retcode": 10009, "msg": "done"}
        return {"ok": 0, "msg": "?"}


class UI:
    def __init__(self):
        self.logs = []

    def on_log(self, t, level="info"):
        self.logs.append((level, t))

    def on_status(self, st):
        self.last = st


def msg(mid, text, reply=None):
    return types.SimpleNamespace(id=mid, message=text, reply_to_msg_id=reply)


async def main():
    common = TMP / "Common/Files"
    ea = FakeEA(common)
    ea.write_state()
    ui = UI()
    eng = engine_mod.Engine(ui)
    eng.loop = asyncio.get_running_loop()
    eng.lock = asyncio.Lock()
    eng.settings.update({"mt5_dir": str(common), "dry_run": False, "be_on_tp1": True})
    eng.setup_bridge()
    CID = -100
    eng.channels = {CID: "Canale Test", -200: "Altro Canale"}
    eng.recent_ids = {CID: collections.deque(maxlen=50), -200: collections.deque(maxlen=50)}
    asyncio.create_task(ea.run())

    async def refresh():
        await asyncio.sleep(0.12)
        eng.state = eng.bridge.read_state()
        eng._track_day()

    async def send(mid, text, reply=None, cid=CID, edited=False):
        await eng._handle(msg(mid, text, reply), cid, edited)
        await refresh()

    async def reset(**kw):
        ea.positions.clear()
        ea.orders.clear()
        base = {k: v for k, v in DEFAULTS.items() if not k.startswith("tg_")}
        base.update({"mt5_dir": str(common), "dry_run": False, "ignore_duplicates": False})
        base.update(kw)
        eng._seen.clear()
        eng.settings.update(base)
        eng._prev_positions = {}
        await refresh()

    await refresh()
    check(eng.mt5_connected(), "collegamento MT5")

    # ---------------------------------------------------------- simboli
    check(eng.resolve_symbol("XAUUSD") == "XAUUSD.m", "suffisso broker XAUUSD")
    check(eng.resolve_symbol("US30") == "US30.cash", "suffisso broker US30")
    check(eng.resolve_symbol("CADCHF") is None, "simbolo inesistente")

    # ---------------------------------------------------------- 1. segnale con 2 TP, una posizione per TP
    await reset()
    await send(1, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020")
    pos = sorted(eng.my_positions(), key=lambda p: p["tp"])
    check(len(pos) == 2 and [p["tp"] for p in pos] == [2010, 2020], "split per TP", pos)
    check(all(p["volume"] == 0.01 and p["sl"] == 1990 and p["comment"] == "TG#1" for p in pos), "lotto fisso e SL")

    # stesso messaggio ricevuto due volte -> non riapre
    await send(1, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020")
    check(len(eng.my_positions()) == 2, "nessun doppione")

    # ---------------------------------------------------------- 2. SL a pareggio in risposta (offset 10 punti)
    await send(2, "SL a pareggio", reply=1)
    check(all(abs(p["sl"] - 2000.10) < 1e-6 for p in eng.my_positions()), "pareggio + offset", eng.my_positions())

    # ---------------------------------------------------------- 3. chiusura parziale
    await reset(fixed_lot=0.10)
    await send(10, "SELL EURUSD 1.0800 SL 1.0850 TP 1.0700")
    await send(11, "chiudete metà", reply=10)
    check(eng.my_positions()[0]["volume"] == 0.05, "chiusura 50%", eng.my_positions())
    await send(12, "close 40%", reply=10)
    check(eng.my_positions()[0]["volume"] == 0.03, "chiusura 40%", eng.my_positions())
    await send(13, "Close all now", reply=10)
    check(not eng.my_positions(), "chiusura totale")

    # ---------------------------------------------------------- 4. ripartizione lotto 50,30,20
    await reset(fixed_lot=0.10, lot_split="50,30,20", max_lot_per_trade=1)
    await send(20, "XAUUSD BUY 2000\nSL 1990\nTP1 2005\nTP2 2010\nTP3 2020")
    vols = sorted(p["volume"] for p in eng.my_positions())
    check(vols == [0.02, 0.03, 0.05], "ripartizione 50/30/20", vols)

    # ---------------------------------------------------------- 5. solo primo / ultimo / TP n°2
    for i, (mode, exp) in enumerate((("first", 2005), ("last", 2020), ("index", 2010))):
        await reset(multi_tp_mode=mode, tp_index=2)
        await send(30 + i, "XAUUSD BUY 2000\nSL 1990\nTP1 2005\nTP2 2010\nTP3 2020")
        p = eng.my_positions()
        check(len(p) == 1 and p[0]["tp"] == exp, f"multi TP = {mode}", (p, ui.logs[-4:]))

    # ---------------------------------------------------------- 6. segnale senza SL
    await reset(no_sl_mode="ignore")
    await send(40, "BUY GOLD 2000 TP 2010")
    check(not eng.my_positions(), "senza SL: ignora")
    await reset(no_sl_mode="default", default_sl_points=500)
    await send(41, "BUY GOLD 2000 TP 2010")
    p = eng.my_positions()
    check(len(p) == 1 and abs(p[0]["sl"] - 1995.0) < 1e-6, "senza SL: SL predefinito 500 punti", p)

    # ---------------------------------------------------------- 7. senza TP -> predefinito
    await reset(no_tp_mode="default", default_tp_points=300)
    await send(42, "SELL GOLD 2000 SL 2010")
    p = eng.my_positions()
    check(len(p) == 1 and abs(p[0]["tp"] - 1997.0) < 1e-6, "senza TP: TP predefinito", p)

    # ---------------------------------------------------------- 8. aggiusta SL/TP
    await reset(sl_adjust_points=100, tp_adjust_points=50)
    await send(43, "BUY GOLD 2000 SL 1990 TP 2010")
    p = eng.my_positions()[0]
    check(abs(p["sl"] - 1989.0) < 1e-6 and abs(p["tp"] - 2009.5) < 1e-6, "aggiustamento SL/TP", p)

    # ---------------------------------------------------------- 9. inverti segnali
    await reset(reverse=True, default_sl_points=1000, default_tp_points=1000)
    await send(44, "BUY GOLD 2000 SL 1990 TP 2010")
    p = eng.my_positions()[0]
    check(p["side"] == "sell" and abs(p["sl"] - 2010) < 1e-6 and abs(p["tp"] - 1990) < 1e-6, "segnale invertito", p)

    # ---------------------------------------------------------- 10. filtri
    await reset(direction_filter="sell")
    await send(50, "BUY GOLD 2000 SL 1990 TP 2010")
    check(not eng.my_positions(), "solo SELL")
    await reset(blocked_symbols="XAUUSD")
    await send(51, "BUY GOLD 2000 SL 1990 TP 2010")
    check(not eng.my_positions(), "simbolo bloccato (alias GOLD)")
    await reset(allowed_symbols="EURUSD, GBPUSD")
    await send(52, "BUY GOLD 2000 SL 1990 TP 2010")
    await send(53, "BUY GBPUSD 1.27 SL 1.26 TP 1.28")
    check([p["symbol"] for p in eng.my_positions()] == ["GBPUSD.m"], "simboli ammessi")
    await reset(paused=True)
    await send(54, "BUY GOLD 2000 SL 1990 TP 2010")
    check(not eng.my_positions(), "pausa")
    import datetime as dt
    await reset(days=[d for d in range(7) if d != dt.datetime.now().weekday()])
    await send(55, "BUY GOLD 2000 SL 1990 TP 2010")
    check(not eng.my_positions(), "giorno non attivo")
    now = dt.datetime.now()
    await reset(hours_enabled=True, hours_start=(now + dt.timedelta(hours=1)).strftime("%H:%M"),
          hours_end=(now + dt.timedelta(hours=2)).strftime("%H:%M"))
    await send(56, "BUY GOLD 2000 SL 1990 TP 2010")
    check(not eng.my_positions(), "fuori orario")
    await reset(hours_enabled=True, hours_start=(now - dt.timedelta(minutes=5)).strftime("%H:%M"),
          hours_end=(now + dt.timedelta(minutes=5)).strftime("%H:%M"))
    await send(57, "BUY GOLD 2000 SL 1990 TP 2010")
    check(len(eng.my_positions()) == 1, "dentro l'orario")

    # ---------------------------------------------------------- 11. limiti
    await reset(max_open_trades=3)
    await send(60, "XAUUSD BUY 2000\nSL 1990\nTP1 2005\nTP2 2010\nTP3 2020\nTP4 2030")
    check(len(eng.my_positions()) == 3, "max posizioni aperte (taglia i TP in eccesso)")
    await reset(max_trades_per_symbol=1)
    await send(61, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020")
    await send(62, "BUY EURUSD 1.08 SL 1.07 TP 1.09")
    check(len(eng.my_positions()) == 2, "max per simbolo")
    await reset(fixed_lot=0.5, max_lot_per_trade=0.2)
    await send(63, "BUY GOLD 2000 SL 1990 TP 2010")
    check(eng.my_positions()[0]["volume"] == 0.2, "lotto massimo per posizione")
    await reset(fixed_lot=0.2, max_lot_per_trade=1, max_total_lots=0.3)
    await send(64, "BUY GOLD 2000 SL 1990 TP 2010")
    await send(65, "BUY EURUSD 1.08 SL 1.07 TP 1.09")
    await send(66, "BUY GBPUSD 1.27 SL 1.26 TP 1.28")
    check(sum(p["volume"] for p in eng.my_positions()) <= 0.3 + 1e-9, "lotti totali massimi",
          [p["volume"] for p in eng.my_positions()])

    # ---------------------------------------------------------- 12. rischio
    n0 = len(ea.received)
    await reset(lot_mode="risk_percent", risk_percent=2)
    await send(70, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020")
    o = ea.opens(n0)
    check(len(o) == 2 and all(float(c["risk_money"]) == 100 and float(c["volume"]) == 0 for c in o), "rischio % diviso per TP", o)
    n0 = len(ea.received)
    await reset(lot_mode="risk_money", risk_money=30, lot_split="70,30")
    await send(71, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020")
    check(sorted(float(c["risk_money"]) for c in ea.opens(n0)) == [9.0, 21.0], "importo fisso con ripartizione")

    # ---------------------------------------------------------- 13. perdita / obiettivo giornaliero
    await reset(daily_loss_limit=100)
    ea.equity = 9850
    await refresh()
    eng.day = {"date": "x", "start_equity": 10000}
    await send(72, "BUY GOLD 2000 SL 1990 TP 2010")
    check(not eng.my_positions(), "perdita giornaliera")
    await reset(daily_profit_target=100)
    ea.equity = 10150
    await refresh()
    eng.day = {"date": "x", "start_equity": 10000}
    await send(73, "BUY GOLD 2000 SL 1990 TP 2010")
    check(not eng.my_positions(), "obiettivo giornaliero")
    ea.equity = 10000
    (config.DATA_DIR / "day.json").unlink(missing_ok=True)
    await refresh()

    # ---------------------------------------------------------- 14. conto reale
    await reset()
    ea.trade_mode = 2
    await refresh()
    await send(74, "BUY GOLD 2000 SL 1990 TP 2010")
    check(not eng.my_positions(), "conto reale bloccato")
    await reset(allow_real=True)
    await send(75, "BUY GOLD 2000 SL 1990 TP 2010")
    check(len(eng.my_positions()) == 1, "conto reale permesso")
    ea.trade_mode = 0

    # ---------------------------------------------------------- 15. ordini pendenti, scadenza, cancellazione
    n0 = len(ea.received)
    await reset(pending_expiry_min=60)
    await send(80, "EURUSD SELL LIMIT 1.0900 SL 1.0950 TP 1.0800")
    check(len(eng.my_orders()) == 1 and ea.opens(n0)[0]["expiry_min"] == "60", "ordine limit con scadenza")
    await send(81, "Ordine cancellato", reply=80)
    check(not eng.my_orders(), "cancellazione pendente")
    await reset(order_type_mode="market")
    await send(82, "EURUSD SELL LIMIT 1.0900 SL 1.0950 TP 1.0800")
    check(len(eng.my_positions()) == 1 and not eng.my_orders(), "forza ordini a mercato")
    await reset(order_type_mode="limit")
    await send(83, "BUY GOLD 1995 SL 1990 TP 2010")
    check(len(eng.my_orders()) == 1, "forza ordini pendenti all'entrata")

    # ---------------------------------------------------------- 16. distanza massima dall'entrata
    await reset(max_entry_distance_points=100)
    await send(84, "BUY GOLD 1990 SL 1980 TP 2010")   # prezzo 2000, entrata 1990 = 1000 punti
    check(not eng.my_positions(), "prezzo troppo lontano")
    await send(85, "BUY GOLD 1999.5 SL 1980 TP 2010")
    check(len(eng.my_positions()) == 1, "prezzo vicino all'entrata")

    # ---------------------------------------------------------- 17. aggiornamenti senza risposta
    await reset()
    await send(90, "BUY GOLD 2000 SL 1990 TP 2010")
    await send(91, "BUY EURUSD 1.08 SL 1.07 TP 1.09")
    await send(92, "SL a pareggio")
    p = {x["symbol"]: x for x in eng.my_positions()}
    check(p["EURUSD.m"]["sl"] > 1.07 and p["XAUUSD.m"]["sl"] == 1990, "senza risposta = ultimo segnale", p)
    await reset(updates_target="all_channel")
    await send(93, "BUY GOLD 2000 SL 1990 TP 2010")
    await send(94, "BUY EURUSD 1.08 SL 1.07 TP 1.09")
    await send(95, "SL a pareggio")
    check(all(x["sl"] in (2000.1, 1.0801) for x in eng.my_positions()), "senza risposta = tutto il canale", eng.my_positions())
    await send(96, "BUY GOLD 2000 SL 1990 TP 2010", cid=-200)
    await send(97, "Close all now")
    check([x["symbol"] for x in eng.my_positions()] == ["XAUUSD.m"], "non tocca gli altri canali", eng.my_positions())

    # ---------------------------------------------------------- 18. nuovo SL / disattivazioni
    await reset()
    await send(100, "BUY GOLD 2000 SL 1990 TP 2010")
    await send(101, "Nuovo SL a 1995", reply=100)
    check(eng.my_positions()[0]["sl"] == 1995, "nuovo SL dal canale")
    eng.settings.update({"follow_close": False})
    await send(102, "Close all now", reply=100)
    check(len(eng.my_positions()) == 1, "'chiudete' disattivato")

    # ---------------------------------------------------------- 19. TP raggiunto -> azioni
    await reset(tp_hit_action="partial_be", tp_hit_partial_percent=50, fixed_lot=0.1)
    await send(110, "BUY GOLD 2000 SL 1990 TP 2010")
    await send(111, "TP1 HIT ✅", reply=110)
    p = eng.my_positions()[0]
    check(p["volume"] == 0.05 and abs(p["sl"] - 2000.1) < 1e-6, "TP raggiunto: parziale + pareggio", p)

    # ---------------------------------------------------------- 20. messaggio modificato
    await reset()
    await send(120, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020")
    await send(120, "BUY GOLD 2000 SL 1985 TP 2012 TP 2025", edited=True)
    p = sorted(eng.my_positions(), key=lambda x: x["tp"])
    check([x["sl"] for x in p] == [1985, 1985] and [x["tp"] for x in p] == [2012, 2025], "modifica da messaggio editato", p)

    # ---------------------------------------------------------- 21. gestione automatica
    await reset(be_on_tp1=True)
    await send(130, "BUY GOLD 2000 SL 1990 TP 2010 TP 2020")
    await eng._auto_manage()
    tp1 = min(eng.my_positions(), key=lambda x: x["tp"])["ticket"]
    ea.price["XAUUSD.m"] = 2010.0
    await refresh()
    await eng._auto_manage()
    ea.positions.pop(tp1)               # chiusa dal broker al TP1
    await refresh()
    await eng._auto_manage()
    await refresh()
    p = eng.my_positions()
    check(len(p) == 1 and abs(p[0]["sl"] - 2000.1) < 1e-6, "TP1 preso → pareggio automatico", p)
    ea.price["XAUUSD.m"] = 2000.0

    await reset(be_after_points=500, be_on_tp1=False)
    await send(131, "BUY GOLD 2000 SL 1990 TP 2050")
    ea.price["XAUUSD.m"] = 2006.0
    await refresh()
    await eng._auto_manage()
    await refresh()
    check(abs(eng.my_positions()[0]["sl"] - 2000.1) < 1e-6, "pareggio dopo X punti")

    await reset(trailing_points=300, trailing_step_points=10, be_on_tp1=False)
    ea.price["XAUUSD.m"] = 2000.0
    await send(132, "BUY GOLD 2000 SL 1990 TP 2050")
    ea.price["XAUUSD.m"] = 2008.0
    await refresh()
    eng._last_modify.clear()
    await eng._auto_manage()
    await refresh()
    check(abs(eng.my_positions()[0]["sl"] - 2005.0) < 1e-6, "trailing stop", eng.my_positions())
    ea.price["XAUUSD.m"] = 2000.0

    # ---------------------------------------------------------- 22. pulsanti
    await reset(fixed_lot=0.1)
    await send(140, "BUY GOLD 2000 SL 1990 TP 2010")
    await send(141, "BUY EURUSD 1.08 SL 1.07 TP 1.09")
    t_eur = [x["ticket"] for x in eng.my_positions() if x["symbol"] == "EURUSD.m"]
    await eng.button("close", t_eur, 50)
    await refresh()
    check({x["symbol"]: x["volume"] for x in eng.my_positions()}["EURUSD.m"] == 0.05, "pulsante chiudi 50% selezionate")
    await eng.button("breakeven", None)
    await refresh()
    check(all(x["sl"] > 1.07 and x["sl"] != 1990 for x in eng.my_positions()), "pulsante pareggio su tutte")
    await eng.button("close", None, 100)
    await refresh()
    check(not eng.my_positions(), "pulsante chiudi tutto")

    # ---------------------------------------------------------- 23. modalità prova
    n0 = len(ea.received)
    await reset(dry_run=True)
    await send(150, "BUY GOLD 2000 SL 1990 TP 2010")
    check(len(ea.received) == n0, "modalità prova: nessun ordine inviato")
    check(any("[PROVA" in t for _, t in ui.logs[-3:]), "modalità prova: mostra cosa farebbe")

    # ---------------------------------------------------------- 24. parole chiave personalizzate + prova messaggio
    s = eng.settings.get()
    s["kw_buy"] += ", ACQUISTARE ORA"
    s["kw_ignore"] = "PROMO"
    s["symbol_aliases"] += "\nLINGOTTO=XAUUSD"
    out = eng.test_message("acquistare ora lingotto 2000 stop loss 1990 obiettivo 2010", dict(s, kw_tp=s["kw_tp"] + ", OBIETTIVO"))
    check("SEGNALE BUY" in out and "XAUUSD" in out and "TP 2010" in out and "Verrebbe inviato" in out, "parole personalizzate", out)
    out = eng.test_message("PROMO: BUY GOLD 2000 SL 1990 TP 2010", s)
    check(out.startswith("Ignorato"), "parole da ignorare", out)

    # ---------------------------------------------------------- 25. messaggi sconosciuti / simbolo mancante
    await reset()
    await send(160, "Buongiorno! Oggi mercati volatili")
    await send(161, "BUY 2000 SL 1990 TP 2010")
    check(not eng.my_positions(), "simbolo mancante → ignorato")
    await reset(default_symbol="XAUUSD")
    await send(162, "BUY 2000 SL 1990 TP 2010")
    check(len(eng.my_positions()) == 1, "simbolo predefinito")

    # stato serializzabile per l'interfaccia
    json.dumps(eng.status_snapshot(), default=str)

    print(f"TUTTI I {len(PASSED)} TEST SUPERATI")


if __name__ == "__main__":
    asyncio.run(main())
