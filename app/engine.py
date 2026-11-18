"""Motore: collega Telegram, il lettore dei messaggi, le impostazioni e MetaTrader 5."""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
import json
import logging
import re
import threading
import time
from pathlib import Path

from .bridge import Bridge, BridgeError, find_common_files_dir
from .config import DATA_DIR
from .parser import describe, parse_message
from .settings import SettingsStore, split_words

log = logging.getLogger("engine")


class SignalDB:
    """Ricorda da quale messaggio Telegram nasce ogni operazione."""

    def __init__(self, path: Path):
        self.path = path
        self.data = {"next_key": 1, "signals": {}}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def new(self, channel, chat_id, msg_id, text, parsed) -> int:
        key = self.data["next_key"]
        self.data["next_key"] += 1
        self.data["signals"][str(key)] = {
            "channel": channel, "chat_id": chat_id, "msg_id": msg_id, "text": text[:1500],
            "time": dt.datetime.now().isoformat(timespec="seconds"), "tickets": [],
            "side": parsed.get("side"), "symbol": parsed.get("symbol"), "sl": parsed.get("sl"), "tps": parsed.get("tps"),
        }
        # tengo solo gli ultimi 500 segnali
        if len(self.data["signals"]) > 500:
            for k in sorted(self.data["signals"], key=int)[:-500]:
                del self.data["signals"][k]
        self.save()
        return key

    def add_ticket(self, key, ticket):
        s = self.data["signals"].get(str(key))
        if s is not None and ticket and ticket not in s["tickets"]:
            s["tickets"].append(ticket)
            self.save()

    def by_message(self, chat_id, msg_id):
        for k, s in self.data["signals"].items():
            if s["chat_id"] == chat_id and s["msg_id"] == msg_id:
                return int(k), s
        return None, None

    def key_for(self, ticket, comment="") -> int | None:
        m = re.search(r"TG#(\d+)", comment or "")
        if m:
            return int(m.group(1))
        for k, s in self.data["signals"].items():
            if ticket in s["tickets"]:
                return int(k)
        return None

    def get(self, key):
        return self.data["signals"].get(str(key))


