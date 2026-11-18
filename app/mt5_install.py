"""Trova MetaTrader 5 sul Mac e installa l'Expert Advisor TelegramBridge."""
from __future__ import annotations

import glob
import shutil
from pathlib import Path

from .config import ROOT

EA_SOURCE = ROOT / "mt5" / "TelegramBridge.mq5"


def _wine_roots() -> list[Path]:
    home = Path.home()
    pats = [
        home / "Library/Application Support/net.metaquotes.wine.metatrader5",
        home / "Library/Application Support/net.metaquotes.wine.*",
        home / "Library/Application Support/MetaTrader 5*",
        home / "Library/Application Support/CrossOver/Bottles/*",
        home / ".wine",
        home / ".mt5",
    ]
    out = []
    for p in pats:
        out += [Path(x) for x in glob.glob(str(p))]
    return out


def find_experts_dirs() -> list[Path]:
    """Tutte le cartelle MQL5/Experts dei terminali MT5 trovati."""
    found = []
    for root in _wine_roots():
        for pat in ("drive_c/Program Files/*/MQL5/Experts",
                    "drive_c/Program Files (x86)/*/MQL5/Experts",
                    "drive_c/users/*/AppData/Roaming/MetaQuotes/Terminal/*/MQL5/Experts"):
            found += [Path(x) for x in glob.glob(str(root / pat))]
    win = Path.home() / "AppData/Roaming/MetaQuotes/Terminal"
    found += [Path(x) for x in glob.glob(str(win / "*/MQL5/Experts"))]
    return sorted(set(found))


def install_ea() -> list[Path]:
    """Copia TelegramBridge.mq5 in ogni cartella Experts trovata. Ritorna i percorsi installati."""
    done = []
    for d in find_experts_dirs():
        try:
            shutil.copy2(EA_SOURCE, d / EA_SOURCE.name)
            done.append(d / EA_SOURCE.name)
        except OSError:
            continue
    return done
