"""Starts the Qt application: logging, theme, single instance and the main window."""
from __future__ import annotations

import getpass
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME, __version__, websearch
from ..database import Database
from ..paths import db_path, log_path
from ..settings import Settings
from .main_window import MainWindow
from .theme import apply_theme, make_icon

log = logging.getLogger("connor")


def setup_logging() -> None:
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    if sys.stderr is not None:
        root.addHandler(logging.StreamHandler())


def _install_error_hooks() -> None:
    def excepthook(exc_type, exc, tb):
        log.error("Unerwarteter Fehler", exc_info=(exc_type, exc, tb))
        try:
            QMessageBox.critical(None, f"{APP_NAME} – Fehler",
                                 f"Da ist etwas schiefgelaufen:\n\n{exc}\n\nDetails stehen in:\n{log_path()}")
        except Exception:  # noqa: BLE001
            pass

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        log.error("Fehler im Hintergrund", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Connor.LocalAssistant")
    except Exception:  # noqa: BLE001 - only cosmetic (taskbar icon)
        pass


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"connor-assistant-{user}"


def _activate_running_instance(name: str) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(400):
        return False
    socket.write(b"show")
    socket.flush()
    socket.waitForBytesWritten(400)
    socket.disconnectFromServer()
    return True


def run(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    setup_logging()
    _set_windows_app_id()
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)

    name = _server_name()
    if _activate_running_instance(name):
        log.info("Connor läuft schon – bringe das Fenster nach vorne.")
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)

    apply_theme(app)
    icon = make_icon()
    app.setWindowIcon(icon)
    _install_error_hooks()
    log.info("Connor %s startet", __version__)
    if websearch.use_system_certificates():
        log.info("Nutze die Zertifikate von Windows für Internetverbindungen")

    try:
        db = Database(db_path())
    except Exception as exc:  # noqa: BLE001
        log.exception("Datenbank konnte nicht geöffnet werden")
        QMessageBox.critical(None, APP_NAME, f"Ich kann meine Datenbank nicht öffnen:\n{db_path()}\n\n{exc}")
        return 1
    settings = Settings.load()
    window = MainWindow(db, settings, icon)

    def on_connection() -> None:
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.disconnected.connect(connection.deleteLater)
        window.show_normal()

    server.newConnection.connect(on_connection)

    if "--minimized" in argv and window.tray is not None:
        log.info("Gestartet im Hintergrund (Tray)")
    else:
        window.show()
    code = app.exec()
    server.close()
    return code
