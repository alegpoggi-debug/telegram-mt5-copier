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


_LOCK = None


def single_instance() -> bool:
    """True se questa è l'unica copia aperta (aspetta qualche secondo: utile dopo un aggiornamento)."""
    global _LOCK
    try:
        import fcntl
    except ImportError:
        return True
    import time
    _LOCK = open(DATA_DIR / "app.lock", "w")
    for _ in range(20):
        try:
            fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            time.sleep(0.5)
    return False


def main():
    logging.basicConfig(filename=DATA_DIR / "app.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("Telegram MT5 Copier")
    icon = ROOT / "app" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    if not single_instance():
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(None, "Telegram MT5 Copier",
                            "L'app è già aperta.\n\nSe non la vedi, c'è un'altra copia dell'app aperta "
                            "(per esempio quella nella cartella Download o la vecchia versione Avvia.command).\n"
                            "Chiudi le altre copie e usa solo quella in Applicazioni: due copie insieme "
                            "fanno scollegare Telegram.")
        return 0
    keep_mac_awake()

    from app.engine import Engine
    from app.gui import MainWindow, UiBridge

    ui = UiBridge()
    engine = Engine(ui)
    win = MainWindow(ui, engine)
    win.show()
    engine.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
