"""Recupero dei dati dalle versioni precedenti (cartella "telegram-mt5-agent" scaricata come zip).

Cerca la cartella vecchia in Scrivania, Documenti, Download e nella cartella Home, poi COPIA
impostazioni, canali e accesso a Telegram nei dati dell'app. Prima fa sempre una copia di
sicurezza dei dati attuali. Non cancella e non modifica mai la cartella vecchia.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from pathlib import Path

MARKER = "import_vecchi_dati.json"
SKIP_DIRS = {"Library", ".Trash", "Applications", "node_modules", ".venv", "venv", "Pictures", "Music", "Movies"}


def _candidates(home: Path, max_depth: int = 4) -> list[Path]:
    """Cartelle 'data' dentro cartelle 'telegram-mt5-agent*' (versioni precedenti)."""
    found = []
    roots = [home / "Desktop", home / "Documents", home / "Downloads", home]
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for cur, dirs, _files in os.walk(root):
            cur_p = Path(cur)
            depth = len(cur_p.parts) - base_depth
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
            if depth >= max_depth:
                dirs[:] = []
            if cur_p.name.startswith("telegram-mt5-agent") and (cur_p / "data").is_dir():
                d = (cur_p / "data").resolve()
                if d not in seen:
                    seen.add(d)
                    found.append(d)
    return found


def _score(d: Path) -> tuple:
    has_session = (d / "telegram.session").exists() or (d / "tg_session.session").exists()
    api = False
    try:
        api = bool(json.loads((d / "settings.json").read_text(encoding="utf-8")).get("tg_api_id"))
    except (OSError, ValueError):
        pass
    if not api and (d.parent / "config.json").exists():
        try:
            api = bool(json.loads((d.parent / "config.json").read_text(encoding="utf-8"))["telegram"].get("api_id"))
        except (OSError, ValueError, KeyError):
            pass
    newest = max((f.stat().st_mtime for f in d.iterdir() if f.is_file()), default=0)
    return (has_session, api, newest)


def _from_first_version(folder: Path) -> dict:
    """Converte config.json / rules.json della primissima versione nelle impostazioni attuali."""
    out = {}
    try:
        cfg = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    tg, mt5, safety = cfg.get("telegram", {}), cfg.get("mt5", {}), cfg.get("safety", {})
    if tg.get("api_id"):
        out["tg_api_id"] = str(tg["api_id"])
        out["tg_api_hash"] = tg.get("api_hash", "")
    chans = [c for c in tg.get("channels", []) if isinstance(c, str) and c.strip() and "Nome esatto" not in c]
    if chans:
        out["tg_channels"] = [{"id": None, "name": c.strip()} for c in chans]
    if mt5.get("bridge_dir"):
        out["mt5_dir"] = mt5["bridge_dir"]
    if mt5.get("magic"):
        out["magic"] = int(mt5["magic"])
    for a, b in (("dry_run", "dry_run"), ("allow_real_account", "allow_real"),
                 ("max_lot_per_trade", "max_lot_per_trade"), ("max_total_open_lots", "max_total_lots")):
        if a in safety:
            out[b] = safety[a]
    try:
        rules = json.loads((folder / "data" / "rules.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rules = {}
    for k in ("paused", "lot_mode", "fixed_lot", "risk_percent", "max_open_trades"):
        if k in rules:
            out[k] = rules[k]
    if rules.get("daily_loss_limit"):
        out["daily_loss_limit"] = rules["daily_loss_limit"]
    if rules.get("allowed_symbols"):
        out["allowed_symbols"] = ", ".join(rules["allowed_symbols"])
    if rules.get("blocked_symbols"):
        out["blocked_symbols"] = ", ".join(rules["blocked_symbols"])
    if rules.get("multi_tp_mode"):
        out["multi_tp_mode"] = {"first_tp": "first", "last_tp": "last"}.get(rules["multi_tp_mode"], "split")
    return out


def _resolve_old_folder(path: Path) -> Path | None:
    """Accetta la cartella telegram-mt5-agent oppure la sua sottocartella data."""
    path = Path(path)
    if (path / "data").is_dir():
        return path / "data"
    if path.name == "data" and path.is_dir():
        return path
    if (path / "config.json").exists():
        (path / "data").mkdir(exist_ok=True)
        return path / "data"
    return None


def import_from(old_data: Path, data_dir: Path, overwrite_login: bool = True) -> list[str]:
    """Copia impostazioni, accesso a Telegram e storico da old_data a data_dir (con copia di sicurezza)."""
    msgs = []
    data_dir = Path(data_dir)
    # 1) copia di sicurezza dei dati attuali
    if data_dir.exists() and any(data_dir.iterdir()):
        backup = data_dir.parent / f"backup-dati-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copytree(data_dir, backup)
        msgs.append(f"Copia di sicurezza dei dati attuali: {backup}")
    data_dir.mkdir(parents=True, exist_ok=True)

    # 2) impostazioni: quelle vecchie hanno la precedenza
    current = {}
    try:
        current = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    old = {}
    try:
        old = json.loads((old_data / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    merged = dict(current)
    merged.update(_from_first_version(old_data.parent))
    merged.update(old)
    (data_dir / "settings.json").write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    msgs.append(f"Impostazioni recuperate da {old_data.parent} ({len(merged.get('tg_channels') or [])} canali, "
                f"api_id Telegram {'presente' if merged.get('tg_api_id') else 'assente'}).")

    # 3) accesso a Telegram
    session = old_data / "telegram.session"
    if not session.exists() and (old_data / "tg_session.session").exists():
        session = old_data / "tg_session.session"
    if session.exists():
        if overwrite_login or not (data_dir / "telegram.session").exists():
            shutil.copy2(session, data_dir / "telegram.session")
            msgs.append("Accesso a Telegram recuperato.")
        else:
            msgs.append("Accesso a Telegram attuale mantenuto.")
    else:
        msgs.append("Nella cartella precedente non c'è un accesso a Telegram salvato.")

    # 4) storico dei segnali (serve a gestire le operazioni già aperte)
    if (old_data / "signals.json").exists():
        shutil.copy2(old_data / "signals.json", data_dir / "signals.json")

    (data_dir / MARKER).write_text(json.dumps({"da": str(old_data), "quando": dt.datetime.now().isoformat(timespec="seconds")},
                                              ensure_ascii=False), encoding="utf-8")
    return msgs


def import_old_data(data_dir: Path, home: Path | None = None, force: bool = False) -> list[str]:
    """Recupero automatico all'avvio (una volta sola). Ritorna i messaggi da mostrare nell'attività."""
    home = home or Path.home()
    data_dir = Path(data_dir)
    if data_dir.resolve().parent.name.startswith("telegram-mt5-agent"):
        return []  # stiamo girando dalla cartella vecchia stessa: niente da recuperare
    marker = data_dir / MARKER
    if marker.exists() and not force:
        return []
    # se l'app è già configurata (codici + accesso salvato) non tocco niente
    try:
        cur = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cur = {}
    if cur.get("tg_api_id") and (data_dir / "telegram.session").exists() and not force:
        marker.write_text(json.dumps({"saltato": "app già configurata"}), encoding="utf-8")
        return []
    try:
        cands = [d for d in _candidates(home) if d != data_dir.resolve()]
    except PermissionError:
        return ["Non ho il permesso di leggere Documenti/Scrivania/Download: chiudi e riapri l'app e premi «Consenti»."]
    if not cands:
        return ["Recupero automatico: nessuna cartella «telegram-mt5-agent» trovata in Scrivania, Documenti o Download. "
                "Puoi indicarla tu: Impostazioni → Telegram → «Recupera dati da una versione precedente»."]
    best = max(cands, key=_score)
    if _score(best)[:2] == (False, False):
        return [f"Trovata la cartella precedente {best.parent}, ma non contiene impostazioni da recuperare."]
    return import_from(best, data_dir, overwrite_login=False)
