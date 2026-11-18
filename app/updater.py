"""Aggiornamenti automatici da GitHub.

Il codice aggiornato viene messo in ~/Library/Application Support/Telegram MT5 Copier/code
(fuori dall'app), quindi l'app non va mai sostituita e i dati non vengono mai toccati.
Il repository da cui aggiornarsi è scritto in app/update_source.txt (es. "nomeutente/telegram-mt5-copier").
"""
from __future__ import annotations

import io
import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .config import DATA_DIR, ROOT

BRANCH = "main"
TIMEOUT = 20


def local_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return "0"


def source() -> str | None:
    try:
        s = (ROOT / "app" / "update_source.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return s if s.count("/") == 1 and " " not in s else None


def _ver_tuple(v: str):
    out = []
    for part in v.strip().split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "telegram-mt5-copier-updater"})
    ctx = ssl.create_default_context()
    try:
        import certifi  # se disponibile, usa i certificati aggiornati
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return r.read()
    except (ssl.SSLError, urllib.error.URLError) as e:
        # alcuni Python su Mac non hanno i certificati: uso curl di sistema
        if sys.platform == "darwin" and os.path.exists("/usr/bin/curl"):
            res = subprocess.run(["/usr/bin/curl", "-fsSL", "--max-time", str(TIMEOUT * 3), url],
                                 capture_output=True, check=False)
            if res.returncode == 0:
                return res.stdout
        raise e


def check() -> dict | None:
    """Ritorna {"version": "2.2", "notes": "..."} se c'è una versione più nuova, altrimenti None."""
    src = source()
    if not src:
        return None
    raw = _get(f"https://raw.githubusercontent.com/{src}/{BRANCH}/VERSION").decode("utf-8", "replace")
    lines = raw.strip().splitlines()
    if not lines:
        return None
    remote = lines[0].strip()
    if _ver_tuple(remote) <= _ver_tuple(local_version()):
        return None
    return {"version": remote, "notes": "\n".join(lines[1:]).strip()}


def support_dir() -> Path:
    # DATA_DIR = .../Application Support/Telegram MT5 Copier/data  -> il codice va accanto
    return DATA_DIR.parent if os.environ.get("TGMT5_DATA") else ROOT.parent / "updates"


def apply() -> str:
    """Scarica e installa l'aggiornamento. Ritorna la nuova versione."""
    src = source()
    if not src:
        raise RuntimeError("aggiornamenti non configurati")
    data = _get(f"https://codeload.github.com/{src}/zip/refs/heads/{BRANCH}")
    base = support_dir()
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / "code-download"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    roots = [p for p in tmp.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("pacchetto di aggiornamento non valido")
    new = roots[0]
    for required in ("main.py", "VERSION", "requirements.txt", "app/engine.py", "app/gui.py", "mt5/TelegramBridge.mq5"):
        if not (new / required).exists():
            raise RuntimeError(f"pacchetto di aggiornamento incompleto (manca {required})")
    version = (new / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
    code = base / "code"
    old = base / "code-old"
    shutil.rmtree(old, ignore_errors=True)
    if code.exists():
        code.rename(old)
    new.rename(code)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    (base / "update.json").write_text(json.dumps({"installed": version}), encoding="utf-8")
    return version


def restart():
    """Riapre l'app dopo qualche secondo (il chiamante deve poi chiudere la finestra)."""
    bundle = os.environ.get("TGMT5_APP")
    if sys.platform == "darwin" and bundle:
        subprocess.Popen(["/bin/sh", "-c", f'sleep 2; /usr/bin/open "{bundle}"'], start_new_session=True)
    else:
        subprocess.Popen([sys.executable, str(support_dir() / "code" / "main.py")], start_new_session=True)