class Engine:
    """Gira in un thread separato con il proprio event loop. `ui` ha on_log(text, level) e on_status(dict)."""

    def __init__(self, ui):
        self.ui = ui
        self.settings = SettingsStore(DATA_DIR / "settings.json")
        self.db = SignalDB(DATA_DIR / "signals.json")
        self.bridge: Bridge | None = None
        self.tg = None
        self.tg_creds = None
        self.tg_authorized = False
        self.channels: dict[int, str] = {}
        self.recent_ids: dict[int, collections.deque] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lock: asyncio.Lock | None = None
        self.state: dict | None = None
        self.day = None
        self._prev_positions: dict[int, dict] = {}
        self._last_modify: dict[int, float] = {}
        self._phone = None
        self._handlers_added = False

    # ================================================================ avvio
    def start(self):
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True).start()
        ready.wait(5)

    def _run(self, ready):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.lock = asyncio.Lock()
        ready.set()
        try:
            self.loop.run_until_complete(self._main())
        except Exception as e:
            log.exception("motore fermo")
            self.ui.on_log(f"Errore grave del motore: {e}", "error")

    def call(self, coro, timeout=60):
        """Esegue una coroutine nel thread del motore e aspetta il risultato (per l'interfaccia)."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _main(self):
        self.setup_bridge()
        asyncio.create_task(self._state_loop())
        try:
            await self.start_telegram()
        except Exception as e:
            log.exception("avvio Telegram")
            self.ui.on_log(f"Telegram non si è collegato ({e}). Impostazioni → Telegram → Accedi di nuovo.", "error")
        await asyncio.Event().wait()

    # ================================================================ MT5
    def setup_bridge(self, path_override: str | None = None) -> Path | None:
        d = path_override or self.settings["mt5_dir"]
        path = Path(d).expanduser() if d else find_common_files_dir()
        if path is None or not path.exists():
            self.bridge = None
            self.ui.on_log("MetaTrader 5 non trovato. Installa/avvia MT5, poi Impostazioni → MetaTrader → Trova.", "warn")
            return None
        self.bridge = Bridge(path)
        self.ui.on_log(f"Cartella di collegamento MT5: {self.bridge.dir}", "info")
        return path

    def mt5_connected(self) -> bool:
        return bool(self.bridge and self.bridge.is_connected())

    async def _state_loop(self):
        while True:
            try:
                self.state = self.bridge.read_state() if self.bridge else None
                if self.mt5_connected():
                    self._track_day()
                    await self._auto_manage()
                self.ui.on_status(self.status_snapshot())
            except Exception:
                log.exception("errore ciclo stato")
            await asyncio.sleep(1)

    def _track_day(self):
        acc = (self.state or {}).get("account")
        if not acc:
            return
        p = DATA_DIR / "day.json"
        today = dt.date.today().isoformat()
        day = {}
        if p.exists():
            try:
                day = json.loads(p.read_text())
            except ValueError:
                day = {}
        if day.get("date") != today:
            day = {"date": today, "start_equity": acc["equity"]}
            p.write_text(json.dumps(day))
        self.day = day

    def my_positions(self):
        m = int(self.settings["magic"])
        return [p for p in (self.state or {}).get("positions", []) if p["magic"] == m]

    def my_orders(self):
        m = int(self.settings["magic"])
        return [o for o in (self.state or {}).get("orders", []) if o["magic"] == m]

    def status_snapshot(self) -> dict:
        s = self.settings.get()
        acc = (self.state or {}).get("account")
        today_pl = None
        if acc and self.day:
            today_pl = acc["equity"] - self.day["start_equity"]
        return {
            "telegram": bool(self.tg and self.tg.is_connected() and self.tg_authorized and self.channels),
            "telegram_authorized": self.tg_authorized,
            "channels": list(self.channels.values()),
            "mt5": self.mt5_connected(),
            "mt5_found": self.bridge is not None,
            "dry_run": s["dry_run"], "paused": s["paused"], "allow_real": s["allow_real"],
            "account": acc, "today_pl": today_pl,
            "positions": self.my_positions(), "orders": self.my_orders(),
        }

    # ================================================================ Telegram
    async def start_telegram(self):
        from telethon import TelegramClient, events

        s = self.settings.get()
        api_id, api_hash = str(s["tg_api_id"]).strip(), str(s["tg_api_hash"]).strip()
        if not api_id or not api_hash:
            self.ui.on_log("Telegram non configurato: Impostazioni → Telegram.", "warn")
            return False
        creds = (api_id, api_hash)
        if self.tg is None or self.tg_creds != creds:
            if self.tg is not None:
                await self.tg.disconnect()
            try:
                self.tg = TelegramClient(str(DATA_DIR / "telegram"), int(api_id), api_hash)
            except ValueError:
                self.ui.on_log("api_id di Telegram non valido (deve essere un numero).", "error")
                return False
            self.tg_creds = creds
            self._handlers_added = False
        if not self.tg.is_connected():
            try:
                await self.tg.connect()
            except Exception as e:
                self.ui.on_log(f"Connessione a Telegram non riuscita: {e}", "error")
                return False
        self.tg_authorized = await self.tg.is_user_authorized()
        if not self.tg_authorized:
            self.ui.on_log("Telegram: accesso non ancora fatto. Impostazioni → Telegram → Accedi.", "warn")
            return False
        await self._load_channels()
        if not self._handlers_added:
            self.tg.add_event_handler(self._on_new, events.NewMessage())
            self.tg.add_event_handler(self._on_edit, events.MessageEdited())
            self._handlers_added = True
        return True

    async def _load_channels(self):
        self.channels = {}
        for c in self.settings["tg_channels"]:
            self.channels[int(c["id"])] = c["name"]
            self.recent_ids.setdefault(int(c["id"]), collections.deque(maxlen=50))
        if not self.channels:
            self.ui.on_log("Nessun canale scelto: Impostazioni → Telegram → seleziona i canali.", "warn")
        for name in self.channels.values():
            self.ui.on_log(f"In ascolto sul canale: {name}", "ok")

    async def tg_send_code(self, phone: str):
        await self.start_telegram()
        self._phone = phone.strip().replace(" ", "")
        await self.tg.send_code_request(self._phone)

    async def tg_sign_in(self, code: str) -> str:
        from telethon.errors import SessionPasswordNeededError
        try:
            await self.tg.sign_in(self._phone, code.strip())
        except SessionPasswordNeededError:
            return "password"
        return await self._after_login()

    async def tg_password(self, pw: str) -> str:
        await self.tg.sign_in(password=pw)
        return await self._after_login()

    async def _after_login(self) -> str:
        me = await self.tg.get_me()
        await self.start_telegram()
        return f"{me.first_name or ''} {('@' + me.username) if me.username else ''}".strip()

    async def tg_me(self) -> str | None:
        if not (self.tg and self.tg_authorized):
            return None
        me = await self.tg.get_me()
        return f"{me.first_name or ''} {('@' + me.username) if me.username else ''}".strip()

    async def tg_list_chats(self) -> list[dict]:
        out = []
        async for d in self.tg.iter_dialogs():
            if d.is_channel or d.is_group:
                out.append({"id": d.id, "name": d.name or str(d.id)})
        return out

    async def tg_logout(self):
        if self.tg:
            await self.tg.log_out()
        self.tg_authorized = False
        self.channels = {}

    async def reload_channels(self):
        if self.tg_authorized:
            await self._load_channels()

    # ================================================================ messaggi
    async def _on_new(self, event):
        if event.chat_id in self.channels:
            await self._handle(event.message, event.chat_id, edited=False)

    async def _on_edit(self, event):
        if event.chat_id in self.channels and self.settings["follow_edits"]:
            await self._handle(event.message, event.chat_id, edited=True)

    def broker_symbols(self):
        return self.bridge.read_symbols() if self.bridge else []

    async def _handle(self, m, cid, edited: bool):
        text = (m.message or "").strip()
        if not text:
            return
        async with self.lock:
            s = self.settings.get()
            channel = self.channels.get(cid, str(cid))
            if not edited:
                if m.id in self.recent_ids.setdefault(cid, collections.deque(maxlen=50)):
                    return
                self.recent_ids[cid].append(m.id)
            self.ui.on_log(f"[{channel}] {'messaggio MODIFICATO' if edited else 'nuovo messaggio'}:\n{text}", "msg")
            r = parse_message(text, s, self.broker_symbols())
            self.ui.on_log(f"Lettura: {describe(r)}", "ai")
            try:
                results = await self.process(r, s, cid, channel, m.id, getattr(m, "reply_to_msg_id", None), text, edited)
            except Exception as e:
                log.exception("errore elaborazione")
                results = [f"errore: {e}"]
            for x in results:
                self.ui.on_log(f"  → {x}", "trade")

    async def process(self, r, s, cid, channel, msg_id, reply_id, text, edited) -> list[str]:
        kind = r["kind"]
        if kind == "signal":
            key, existing = self.db.by_message(cid, msg_id)
            if existing and edited:
                return await self._update_from_edit(r, existing)
            if existing:
                return ["segnale già eseguito"]
            if not r["symbol"]:
                return ["simbolo non riconosciuto: aggiungilo in Impostazioni → Orari e simboli → Nomi alternativi"]
            key = self.db.new(channel, cid, msg_id, text, r)
            return await self.open_signal(r, s, key)
        if kind in ("none", "ignore"):
            return []
        targets = self._targets(cid, reply_id, r.get("symbol"), s)
        pos = [t for t in targets if t[0] == "pos"]
        ords = [t for t in targets if t[0] == "ord"]
        if kind == "cancel":
            if not s["follow_cancel"]:
                return ["'cancella' del canale disattivato nelle impostazioni"]
            return await self.cancel([o[1]["ticket"] for o in ords]) if ords else ["nessun ordine pendente da cancellare"]
        if not targets:
            return ["nessuna operazione aperta a cui applicare il messaggio"]
        if kind == "breakeven":
            if not s["follow_breakeven"]:
                return ["'SL a pareggio' del canale disattivato nelle impostazioni"]
            return await self.breakeven([p[1]["ticket"] for p in pos])
        if kind == "move_sl":
            if not s["follow_move_sl"]:
                return ["'nuovo SL' del canale disattivato nelle impostazioni"]
            return await self.modify([t[1]["ticket"] for t in targets], sl=r["new_sl"])
        if kind == "close":
            if not s["follow_close"]:
                return ["'chiudete' del canale disattivato nelle impostazioni"]
            out = await self.close([p[1]["ticket"] for p in pos], 100)
            if ords:
                out += await self.cancel([o[1]["ticket"] for o in ords])
            return out
        if kind == "partial":
            if not s["follow_partial"]:
                return ["'chiusura parziale' del canale disattivata nelle impostazioni"]
            out = await self.close([p[1]["ticket"] for p in pos], r.get("percent") or s["partial_percent"])
            if r.get("also_breakeven") and s["follow_breakeven"]:
                await asyncio.sleep(1.2)
                self.state = self.bridge.read_state() if self.bridge else self.state
                out += await self.breakeven([p[1]["ticket"] for p in pos])
            return out
        if kind == "tp_hit":
            act = s["tp_hit_action"]
            out = []
            if act in ("partial", "partial_be"):
                out += await self.close([p[1]["ticket"] for p in pos], s["tp_hit_partial_percent"])
            if act in ("breakeven", "partial_be"):
                if out:
                    await asyncio.sleep(1.2)
                    self.state = self.bridge.read_state() if self.bridge else self.state
                out += await self.breakeven([p[1]["ticket"] for p in pos])
            return out or ["impostato: non fare nulla al 'TP raggiunto'"]
        return []

    def _targets(self, cid, reply_id, symbol, s):
        """Posizioni/ordini a cui si applica un messaggio di gestione."""
        items = [("pos", p) for p in self.my_positions()] + [("ord", o) for o in self.my_orders()]
        keyed = []
        for kind, it in items:
            k = self.db.key_for(it["ticket"], it["comment"])
            sig = self.db.get(k) if k else None
            if sig and sig["chat_id"] == cid:
                keyed.append((kind, it, k, sig))
        if reply_id:
            k, _ = self.db.by_message(cid, reply_id)
            if k:
                return [(a, b) for a, b, kk, _ in keyed if kk == k]
        if symbol:
            keyed = [x for x in keyed if _base(x[1]["symbol"]).startswith(_base(symbol)[:6])] or keyed
        if s["updates_target"] == "last_signal" and keyed:
            last = max(x[2] for x in keyed)
            keyed = [x for x in keyed if x[2] == last]
        return [(a, b) for a, b, _, _ in keyed]

    async def _update_from_edit(self, r, sig) -> list[str]:
        tickets = [t for t in sig["tickets"] if self._find(t)[1]]
        if not tickets:
            return ["messaggio modificato: nessuna operazione aperta collegata"]
        out = []
        new_tps = r["tps"]
        for i, t in enumerate(tickets):
            tp = new_tps[i] if len(new_tps) == len(tickets) else None
            if r["sl"] != sig.get("sl") or tp is not None:
                out += await self.modify([t], sl=r["sl"] if r["sl"] != sig.get("sl") else None, tp=tp)
        sig["sl"], sig["tps"] = r["sl"], r["tps"]
        self.db.save()
        return out or ["messaggio modificato: SL/TP invariati"]

    # ================================================================ apertura
    def resolve_symbol(self, raw: str) -> str | None:
        if not raw:
            return None
        broker = self.broker_symbols()
        up = _base(raw)
        if not broker:
            return raw.upper()
        exact = [b for b in broker if _base(b) == up]
        if exact:
            return sorted(exact, key=len)[0]
        pref = sorted([b for b in broker if _base(b).startswith(up)], key=len)
        if pref:
            return pref[0]
        alts = {"XAUUSD": ["GOLD"], "XAGUSD": ["SILVER"], "US30": ["DJ30", "WS30", "DOW", "USA30", "DJI"],
                "NAS100": ["USTEC", "NDX", "US100", "USTECH", "NQ100"], "US500": ["SPX500", "SP500", "USA500"],
                "GER40": ["DE40", "DAX40", "GER30", "DE30"], "USOIL": ["WTI", "XTIUSD", "CRUDE"],
                "UKOIL": ["BRENT", "XBRUSD"], "BTCUSD": ["BITCOIN", "BTCUSDT"]}
        for alt in alts.get(up, []):
            hit = sorted([b for b in broker if _base(b).startswith(alt)], key=len)
            if hit:
                return hit[0]
        return None

    def _blocked_reason(self, s, side, sym, raw) -> str | None:
        if s["paused"]:
            return "programma IN PAUSA"
        now = dt.datetime.now()
        if now.weekday() not in s["days"]:
            return "giorno non attivo"
        if s["hours_enabled"]:
            a, b, hm = s["hours_start"], s["hours_end"], now.strftime("%H:%M")
            inside = (a <= hm <= b) if a <= b else (hm >= a or hm <= b)
            if not inside:
                return f"fuori orario ({a}-{b})"
        if s["direction_filter"] != "both" and side != s["direction_filter"]:
            return f"accetto solo {s['direction_filter'].upper()}"
        names = {_base(sym), _base(raw)}
        blocked = [_base(x) for x in split_words(s["blocked_symbols"])]
        if any(n.startswith(b) for n in names for b in blocked if b):
            return f"{raw} è tra i simboli bloccati"
        allowed = [_base(x) for x in split_words(s["allowed_symbols"])]
        if allowed and not any(n.startswith(a) for n in names for a in allowed if a):
            return f"{raw} non è tra i simboli ammessi"
        acc = (self.state or {}).get("account") or {}
        if acc and self.day:
            pl = acc["equity"] - self.day["start_equity"]
            if s["daily_loss_limit"] and pl <= -s["daily_loss_limit"]:
                return "perdita massima giornaliera raggiunta"
            if s["daily_profit_target"] and pl >= s["daily_profit_target"]:
                return "obiettivo di profitto giornaliero raggiunto"
        return None

    def _ready(self, s, opening: bool) -> str | None:
        if s["dry_run"]:
            return None
        if not self.mt5_connected():
            return "MetaTrader non collegato (EA TelegramBridge non attivo o Algo Trading spento)"
        acc = (self.state or {}).get("account") or {}
        if opening and acc.get("trade_mode") == 2 and not s["allow_real"]:
            return "conto REALE: aperture bloccate (Impostazioni → Volume e rischio → Permetti conto reale)"
        return None

    def plan_signal(self, r, s) -> tuple[list[dict], str | None]:
        """Calcola gli ordini da inviare per un segnale. Ritorna (ordini, motivo_blocco)."""
        side = r["side"]
        if s["reverse"]:
            side = "sell" if side == "buy" else "buy"
        raw = r["symbol"] or ""
        sym = self.resolve_symbol(raw)
        if not sym:
            return [], f"simbolo '{raw}' non trovato sul broker: aggiungi un nome alternativo nelle impostazioni"
        why = self._blocked_reason(s, side, sym, raw)
        if why:
            return [], why

        sl = r["sl"]
        sl_points = 0
        if s["reverse"]:
            sl, tps_in = None, []  # con segnali invertiti SL/TP del canale non hanno senso: uso quelli predefiniti
        else:
            tps_in = list(r["tps"])
        if sl is None:
            if s["no_sl_mode"] == "ignore" and not s["reverse"]:
                return [], "segnale senza Stop Loss (impostazione: ignora)"
            if s["no_sl_mode"] == "default" or s["reverse"]:
                sl_points = s["default_sl_points"]
        tp_points = 0
        if not tps_in and (s["no_tp_mode"] == "default" or s["reverse"]):
            tp_points = s["default_tp_points"]

        mode = s["multi_tp_mode"]
        if tps_in:
            if mode == "first":
                tps_in = tps_in[:1]
            elif mode == "last":
                tps_in = tps_in[-1:]
            elif mode == "index":
                i = max(1, s["tp_index"]) - 1
                tps_in = [tps_in[min(i, len(tps_in) - 1)]]
        tps = tps_in or [0.0]
        n = len(tps)

        otype = r["order_type"]
        if s["order_type_mode"] == "market":
            otype = "market"
        elif s["order_type_mode"] == "limit" and r["entry"] is not None:
            otype = "limit"
        if otype in ("limit", "stop") and r["entry"] is None:
            otype = "market"
        if s["reverse"] and otype != "market":
            otype = "market"

        # limiti di numero posizioni e lotti
        if not s["dry_run"]:
            mine = self.my_positions() + self.my_orders()
            free = s["max_open_trades"] - len(mine)
            same = len([p for p in mine if p["symbol"] == sym])
            free = min(free, s["max_trades_per_symbol"] - same)
            if free <= 0:
                return [], "raggiunto il numero massimo di posizioni (totale o per simbolo)"
            if n > free:
                tps, n = tps[:free], free
            used = sum(p["volume"] for p in mine)
            room = s["max_total_lots"] - used
            if room <= 0:
                return [], "raggiunto il limite totale di lotti aperti"
        else:
            room = s["max_total_lots"]

        # ripartizione
        weights = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s["lot_split"] or "")][:n]
        if weights and sum(weights) > 0:
            if len(weights) < n:
                rest = max(0.0, 100 - sum(weights))
                weights += [rest / (n - len(weights))] * (n - len(weights))
            tot = sum(weights) or 1
            weights = [w / tot for w in weights]
            split_mode = True
        else:
            weights = [1 / n] * n
            split_mode = False

        acc = (self.state or {}).get("account") or {}
        cap = min(s["max_lot_per_trade"], room / n if not s["dry_run"] else s["max_lot_per_trade"])
        orders = []
        for tp, w in zip(tps, weights):
            vol, risk = 0.0, 0.0
            if s["lot_mode"] == "fixed":
                vol = s["fixed_lot"] * w if split_mode else s["fixed_lot"]
                vol = round(min(vol, cap), 4)
            elif s["lot_mode"] == "risk_percent":
                risk = round(acc.get("balance", 0) * s["risk_percent"] / 100 * w, 2)
            else:
                risk = round(s["risk_money"] * w, 2)
            orders.append({
                "action": "OPEN", "symbol": sym, "side": side.upper(), "otype": otype.upper(),
                "price": r["entry"] if otype != "market" else 0, "entry_ref": r["entry"] or 0,
                "sl": sl or 0, "tp": tp or 0, "volume": vol, "risk_money": risk, "max_lot": round(cap, 4),
                "sl_points": sl_points, "tp_points": tp_points if not tp else 0,
                "sl_adj": s["sl_adjust_points"], "tp_adj": s["tp_adjust_points"],
                "max_dist": s["max_entry_distance_points"] if otype == "market" else 0,
                "expiry_min": s["pending_expiry_min"] if otype != "market" else 0,
                "magic": s["magic"], "deviation": s["slippage_points"],
            })
        if s["lot_mode"] == "risk_percent" and not s["dry_run"] and not acc.get("balance"):
            return [], "saldo non disponibile per calcolare il rischio"
        return orders, None

    async def open_signal(self, r, s, key) -> list[str]:
        orders, why = self.plan_signal(r, s)
        if why:
            return [f"NON eseguito: {why}"]
        err = self._ready(s, opening=True)
        if err:
            return [f"NON eseguito: {err}"]
        out = []
        for o in orders:
            o["comment"] = f"TG#{key}" if key else "TG#manuale"
            desc = _desc(o)
            if s["dry_run"]:
                out.append(f"[PROVA - non inviato] {desc}")
                continue
            try:
                res = await self.bridge.send(o)
            except BridgeError as e:
                out.append(f"ERRORE {desc}: {e}")
                continue
            if res.get("ok") == "1":
                t = int(res.get("ticket") or 0)
                if key:
                    self.db.add_ticket(key, t)
                out.append(f"APERTO {desc} → ticket {t}, lotti {res.get('volume')}, prezzo {res.get('price')}")
            else:
                out.append(f"RIFIUTATO {desc}: {res.get('msg')} (codice {res.get('retcode')})")
        return out

    # ================================================================ gestione
    def _find(self, ticket):
        for p in self.my_positions():
            if p["ticket"] == ticket:
                return "pos", p
        for o in self.my_orders():
            if o["ticket"] == ticket:
                return "ord", o
        return None, None

    def _point(self, symbol) -> float:
        info = (self.state or {}).get("symbols", {}).get(symbol)
        return info["point"] if info else 0.0

    async def _send(self, cmd, desc) -> str:
        s = self.settings.get()
        if s["dry_run"]:
            return f"[PROVA - non inviato] {desc}"
        err = self._ready(s, opening=False)
        if err:
            return f"NON eseguito {desc}: {err}"
        try:
            res = await self.bridge.send(cmd)
        except BridgeError as e:
            return f"ERRORE {desc}: {e}"
        if res.get("ok") == "1":
            return f"OK {desc}"
        return f"RIFIUTATO {desc}: {res.get('msg')} (codice {res.get('retcode')})"

    async def modify(self, tickets, sl=None, tp=None) -> list[str]:
        out = []
        for t in tickets:
            kind, it = self._find(int(t))
            if not it:
                out.append(f"ticket {t} non trovato")
                continue
            if sl is None and tp is None:
                continue
            self._last_modify[int(t)] = time.time()
            out.append(await self._send({"action": "MODIFY", "ticket": t, "sl": -1 if sl is None else sl,
                                         "tp": -1 if tp is None else tp},
                                        f"modifica {it['symbol']} #{t} SL {sl if sl is not None else '='} TP {tp if tp is not None else '='}"))
        return out

    async def breakeven(self, tickets, offset_points=None) -> list[str]:
        s = self.settings.get()
        off = s["be_offset_points"] if offset_points is None else offset_points
        out = []
        for t in tickets:
            kind, p = self._find(int(t))
            if kind != "pos":
                continue
            pt = self._point(p["symbol"])
            be = p["price_open"] + (off * pt if p["side"] == "buy" else -off * pt)
            if p["sl"] and ((p["side"] == "buy" and p["sl"] >= be) or (p["side"] == "sell" and p["sl"] <= be)):
                out.append(f"#{t} già a pareggio o meglio")
                continue
            out += await self.modify([t], sl=round(be, 8))
        return out or ["nessuna posizione da portare a pareggio"]

    async def close(self, tickets, percent) -> list[str]:
        out = []
        for t in tickets:
            kind, p = self._find(int(t))
            if kind != "pos":
                out.append(f"posizione {t} non trovata")
                continue
            vol = 0 if percent >= 100 else round(p["volume"] * percent / 100, 4)
            out.append(await self._send({"action": "CLOSE", "ticket": t, "volume": vol,
                                         "deviation": self.settings["slippage_points"]},
                                        f"chiusura {percent:g}% {p['symbol']} #{t}"))
        return out or ["nessuna posizione"]

    async def cancel(self, tickets) -> list[str]:
        out = []
        for t in tickets:
            out.append(await self._send({"action": "CANCEL", "ticket": t}, f"cancella ordine #{t}"))
        return out or ["nessun ordine"]

    async def _auto_manage(self):
        s = self.settings.get()
        positions = {p["ticket"]: p for p in self.my_positions()}
        # 1) TP preso su una posizione → pareggio sulle altre dello stesso segnale
        if s["be_on_tp1"] and self._prev_positions:
            for t, old in self._prev_positions.items():
                if t in positions or not old["tp"]:
                    continue
                pc, tp, sl = old["price_current"], old["tp"], old["sl"]
                hit_tp = abs(pc - tp) < abs(pc - sl) if sl else True
                if not hit_tp:
                    continue
                k = self.db.key_for(t, old["comment"])
                if not k:
                    continue
                others = [p["ticket"] for p in positions.values() if self.db.key_for(p["ticket"], p["comment"]) == k]
                if others:
                    self.ui.on_log(f"TP preso su #{t}: sposto a pareggio le altre posizioni del segnale #{k}", "ok")
                    for x in await self.breakeven(others):
                        self.ui.on_log(f"  → {x}", "trade")
        self._prev_positions = positions
        # 2) pareggio dopo X punti e trailing stop
        for t, p in positions.items():
            if time.time() - self._last_modify.get(t, 0) < 5:
                continue
            pt = self._point(p["symbol"])
            if not pt:
                continue
            buy = p["side"] == "buy"
            gain = ((p["price_current"] - p["price_open"]) if buy else (p["price_open"] - p["price_current"])) / pt
            if s["be_after_points"] and gain >= s["be_after_points"]:
                be = p["price_open"] + (s["be_offset_points"] * pt if buy else -s["be_offset_points"] * pt)
                if not p["sl"] or (buy and p["sl"] < be - pt / 2) or (not buy and p["sl"] > be + pt / 2):
                    self.ui.on_log(f"#{t} {p['symbol']}: +{gain:.0f} punti, sposto SL a pareggio", "ok")
                    for x in await self.modify([t], sl=round(be, 8)):
                        self.ui.on_log(f"  → {x}", "trade")
                    continue
            if s["trailing_points"] and gain > s["trailing_points"]:
                step = max(1, s["trailing_step_points"]) * pt
                new = p["price_current"] - s["trailing_points"] * pt if buy else p["price_current"] + s["trailing_points"] * pt
                better = (not p["sl"]) or (buy and new > p["sl"] + step) or (not buy and new < p["sl"] - step)
                if better:
                    self.ui.on_log(f"#{t} {p['symbol']}: trailing stop → SL {new:.5g}", "ok")
                    for x in await self.modify([t], sl=round(new, 8)):
                        self.ui.on_log(f"  → {x}", "trade")

    # ================================================================ pulsanti
    async def button(self, action: str, tickets: list[int] | None = None, percent: float = 100) -> list[str]:
        allp = [p["ticket"] for p in self.my_positions()]
        sel = tickets if tickets else allp
        if action == "close":
            out = await self.close(sel, percent)
            if not tickets:
                out += await self.cancel([o["ticket"] for o in self.my_orders()]) if self.my_orders() else []
            return out
        if action == "breakeven":
            return await self.breakeven(sel)
        return []

    def test_message(self, text: str, settings: dict) -> str:
        r = parse_message(text, settings, self.broker_symbols())
        lines = [describe(r)]
        if r["kind"] == "signal" and r["symbol"]:
            orders, why = self.plan_signal(r, dict(settings, dry_run=True, paused=False))
            if why:
                lines.append(f"Non verrebbe eseguito: {why}")
            for o in orders:
                lines.append("Verrebbe inviato: " + _desc(o))
        return "\n".join(lines)


def _base(sym: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (sym or "").upper().split(".")[0])


def _desc(o: dict) -> str:
    vol = f"lotti {o['volume']:g}" if o["volume"] else f"rischio {o['risk_money']:g}"
    entry = "a mercato" if o["otype"] == "MARKET" else f"a {o['price']:g}"
    sl = f"{o['sl']:g}" if o["sl"] else (f"{o['sl_points']} punti" if o["sl_points"] else "nessuno")
    tp = f"{o['tp']:g}" if o["tp"] else (f"{o['tp_points']} punti" if o["tp_points"] else "nessuno")
    return f"{o['side']} {o['otype']} {o['symbol']} {vol} {entry}, SL {sl}, TP {tp}"
