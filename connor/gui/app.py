"""Starts the Qt application: logging, theme, single instance and the main window."""
from __future__ import annotations

import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME, __version__, build_id, system, websearch
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


def instance_action(message: str, build: str) -> str:
    """What a running Connor does when it gets a message from a Connor that is just starting:
    "show" (same code), "replace" (newer/other code – make room), "quit" (update) or "ignore"."""
    message = message.strip()
    if message == "quit":
        return "quit"
    if message.startswith("show"):
        other = message[4:].strip()
        return "replace" if other and other != build else "show"
    return "ignore"


def _contact_running_instance(name: str, build: str) -> str:
    """"" = nobody running, "same" = it shows its window, "replaced" = it quits, "old" = an old Connor
    that does not understand the question."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(400):
        return ""
    socket.write(f"show {build}\n".encode())
    socket.flush()
    socket.waitForBytesWritten(400)
    reply = bytes(socket.readAll()) if socket.waitForReadyRead(2500) else b""
    socket.disconnectFromServer()
    answer = reply.decode("utf-8", "ignore").strip()
    if answer == "ok":
        return "same"
    return "replaced" if answer == "quit" else "old"


def _wait_until_gone(timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not system.instance_running():
            return True
        time.sleep(0.2)
    return not system.instance_running()


def _take_over(name: str, build: str) -> bool:
    """Returns False if the same Connor is already running (then it just shows its window)."""
    state = _contact_running_instance(name, build)
    if state == "same":
        return False
    if state:
        log.info("Eine andere Connor-Version läuft noch (%s) – ich ersetze sie.", state)
        if state == "old" or not _wait_until_gone(6):
            system.stop_processes(system.connor_processes())
            _wait_until_gone(4)
    return True


def run(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    setup_logging()
    _set_windows_app_id()
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)

    name = system.server_name()
    build = build_id()
    if not _take_over(name, build):
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

    def handle(connection) -> None:
        action = instance_action(bytes(connection.readAll()).decode("utf-8", "ignore"), build)
        if action == "show":
            connection.write(b"ok\n")
            connection.flush()
            window.show_normal()
        elif action == "replace":
            log.info("Eine neuere Version von Connor startet – ich mache Platz.")
            connection.write(b"quit\n")
            connection.flush()
            QTimer.singleShot(300, window.quit)
        elif action == "quit":
            log.info("Connor wird für ein Update beendet.")
            QTimer.singleShot(0, window.quit)

    def on_connection() -> None:
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connection.disconnected.connect(connection.deleteLater)
            connection.readyRead.connect(lambda c=connection: handle(c))

    server.newConnection.connect(on_connection)

    if "--minimized" in argv and window.tray is not None:
        log.info("Gestartet im Hintergrund (Tray)")
    else:
        window.show()
    code = app.exec()
    server.close()
    return code
