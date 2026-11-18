"""Percorsi del programma."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Nell'app per Mac i dati stanno in ~/Library/Application Support (l'app resta intatta negli aggiornamenti)
DATA_DIR = Path(os.environ["TGMT5_DATA"]) if os.environ.get("TGMT5_DATA") else ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
