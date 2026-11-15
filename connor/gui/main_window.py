"""Connor's main window: tabs, model picker, tray icon, file drops and the reminder clock."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QResizeEvent
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QPushButton, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, backup, system
from ..database import Database, str_to_dt
from ..engine import ConnorEngine
from ..llm import OLLAMA_DOWNLOAD, RECOMMENDED_MODEL, OllamaClient, OllamaError
from ..settings import Settings
from ..timeparse import format_dt
from ..voice import Transcriber
from . import theme
from .chat_tab import ChatTab
from .common import Worker
from .documents_tab import DocumentsTab
from .memory_tab import MemoryTab
from .reminders_tab import RemindersTab
from .settings_dialog import SettingsDialog
from .voice_drop import DropOverlay, DropWatcher
from .writing_tab import WritingTab

log = logging.getLogger(__name__)

REMINDER_INTERVAL_MS = 15_000


class ReminderPopup(QDialog):
    def __init__(self, parent: QWidget, reminder: dict, on_snooze):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("⏰ Connor erinnert dich")
        self.setMinimumWidth(380)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        text = QLabel(reminder["text"])
        text.setObjectName("h2")
        text.setWordWrap(True)
        layout.addWidget(text)
        due = str_to_dt(reminder["due_at"])
        late = datetime.now() - due > timedelta(minutes=5)
        info = QLabel(("Verpasst – war fällig am " if late else "Fällig: ") + format_dt(due))
        info.setObjectName("muted")
        layout.addWidget(info)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        snooze = QPushButton("In 10 Minuten nochmal")
        snooze.clicked.connect(lambda: (on_snooze(reminder["text"]), self.close()))
        buttons.addWidget(snooze)
        done = QPushButton("Erledigt ✓")
        done.setObjectName("primary")
        done.clicked.connect(self.close)
        buttons.addWidget(done)
        layout.addSpacing(10)
        layout.addLayout(buttons)


class MainWindow(QMainWindow):
    def __init__(self, db: Database, settings: Settings, icon: QIcon):
        super().__init__()
        self.db = db
        self.settings = settings
        self.icon = icon
        self.engine = ConnorEngine(db, settings)
        self.transcriber = Transcriber(settings.voice_model, settings.voice_language, settings.voice_device)
        self._quitting = False
        self._checking = False
        self._warming = False
        self._warmed_key: tuple | None = None
        self._workers: set[Worker] = set()

        self.setWindowTitle(f"{APP_NAME} – dein lokaler KI-Kumpel")
        self.setWindowIcon(icon)
        self.resize(1240, 820)
        self.setMinimumSize(1000, 620)

        get_transcriber = lambda: self.transcriber  # noqa: E731 - settings can replace it
        self.chat_tab = ChatTab(db, self.engine, settings, get_transcriber)
        self.writing_tab = WritingTab(db, self.engine, settings, get_transcriber)
        self.documents_tab = DocumentsTab(db, get_transcriber)
        self.memory_tab = MemoryTab(db)
        self.reminders_tab = RemindersTab(db)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.addTab(self.chat_tab, "💬  Chat")
        self.tabs.addTab(self.writing_tab, "✍️  Schreibwerkstatt")
        self.tabs.addTab(self.documents_tab, "📚  Dokumente")
        self.tabs.addTab(self.memory_tab, "🧠  Gedächtnis")
        self.tabs.addTab(self.reminders_tab, "⏰  Erinnerungen")
        self.tabs.setCornerWidget(self._build_corner(), Qt.Corner.TopRightCorner)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        self.chat_tab.data_changed.connect(self.on_data_changed)
        self.chat_tab.connection_problem.connect(lambda: self.check_ollama(try_start=False))
        self.writing_tab.data_changed.connect(self.on_data_changed)
        self.documents_tab.changed.connect(lambda: self.on_data_changed({"documents", "projects"}))
        self.memory_tab.changed.connect(lambda: self.on_data_changed({"projects"}))

        self.setAcceptDrops(True)
        self.drop_overlay = DropOverlay(self)
        # Handle the files after the drop has finished – otherwise Explorer waits for our dialogs.
        self.drop_overlay.dropped.connect(lambda paths: QTimer.singleShot(0, lambda: self.handle_dropped(paths)))
        self.drop_watcher = DropWatcher(self, self.show_drop_overlay)
        QApplication.instance().installEventFilter(self.drop_watcher)

        self.tray = self._build_tray()

        self.reminder_timer = QTimer(self)
        self.reminder_timer.setInterval(REMINDER_INTERVAL_MS)
        self.reminder_timer.timeout.connect(self.check_reminders)
        self.reminder_timer.start()
        QTimer.singleShot(1500, self.check_reminders)

        self.recheck_timer = QTimer(self)
        self.recheck_timer.setSingleShot(True)
        self.recheck_timer.timeout.connect(lambda: self.check_ollama(try_start=False))
        QTimer.singleShot(200, lambda: self.check_ollama(try_start=True))

        self._backing_up = False
        self.backup_timer = QTimer(self)
        self.backup_timer.setInterval(60 * 60 * 1000)
        self.backup_timer.timeout.connect(self.run_backup)
        self.backup_timer.start()
        QTimer.singleShot(30_000, self.run_backup)

        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(settings.window_geometry.encode("ascii")))

    # ------------------------------------------------------------- layout
    def _build_corner(self) -> QWidget:
        corner = QWidget()
        layout = QHBoxLayout(corner)
        layout.setContentsMargins(0, 4, 12, 4)
        layout.setSpacing(8)
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("statusDot")
        self.status_dot.setStyleSheet(f"color: {theme.MUTED};")
        self.status_dot.setToolTip("Verbindung zu Ollama wird geprüft …")
        layout.addWidget(self.status_dot)
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(190)
        self.model_box.setToolTip("Welches lokale KI-Modell Connor benutzt")
        self.model_box.activated.connect(self._on_model_chosen)
        layout.addWidget(self.model_box)
        refresh = QPushButton("↻")
        refresh.setToolTip("Ollama und Modelle neu prüfen")
        refresh.clicked.connect(lambda: self.check_ollama(try_start=True))
        layout.addWidget(refresh)
        settings_button = QPushButton("⚙  Einstellungen")
        settings_button.clicked.connect(self.open_settings)
        layout.addWidget(settings_button)
        return corner

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self.icon, self)
        tray.setToolTip(f"{APP_NAME} – dein lokaler KI-Kumpel")
        menu = QMenu()
        open_action = QAction("Connor öffnen", menu)
        open_action.triggered.connect(self.show_normal)
        menu.addAction(open_action)
        menu.addSeparator()
        quit_action = QAction("Beenden", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        self._tray_menu = menu
        tray.activated.connect(self._on_tray_activated)
        tray.messageClicked.connect(self.show_normal)
        tray.show()
        return tray

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_normal()

    def show_normal(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is self.memory_tab:
            self.memory_tab.refresh_projects()
            self.memory_tab.refresh()
        elif widget is self.reminders_tab:
            self.reminders_tab.refresh()
        elif widget is self.writing_tab:
            self.writing_tab.refresh_projects()
        elif widget is self.chat_tab:
            self.chat_tab.refresh_projects()
        elif widget is self.documents_tab:
            self.documents_tab.refresh_projects()
            self.documents_tab.refresh()

    def on_data_changed(self, changes) -> None:
        changes = set(changes or ())
        if "memory" in changes:
            self.memory_tab.refresh_projects()
            self.memory_tab.refresh()
        if "reminder" in changes:
            self.reminders_tab.refresh()
        if "documents" in changes:
            self.documents_tab.refresh_projects()
            self.documents_tab.refresh()
            self.chat_tab.refresh_attachments()
        self.chat_tab.refresh_projects()
        self.writing_tab.refresh_projects()

    # -------------------------------------------------------------- drops
    def show_drop_overlay(self) -> None:
        widget = self.tabs.currentWidget()
        if widget is self.chat_tab:
            text = "📎\n\nLoslassen – Connor liest die Dateien\nund hängt sie an diesen Chat"
        elif widget is self.writing_tab:
            text = "📥\n\nLoslassen – als Geschichte öffnen\noder als Wissen für Connor speichern"
        else:
            text = "📚\n\nLoslassen – Connor nimmt die Dateien\nin deine Dokumente auf"
        self.drop_overlay.setText(text + "\n\nMarkdown · Text · Word · PDF · E-Book · Audio · ganze Ordner")
        self.drop_overlay.setGeometry(self.rect().adjusted(12, 12, -12, -12))
        self.drop_overlay.show()
        self.drop_overlay.raise_()

    def handle_dropped(self, paths: list[str]) -> None:
        widget = self.tabs.currentWidget()
        if widget is self.chat_tab:
            self.chat_tab.add_files(paths)
        elif widget is self.writing_tab:
            self.writing_tab.handle_drop(paths)
        else:
            self.tabs.setCurrentWidget(self.documents_tab)
            self.documents_tab.import_paths(paths)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.drop_overlay.isVisible():
            self.drop_overlay.setGeometry(self.rect().adjusted(12, 12, -12, -12))

    # ------------------------------------------------------------- Ollama
    def check_ollama(self, try_start: bool = False) -> None:
        if self._checking:
            return
        self._checking = True
        url = self.settings.ollama_url

        def job(_worker: Worker):
            client = OllamaClient(url)
            try:
                return "ok", client.list_models(timeout=3)
            except OllamaError as exc:
                if try_start and system.try_start_ollama(url):
                    for _ in range(12):
                        time.sleep(1)
                        try:
                            return "ok", client.list_models(timeout=2)
                        except OllamaError:
                            continue
                return "offline", str(exc)

        worker = Worker(job)
        worker.finished.connect(self._on_ollama_status)
        worker.failed.connect(lambda w, message: self._on_ollama_status(w, ("offline", message)))
        self._workers.add(worker)
        worker.start()

    def _on_ollama_status(self, worker: Worker, result) -> None:
        self._workers.discard(worker)
        self._checking = False
        state, payload = result if result else ("offline", "Unbekannter Fehler")
        if state != "ok":
            self._set_dot(theme.DANGER, payload)
            self.chat_tab.set_banner(
                "⚠️ <b>Ollama läuft nicht.</b> Connor braucht Ollama als lokales KI-Gehirn. "
                "Starte Ollama oder installiere es von "
                f"<a href='{OLLAMA_DOWNLOAD}'>ollama.com/download</a> – danach oben rechts auf ↻ klicken."
            )
            self.chat_tab.set_idle_status("🔴 Keine Verbindung zu Ollama")
            self.recheck_timer.start(20_000)
            return

        models: list[str] = payload
        self.model_box.blockSignals(True)
        self.model_box.clear()
        self.model_box.addItems(models)
        chosen = self.settings.model.strip()
        if not models:
            self.model_box.blockSignals(False)
            self._set_dot(theme.WARNING, "Ollama läuft, aber es ist kein Modell installiert")
            self.chat_tab.set_banner(
                "📦 <b>Noch kein KI-Modell installiert.</b> Öffne ein Terminal (PowerShell) und tippe: "
                f"<code>ollama pull {RECOMMENDED_MODEL}</code> – danach oben rechts auf ↻ klicken."
            )
            self.chat_tab.set_idle_status("🟡 Kein Modell installiert")
            return
        if not chosen:
            chosen = next((m for m in models if m.split(":")[0] == RECOMMENDED_MODEL.split(":")[0]), models[0])
            self.settings.model = chosen
            self.settings.save()
        if chosen not in models:
            self.model_box.addItem(chosen)
            self.chat_tab.set_banner(
                f"📦 Das eingestellte Modell <b>{chosen}</b> ist nicht installiert. Installiere es mit "
                f"<code>ollama pull {chosen}</code> oder wähle oben rechts ein anderes."
            )
        else:
            self.chat_tab.set_banner("")
        self.model_box.setCurrentText(chosen)
        self.model_box.blockSignals(False)
        self._set_dot(theme.SUCCESS, f"Verbunden mit Ollama – {len(models)} Modell(e)")
        self.chat_tab.set_idle_status(f"🔒 Läuft lokal auf deinem PC  ·  Modell: {chosen}")
        if chosen in models:
            self.warm_up()

    def warm_up(self) -> None:
        """Loads the model in the background so the first answer does not take ages."""
        key = (self.settings.model, self.settings.context_size, self.settings.default_mode,
               self.settings.web_enabled, self.settings.use_tools, self.settings.user_name,
               self.settings.extra_instructions)
        if self._warming or key == self._warmed_key:
            return
        self._warming = True
        engine = self.engine

        def job(_worker: Worker) -> str:
            started = time.monotonic()
            model = engine.warm_up()
            log.info("Modell %s ist bereit (%.1f s)", model, time.monotonic() - started)
            return model

        def done(worker: Worker, _result) -> None:
            self._workers.discard(worker)
            self._warming = False
            self._warmed_key = key

        def failed(worker: Worker, message: str) -> None:
            self._workers.discard(worker)
            self._warming = False
            log.info("Vorladen des Modells hat nicht geklappt: %s", message)

        worker = Worker(job)
        worker.finished.connect(done)
        worker.failed.connect(failed)
        self._workers.add(worker)
        worker.start()

    def _set_dot(self, color: str, tooltip: str) -> None:
        self.status_dot.setStyleSheet(f"color: {color};")
        self.status_dot.setToolTip(tooltip)

    def _on_model_chosen(self, _index: int) -> None:
        model = self.model_box.currentText().strip()
        if model and model != self.settings.model:
            self.settings.model = model
            self.settings.save()
            self.check_ollama()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self, backup_now=lambda: self.run_backup(force=True))
        if dialog.exec():
            dialog.apply()
            self.settings.save()
            self.transcriber.configure(self.settings.voice_model, self.settings.voice_language,
                                       self.settings.voice_device)
            self.chat_tab.apply_settings()
            self.check_ollama(try_start=True)
            if self.chat_tab.chat_id is None:
                self.chat_tab.new_chat()

    # ---------------------------------------------------------- reminders
    def run_backup(self, force: bool = False) -> None:
        """Once a day (or right now with ``force``) a copy of all data, in the background."""
        if self._backing_up or not (self.settings.backup_enabled or force):
            return
        try:
            folder = backup.backup_folder(self.settings.backup_dir)
        except OSError as exc:
            log.warning("Backup-Ordner nicht erreichbar: %s", exc)
            return
        if not force and not backup.backup_due(folder):
            return
        self._backing_up = True
        source, keep = Path(self.db.path), self.settings.backup_keep

        def job(_worker: Worker):
            return backup.make_backup(folder, source=source, keep=keep)

        def finished(worker: Worker, path) -> None:
            self._workers.discard(worker)
            self._backing_up = False
            if force and path:
                QMessageBox.information(self, "Backup", f"Gesichert:\n{path}")

        def failed(worker: Worker, message: str) -> None:
            self._workers.discard(worker)
            self._backing_up = False
            log.warning("Backup fehlgeschlagen: %s", message)
            if force:
                QMessageBox.warning(self, "Backup", f"Das Backup hat nicht geklappt:\n{message}")

        worker = Worker(job)
        worker.finished.connect(finished)
        worker.failed.connect(failed)
        self._workers.add(worker)
        worker.start()

    def check_reminders(self) -> None:
        now = datetime.now()
        try:
            due = self.db.due_reminders(now)
            for reminder in due[:10]:
                self.db.acknowledge_reminder(reminder["id"], now)
                self._notify(reminder)
        except Exception:  # noqa: BLE001 - never let the clock crash the app
            log.exception("Erinnerungen konnten nicht geprüft werden")
            return
        if due:
            self.reminders_tab.refresh()

    def _notify(self, reminder: dict) -> None:
        log.info("Erinnerung #%s: %s", reminder["id"], reminder["text"])
        if self.tray is not None:
            self.tray.showMessage("⏰ Connor erinnert dich", reminder["text"], self.icon, 20_000)
        QApplication.beep()
        popup = ReminderPopup(self, reminder, self._snooze)
        popup.show()
        popup.raise_()
        popup.activateWindow()
        QApplication.alert(self)

    def _snooze(self, text: str) -> None:
        self.db.add_reminder(text, datetime.now() + timedelta(minutes=10))
        self.reminders_tab.refresh()

    # ------------------------------------------------------------ closing
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.tray is not None and self.settings.minimize_to_tray and not self._quitting:
            event.ignore()
            self.hide()
            self.writing_tab.save_current()
            if not self.settings.tray_hint_shown:
                self.tray.showMessage(
                    "Connor läuft weiter",
                    "Ich bin unten rechts im Tray und erinnere dich weiterhin. Rechtsklick → Beenden zum Schliessen.",
                    self.icon, 8000,
                )
                self.settings.tray_hint_shown = True
                self._save_settings()
            return
        self._shutdown()
        event.accept()
        QApplication.quit()

    def quit(self) -> None:
        self._quitting = True
        self.close()

    def _save_settings(self) -> None:
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        try:
            self.settings.save()
        except OSError:
            log.exception("Einstellungen konnten nicht gespeichert werden")

    def _shutdown(self) -> None:
        self.reminder_timer.stop()
        QApplication.instance().removeEventFilter(self.drop_watcher)
        self.writing_tab.shutdown()
        self.chat_tab.shutdown()
        self.documents_tab.shutdown()
        for worker in list(self._workers):
            worker.stop()
        self._save_settings()
        if self.tray is not None:
            self.tray.hide()
