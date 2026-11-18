"""Avvio del programma:  python main.py  (oppure apri l'app "Telegram MT5 Copier")"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.config import DATA_DIR, ROOT


def keep_mac_awake():
    """Impedisce al Mac di andare in stop finché il programma è aperto."""
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
        except OSError:
            pass


def main():
    logging.basicConfig(filename=DATA_DIR / "app.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("Telegram MT5 Copier")
    icon = ROOT / "app" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    keep_mac_awake()

    # recupero (una volta sola) dei dati della versione in cartella, prima di caricare le impostazioni
    try:
        from app.migrate import import_old_data
        import_msgs = import_old_data(DATA_DIR)
    except Exception as e:  # il recupero non deve mai impedire l'avvio
        logging.exception("recupero dati")
        import_msgs = [f"Recupero dei dati precedenti non riuscito: {e}"]

    from app.engine import Engine
    from app.gui import MainWindow, UiBridge

    ui = UiBridge()
    engine = Engine(ui)
    win = MainWindow(ui, engine)
    win.show()
    for m in import_msgs:
        ui.on_log(m, "ok")
    engine.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
