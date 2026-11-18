"""Test del recupero dati dalle versioni precedenti.  python -m tests.test_migrate"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.migrate import MARKER, import_old_data  # noqa: E402


def sha(d):
    return {f.name: hashlib.sha1(f.read_bytes()).hexdigest() for f in sorted(Path(d).rglob("*")) if f.is_file()}


def main():
    # --- A) versione in cartella (gratuita): settings.json + telegram.session in Download
    home = Path(tempfile.mkdtemp())
    old = home / "Downloads" / "telegram-mt5-agent" / "data"
    old.mkdir(parents=True)
    (old / "settings.json").write_text(json.dumps({"tg_api_id": "111", "tg_api_hash": "h" * 32,
                                                   "tg_channels": [{"id": -100123, "name": "VIP"}],
                                                   "fixed_lot": 0.07, "mt5_dir": "/vecchio/percorso"}))
    (old / "telegram.session").write_bytes(b"SESSIONE")
    (old / "signals.json").write_text('{"next_key": 9, "signals": {}}')
    before = sha(old.parent)
    app_data = home / "Library/Application Support/Telegram MT5 Copier/data"
    app_data.mkdir(parents=True)
    (app_data / "settings.json").write_text(json.dumps({"tg_api_id": "", "fixed_lot": 0.01, "dry_run": True}))
    msgs = import_old_data(app_data, home=home)
    s = json.loads((app_data / "settings.json").read_text())
    assert s["tg_api_id"] == "111" and s["fixed_lot"] == 0.07 and s["tg_channels"][0]["id"] == -100123, s
    assert (app_data / "telegram.session").read_bytes() == b"SESSIONE"
    assert (app_data / MARKER).exists()
    backups = list(app_data.parent.glob("backup-dati-*"))
    assert len(backups) == 1 and json.loads((backups[0] / "settings.json").read_text())["fixed_lot"] == 0.01
    assert sha(old.parent) == before, "la cartella vecchia è stata modificata!"
    assert import_old_data(app_data, home=home) == []  # una volta sola
    print("A ok:", *msgs, sep="\n  ")

    # --- B) primissima versione: config.json + rules.json + tg_session.session
    home = Path(tempfile.mkdtemp())
    f = home / "Documents" / "lavoro" / "telegram-mt5-agent"
    (f / "data").mkdir(parents=True)
    (f / "config.json").write_text(json.dumps({"telegram": {"api_id": 222, "api_hash": "x" * 32, "channels": ["Gold VIP"]},
                                               "safety": {"dry_run": True, "max_lot_per_trade": 0.2}}))
    (f / "data" / "rules.json").write_text(json.dumps({"fixed_lot": 0.03, "multi_tp_mode": "first_tp",
                                                      "blocked_symbols": ["XAUUSD"]}))
    (f / "data" / "tg_session.session").write_bytes(b"S2")
    app_data = home / "AS" / "data"
    app_data.mkdir(parents=True)
    msgs = import_old_data(app_data, home=home)
    s = json.loads((app_data / "settings.json").read_text())
    assert s["tg_api_id"] == "222" and s["tg_channels"] == [{"id": None, "name": "Gold VIP"}], s
    assert s["fixed_lot"] == 0.03 and s["multi_tp_mode"] == "first" and s["blocked_symbols"] == "XAUUSD", s
    assert s["max_lot_per_trade"] == 0.2 and (app_data / "telegram.session").read_bytes() == b"S2"
    print("B ok:", *msgs, sep="\n  ")

    # --- C) nessuna cartella vecchia: messaggio, nessun marker (riprova al prossimo avvio)
    home = Path(tempfile.mkdtemp())
    app_data = home / "AS" / "data"
    app_data.mkdir(parents=True)
    msgs = import_old_data(app_data, home=home)
    assert not (app_data / MARKER).exists() and "Nessuna cartella" in msgs[0]
    print("C ok:", msgs[0][:60])
    print("TEST RECUPERO DATI SUPERATI")


if __name__ == "__main__":
    main()
