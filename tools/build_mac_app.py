"""Costruisce "Telegram MT5 Copier.app" (app per Mac) nella cartella indicata.

    python3 tools/build_mac_app.py dist/

L'app contiene il programma; alla prima apertura installa da sola i componenti Python
in ~/Library/Application Support/Telegram MT5 Copier/ e lì salva anche i dati.
I dati di una versione precedente (cartella telegram-mt5-agent/data) vengono COPIATI, mai cancellati.
"""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Telegram MT5 Copier"
VERSION = (ROOT / "VERSION").read_text().splitlines()[0].strip()

INFO_PLIST = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>{APP_NAME}</string>
  <key>CFBundleDisplayName</key><string>{APP_NAME}</string>
  <key>CFBundleIdentifier</key><string>local.telegram-mt5-copier</string>
  <key>CFBundleVersion</key><string>{VERSION}</string>
  <key>CFBundleShortVersionString</key><string>{VERSION}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>10.15</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.finance</string>
  <key>NSDesktopFolderUsageDescription</key><string>Per recuperare le impostazioni della versione precedente.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>Per recuperare le impostazioni della versione precedente.</string>
  <key>NSDownloadsFolderUsageDescription</key><string>Per recuperare le impostazioni della versione precedente.</string>
</dict>
</plist>
"""

LAUNCHER = r'''#!/bin/bash
# Avvio dell'app "Telegram MT5 Copier"
APP_NAME="Telegram MT5 Copier"
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
export TGMT5_APP="$(cd "$CONTENTS/.." && pwd)"
BUNDLED="$CONTENTS/Resources/app"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
VENV="$SUPPORT/venv"
export TGMT5_DATA="$SUPPORT/data"
LOG="$SUPPORT/avvio.log"
mkdir -p "$TGMT5_DATA"
echo "=== avvio $(date) ===" >> "$LOG"

avviso() { /usr/bin/osascript -e "display notification \"$1\" with title \"$APP_NAME\"" >/dev/null 2>&1; }
errore() { /usr/bin/osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with icon caution with title \"$APP_NAME\"" >/dev/null 2>&1; }

# ---- 1) recupero dei dati della versione precedente (solo COPIA, niente viene cancellato)
if [ ! -f "$TGMT5_DATA/settings.json" ]; then
  OLD=""
  for base in "$HOME/Desktop" "$HOME/Documents" "$HOME/Downloads" "$HOME"; do
    f=$(/usr/bin/find "$base" -maxdepth 4 -path "*telegram-mt5-agent*/data/settings.json" 2>/dev/null | head -1)
    if [ -n "$f" ]; then OLD="$(dirname "$f")"; break; fi
  done
  if [ -n "$OLD" ]; then
    cp -Rn "$OLD"/. "$TGMT5_DATA"/ >> "$LOG" 2>&1 && echo "dati copiati da $OLD" >> "$LOG"
    avviso "Impostazioni e accesso a Telegram recuperati dalla versione precedente."
  fi
fi

# ---- 2) Python 3
PY=""
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/bin/python3; do
  if [ -x "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$p"; break
  fi
done
if [ -z "$PY" ]; then
  errore "Per funzionare serve Python 3 di Apple (gratis). Ora si apre l'installazione: premi Installa e, quando ha finito, riapri l'app."
  /usr/bin/xcode-select --install >/dev/null 2>&1
  exit 1
fi

# ---- 3) quale versione del programma usare: quella aggiornata (se più nuova) o quella dentro l'app
SRC="$BUNDLED"
UPD="$SUPPORT/code"
if [ -f "$UPD/main.py" ] && [ -f "$UPD/VERSION" ]; then
  if "$PY" -c 'import sys
def v(p):
    return tuple(int(x) if x.isdigit() else 0 for x in open(p).readline().strip().split("."))
sys.exit(0 if v(sys.argv[1]) >= v(sys.argv[2]) else 1)' "$UPD/VERSION" "$BUNDLED/VERSION" >/dev/null 2>&1; then
    SRC="$UPD"
  fi
fi
echo "programma: $SRC" >> "$LOG"

# ---- 4) componenti (solo la prima volta o dopo un aggiornamento)
STAMP="$VENV/.ok-$(/usr/bin/cksum < "$SRC/requirements.txt" | cut -d' ' -f1)"
if [ ! -f "$STAMP" ]; then
  avviso "Prima apertura: installo i componenti (1-3 minuti). La finestra si apre da sola al termine."
  rm -rf "$VENV"
  if ! { "$PY" -m venv "$VENV" && "$VENV/bin/python" -m pip install -q --upgrade pip && \
         "$VENV/bin/python" -m pip install -q -r "$SRC/requirements.txt"; } >> "$LOG" 2>&1; then
    errore "Installazione dei componenti non riuscita. Controlla la connessione a internet e riapri l'app."
    exit 1
  fi
  touch "$STAMP"
fi

# ---- 5) avvio
cd "$SRC"
exec "$VENV/bin/python" "$SRC/main.py" >> "$LOG" 2>&1
'''

INCLUDE = ["main.py", "requirements.txt", "VERSION", "app", "mt5", "GUIDA.md"]


def build(dist: Path):
    app = dist / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    res = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    (res / "app").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text(INFO_PLIST, encoding="utf-8")
    (app / "Contents" / "PkgInfo").write_text("APPL????")
    launcher = macos / "launcher"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    for name in INCLUDE:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, res / "app" / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, res / "app" / name)
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py"), str(res)], check=True,
                   stdout=subprocess.DEVNULL)
    shutil.copy2(res / "icon.png", res / "app" / "app" / "icon.png")
    (res / "icon.png").unlink()
    return app


if __name__ == "__main__":
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist")
    dist.mkdir(parents=True, exist_ok=True)
    print(build(dist))
