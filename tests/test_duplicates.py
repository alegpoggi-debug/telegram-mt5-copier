"""Test della protezione dai messaggi doppi.   python -m tests.test_duplicates"""
import asyncio
import collections
import os
import sys
import tempfile
import types
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["TGMT5_DATA"] = str(TMP / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import Engine  # noqa: E402
from tests.test_zone import FakeEA  # noqa: E402

PASSED = []


def check(cond, name, detail=""):
    if not cond:
        raise AssertionError(f"FALLITO: {name} {detail}")
    PASSED.append(name)


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
    ea.bid, ea.ask = 2347.8, 2348.0
    ea.write_state()
    ui = UI()
    eng = Engine(ui)
    eng.loop = asyncio.get_running_loop()
    eng.lock = asyncio.Lock()
    eng.settings.update({"mt5_dir": str(common), "dry_run": False, "max_open_trades": 99, "days": list(range(7)),
                         "max_trades_per_symbol": 99, "max_total_lots": 99})
    eng.setup_bridge()
    eng.channels = {-1: "Canale A", -2: "Canale B"}
    eng.recent_ids = {-1: collections.deque(maxlen=50), -2: collections.deque(maxlen=50)}
    asyncio.create_task(ea.run())
    mid = [0]

    async def msg(text, cid=-1, reply=None):
        await asyncio.sleep(0.1)
        eng.state = eng.bridge.read_state()
        n0, l0 = len(ea.received), len(ui.logs)
        mid[0] += 1
        await eng._handle(types.SimpleNamespace(id=mid[0], message=text, reply_to_msg_id=reply), cid, False)
        return [c for c in ea.received[n0:] if c["action"] in ("OPEN", "CLOSE", "MODIFY")], ui.logs[l0:], mid[0]

    S = "BUY GOLD 2345-2350 SL 2340 TP 2360"
    a, _, first = await msg(S)
    check(len(a) == 1, "primo segnale: aperto")
    a, logs, _ = await msg(S)
    check(not a and any("segnale doppio ignorato" in x for x in logs), "stesso messaggio ripetuto: ignorato", logs)
    a, _, _ = await msg("🔥 Buy gold 2345 - 2350\nSl: 2340\nTp: 2360")
    check(not a, "stesso segnale scritto diversamente: ignorato")
    a, _, _ = await msg(S, cid=-2)
    check(not a, "stesso segnale da un altro canale: ignorato")
    a, _, _ = await msg("BUY GOLD 2345-2350 SL 2338 TP 2360")
    check(len(a) == 1, "SL diverso: è un segnale nuovo")
    a, _, _ = await msg("SELL GOLD 2345-2350 SL 2355 TP 2330")
    check(len(a) == 1, "direzione diversa: è un segnale nuovo")

    # stesso segnale riscritto in modo diverso: è sempre un doppio
    for text, why in (("BUY GOLD 2350-2345 SL 2340 TP 2360", "fascia scritta al contrario"),
                      ("BUY GOLD 2348 SL 2340 TP 2360", "prezzo dentro la fascia"),
                      ("BUY GOLD NOW SL 2340 TP 2360", "senza prezzo (NOW)"),
                      ("BUY LIMIT GOLD 2345 SL 2340 TP 2360", "LIMIT invece che a mercato"),
                      ("BUY GOLD 2345-2350 SL 2340 TP 2360 TP 2370", "con un TP in più"),
                      ("📢 SEGNALE\nGold buy 2345/2350\nSL: 2340\nTP1: 2360\nBuona fortuna!", "con testo in più")):
        a, logs, _ = await msg(text)
        check(not a and any("doppio ignorato" in x for x in logs), f"riscritto ({why}): ignorato", (text, logs))
    a, _, _ = await msg("BUY GOLD 2346 SL 2339 TP 2361 TP 2356", cid=-2)
    b, _, _ = await msg("BUY GOLD 2346 SL 2339 TP 2356 TP 2361", cid=-2)
    check(len(a) == 2 and not b, "TP in ordine diverso: ignorato", (len(a), len(b)))
    # ma un segnale davvero diverso resta nuovo
    a, logs, _ = await msg("BUY GOLD 2352-2355 SL 2340 TP 2360")
    check(not a and any("doppio ignorato" in x for x in logs), "seconda entrata con stessi SL e TP: ignorata", logs)
    a, _, _ = await msg("BUY GOLD 2352-2355 SL 2340 TP 2358 TP 2360")
    check(len(a) == 2, "entrata diversa e TP1 diverso: segnale nuovo", len(a))
    a, _, _ = await msg("BUY GOLD 2341 SL 2336 TP 2360")
    check(len(a) == 1, "entrata e SL diversi (stesso TP): segnale nuovo")
    a, _, _ = await msg("BUY GOLD 2345-2350 SL 2340 TP 2365")
    check(len(a) == 1, "TP diverso: segnale nuovo")

    # gestione: "chiudete metà" ripetuto non chiude due volte
    await asyncio.sleep(0.2)
    a, _, _ = await msg("chiudete metà", reply=first)
    check(len([c for c in a if c["action"] == "CLOSE"]) == 1, "primo 'chiudete metà': eseguito")
    a, logs, _ = await msg("chiudete metà", reply=first)
    check(not a and any("messaggio doppio ignorato" in x for x in logs), "'chiudete metà' ripetuto: ignorato", logs)

    # finestra di tempo scaduta: lo stesso segnale torna valido
    for k in list(eng._seen):
        eng._seen[k] -= 11 * 60
    a, _, _ = await msg(S)
    check(len(a) == 1, "dopo la finestra di tempo: di nuovo valido")

    # protezione spenta: entra ogni volta
    eng.settings.update({"ignore_duplicates": False})
    a1, _, _ = await msg(S)
    a2, _, _ = await msg(S)
    check(len(a1) == 1 and len(a2) == 1, "protezione spenta: apre entrambi")

    # finestra di tempo personalizzata
    eng.settings.update({"ignore_duplicates": True, "duplicate_window_min": 2})
    eng._seen.clear()
    await msg(S)
    for k in list(eng._seen):
        eng._seen[k] -= 3 * 60
    a, _, _ = await msg(S)
    check(len(a) == 1, "finestra di 2 minuti rispettata")
    print(f"TUTTI I {len(PASSED)} TEST DEI MESSAGGI DOPPI SUPERATI")


if __name__ == "__main__":
    asyncio.run(main())
