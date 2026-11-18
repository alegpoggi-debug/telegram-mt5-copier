"""Comunicazione con l'Expert Advisor TelegramBridge dentro MetaTrader 5.

Funziona tramite file nella cartella "Common/Files/TGBridge" di MT5:
  - l'app scrive cmd_<id>.txt, l'EA lo esegue e risponde con res_<id>.txt
  - l'EA scrive ogni secondo state.txt (conto, posizioni, ordini) e ogni minuto symbols.txt
"""
from __future__ import annotations

import asyncio
import glob
import os
import time
import uuid
from pathlib import Path

SUBDIR = "TGBridge"


def find_common_files_dir() -> Path | None:
    """Cerca la cartella Common/Files di MetaTrader 5 sul Mac (app ufficiale o Wine/CrossOver)."""
    home = Path.home()
    patterns = [
        str(home / "Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common/Files"),
        str(home / "Library/Application Support/*/drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common/Files"),
        str(home / "Library/Application Support/CrossOver/Bottles/*/drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common/Files"),
        str(home / ".wine/drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/Common/Files"),
        str(home / "AppData/Roaming/MetaQuotes/Terminal/Common/Files"),  # Windows
    ]
    for p in patterns:
        hits = sorted(glob.glob(p))
        if hits:
            return Path(hits[0])
    return None


class BridgeError(Exception):
    pass


class Bridge:
    def __init__(self, common_files_dir: Path):
        self.dir = Path(common_files_dir) / SUBDIR
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---------- stato ----------
    def state_age(self) -> float | None:
        p = self.dir / "state.txt"
        if not p.exists():
            return None
        return time.time() - p.stat().st_mtime

    def is_connected(self) -> bool:
        age = self.state_age()
        return age is not None and age < 5

    def read_state(self) -> dict | None:
        p = self.dir / "state.txt"
        if not p.exists():
            return None
        try:
            text = p.read_text(encoding="latin-1")
        except OSError:
            return None
        return parse_state(text)

    def read_symbols(self) -> list[str]:
        p = self.dir / "symbols.txt"
        if not p.exists():
            return []
        try:
            return [s.strip() for s in p.read_text(encoding="latin-1").splitlines() if s.strip()]
        except OSError:
            return []

    # ---------- comandi ----------
    async def send(self, cmd: dict, timeout: float = 15.0) -> dict:
        cid = uuid.uuid4().hex[:12]
        lines = [f"{k}={_clean(v)}" for k, v in cmd.items()]
        tmp = self.dir / f"tmp_{cid}"
        final = self.dir / f"cmd_{cid}.txt"
        tmp.write_text("\n".join(lines) + "\n", encoding="ascii", errors="replace")
        os.replace(tmp, final)
        res = self.dir / f"res_{cid}.txt"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if res.exists():
                await asyncio.sleep(0.05)
                try:
                    data = _parse_kv(res.read_text(encoding="latin-1"))
                    res.unlink(missing_ok=True)
                    return data
                except OSError:
                    pass
            await asyncio.sleep(0.1)
        # l'EA non ha risposto: ritiro il comando se non ancora preso in carico
        try:
            final.unlink()
            raise BridgeError("MetaTrader non risponde (EA TelegramBridge attivo? AutoTrading acceso?)")
        except FileNotFoundError:
            raise BridgeError("Comando preso in carico da MetaTrader ma nessuna risposta entro il tempo limite: controlla il terminale")


def _clean(v) -> str:
    s = "" if v is None else str(v)
    return s.replace("\n", " ").replace("\r", " ").replace("=", "-")


def _parse_kv(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_state(text: str) -> dict:
    st = {"account": None, "positions": [], "orders": [], "time": None, "symbols": {}}
    for line in text.splitlines():
        parts = line.strip().split("|")
        if not parts or not parts[0]:
            continue
        kind = parts[0]
        try:
            if kind == "account" and len(parts) >= 8:
                st["account"] = {
                    "login": parts[1],
                    "trade_mode": int(parts[2]),  # 0 demo, 1 contest, 2 reale
                    "balance": float(parts[3]),
                    "equity": float(parts[4]),
                    "currency": parts[5],
                    "free_margin": float(parts[6]),
                    "server": parts[7],
                }
            elif kind == "pos" and len(parts) >= 12:
                st["positions"].append({
                    "ticket": int(parts[1]),
                    "symbol": parts[2],
                    "side": "buy" if parts[3] == "0" else "sell",
                    "volume": float(parts[4]),
                    "price_open": float(parts[5]),
                    "sl": float(parts[6]),
                    "tp": float(parts[7]),
                    "profit": float(parts[8]),
                    "magic": int(parts[9]),
                    "comment": parts[10],
                    "price_current": float(parts[11]),
                })
            elif kind == "ord" and len(parts) >= 10:
                st["orders"].append({
                    "ticket": int(parts[1]),
                    "symbol": parts[2],
                    "type": parts[3],
                    "volume": float(parts[4]),
                    "price": float(parts[5]),
                    "sl": float(parts[6]),
                    "tp": float(parts[7]),
                    "magic": int(parts[8]),
                    "comment": parts[9],
                })
            elif kind == "sym" and len(parts) >= 6:
                st["symbols"][parts[1]] = {"point": float(parts[2]), "digits": int(parts[3]),
                                           "bid": float(parts[4]), "ask": float(parts[5])}
            elif kind == "time" and len(parts) >= 2:
                st["time"] = parts[1]
        except (ValueError, IndexError):
            continue
    return st
