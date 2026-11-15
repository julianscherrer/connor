"""The chat: list of saved chats on the left, conversation with Connor on the right."""
from __future__ import annotations

import logging
import re
from datetime import datetime

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from ..database import Database
from ..engine import ConnorEngine, Reply
from ..llm import OllamaError, strip_think
from ..paths import export_dir
from ..prompts import HELP_TEXT, QUICK_ACTIONS
from ..settings import Settings
from ..timeparse import extract_when, format_dt, parse_when
from ..tools import ToolExecutor
from .common import SUBTITLE_ROLE, TwoLineDelegate, Worker, now_stamp, pretty_stamp
from .widgets import ChatInput, ChatView, MessageBubble

log = logging.getLogger(__name__)


def make_title(text: str, limit: int = 48) -> str:
    first = " ".join(text.strip().splitlines()[0].split()) if text.strip() else "Neuer Chat"
    if first.lower().startswith("/suche "):
        first = "Suche: " + first[7:].strip()
    first = first.lstrip("/")
    if len(first) <= limit:
        return first or "Neuer Chat"
    cut = first[:limit].rsplit(" ", 1)[0]
    return (cut or first[:limit]) + " …"


class ChatTab(QWidget):
    data_changed = Signal(object)  # set of changed areas: {"memory", "reminder", "projects"}
    connection_problem = Signal()

    def __init__(self, db: Database, engine: ConnorEngine, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.engine = engine
        self.settings = settings
        self.chat_id: int | None = None
        self.idle_status = "🔒 Läuft lokal auf deinem PC"

        self._active: Worker | None = None
        self._workers: set[Worker] = set()
        self._bubble: MessageBubble | None = None
        self._raw = ""
        self._segment_start = 0

        self._render_timer = QTimer(self)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._flush)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh_chat_list)

        self._build_ui()
        self.refresh_projects()
        self.refresh_chat_list()
        self.new_chat()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(260)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 14, 12, 12)
        side_layout.setSpacing(8)
        new_button = QPushButton("＋  Neuer Chat")
        new_button.setObjectName("primary")
        new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        new_button.clicked.connect(self.new_chat)
        side_layout.addWidget(new_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔎  Chats durchsuchen …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        side_layout.addWidget(self.search)
        self.chat_list = QListWidget()
        self.chat_list.setObjectName("sideList")
        self.chat_list.setItemDelegate(TwoLineDelegate(self.chat_list))
        self.chat_list.setMouseTracking(True)
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self._chat_menu)
        self.chat_list.currentItemChanged.connect(self._on_chat_selected)
        side_layout.addWidget(self.chat_list, 1)
        privacy = QLabel("🔒 Alles bleibt auf deinem PC")
        privacy.setObjectName("small")
        side_layout.addWidget(privacy)
        root.addWidget(side)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(24, 14, 24, 8)
        self.title_label = QLabel("Neuer Chat")
        self.title_label.setObjectName("h2")
        header.addWidget(self.title_label, 1)
        project_label = QLabel("Projekt:")
        project_label.setObjectName("muted")
        header.addWidget(project_label)
        self.project_box = QComboBox()
        self.project_box.setEditable(True)
        self.project_box.setMinimumWidth(200)
        self.project_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.project_box.lineEdit().setPlaceholderText("kein Projekt")
        self.project_box.setToolTip("Zu welcher Geschichte gehört dieser Chat? Connor holt dann passende Erinnerungen.")
        self.project_box.activated.connect(lambda _index: self._project_changed())
        self.project_box.lineEdit().editingFinished.connect(self._project_changed)
        header.addWidget(self.project_box)
        main.addLayout(header)

        banner_row = QHBoxLayout()
        banner_row.setContentsMargins(24, 0, 24, 6)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.setTextFormat(Qt.TextFormat.RichText)
        self.banner.setOpenExternalLinks(True)
        self.banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.banner.hide()
        banner_row.addWidget(self.banner)
        main.addLayout(banner_row)

        self.view = ChatView()
        main.addWidget(self.view, 1)

        chips = QHBoxLayout()
        chips.setContentsMargins(24, 8, 24, 0)
        chips.setSpacing(6)
        for label, template in QUICK_ACTIONS:
            chip = QPushButton(label)
            chip.setObjectName("chip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, t=template: self.insert_template(t))
            chips.addWidget(chip)
        chips.addStretch(1)
        main.addLayout(chips)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(24, 8, 24, 4)
        bottom.setSpacing(8)
        self.input = ChatInput()
        self.input.submitted.connect(self.send)
        bottom.addWidget(self.input, 1)
        self.send_button = QPushButton("Senden  ➤")
        self.send_button.setObjectName("primary")
        self.send_button.setMinimumHeight(44)
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.send)
        bottom.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        self.stop_button = QPushButton("■  Stopp")
        self.stop_button.setObjectName("danger")
        self.stop_button.setMinimumHeight(44)
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.hide()
        bottom.addWidget(self.stop_button, 0, Qt.AlignmentFlag.AlignBottom)
        main.addLayout(bottom)

        self.status = QLabel(self.idle_status)
        self.status.setObjectName("small")
        self.status.setContentsMargins(26, 2, 24, 10)
        main.addWidget(self.status)
        root.addLayout(main, 1)

    # ------------------------------------------------------------ helpers
    @property
    def busy(self) -> bool:
        return self._active is not None

    def project(self) -> str:
        return self.project_box.currentText().strip()

    def set_banner(self, html: str) -> None:
        self.banner.setText(html)
        self.banner.setVisible(bool(html))

    def set_idle_status(self, text: str) -> None:
        self.idle_status = text
        if not self.busy:
            self.status.setText(text)

    def insert_template(self, template: str) -> None:
        self.input.setPlainText(template)
        cursor = self.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input.setTextCursor(cursor)
        self.input.setFocus()

    def welcome_text(self) -> str:
        name = self.settings.user_name.strip()
        hello = f"Hoi {name}! 👋" if name else "Hoi! 👋"
        return (
            f"## {hello}\n\n"
            "Ich bin **Connor**, dein KI-Kumpel – und ich laufe komplett auf deinem PC.\n\n"
            "Woran arbeiten wir heute?\n\n"
            "- Erzähl mir von deiner Story-Idee, ich spinne mit dir weiter\n"
            "- Lass uns einen Charakter, eine Welt oder einen Plot-Twist bauen\n"
            "- Sag mir, woran ich dich erinnern soll\n"
            "- Tippe `/hilfe` für alle Befehle\n"
        )

    def refresh_projects(self) -> None:
        current = self.project_box.currentText()
        self.project_box.blockSignals(True)
        self.project_box.clear()
        self.project_box.addItem("")
        self.project_box.addItems(self.db.list_projects())
        self.project_box.setCurrentText(current)
        self.project_box.blockSignals(False)

    def refresh_chat_list(self) -> None:
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        for chat in self.db.list_chats(self.search.text()):
            item = QListWidgetItem(chat["title"])
            item.setData(Qt.ItemDataRole.UserRole, chat["id"])
            subtitle = pretty_stamp(chat["updated_at"])
            if chat["project"]:
                subtitle += f"  ·  {chat['project']}"
            item.setData(SUBTITLE_ROLE, subtitle)
            item.setToolTip(f"{chat['title']}\n{subtitle}")
            self.chat_list.addItem(item)
            if chat["id"] == self.chat_id:
                self.chat_list.setCurrentItem(item)
        self.chat_list.blockSignals(False)

    def _project_changed(self) -> None:
        project = self.project()
        if self.chat_id is None:
            return
        chat = self.db.get_chat(self.chat_id)
        if chat and chat["project"] != project:
            self.db.set_chat_project(self.chat_id, project)
            self.refresh_chat_list()
            self.data_changed.emit({"projects"})

    # -------------------------------------------------------------- chats
    def new_chat(self) -> None:
        if self.busy:
            self.stop()
        self.chat_id = None
        self.title_label.setText("Neuer Chat")
        self.view.show_welcome(self.welcome_text())
        self.chat_list.blockSignals(True)
        self.chat_list.setCurrentItem(None)
        self.chat_list.clearSelection()
        self.chat_list.blockSignals(False)
        self.input.setFocus()

    def _on_chat_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        chat_id = current.data(Qt.ItemDataRole.UserRole)
        if chat_id != self.chat_id:
            self.open_chat(chat_id)

    def open_chat(self, chat_id: int) -> None:
        chat = self.db.get_chat(chat_id)
        if chat is None:
            return
        if self.busy:
            self.stop()
        self.chat_id = chat_id
        self.title_label.setText(chat["title"])
        self.project_box.blockSignals(True)
        self.project_box.setCurrentText(chat["project"])
        self.project_box.blockSignals(False)
        self.view.clear()
        for message in self.db.get_messages(chat_id):
            if message["role"] in ("user", "assistant"):
                self.view.add_message(message["role"], message["content"], pretty_stamp(message["created_at"]))
            elif message["role"] == "event":
                self.view.add_event(message["content"])
        self.view.scroll_to_bottom()
        self.input.setFocus()

    def _ensure_chat(self, first_text: str) -> int:
        if self.chat_id is None:
            title = make_title(first_text)
            self.chat_id = self.db.create_chat(title, self.project())
            self.title_label.setText(title)
        return self.chat_id

    def _chat_menu(self, pos: QPoint) -> None:
        item = self.chat_list.itemAt(pos)
        if item is None:
            return
        chat_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        rename = menu.addAction("✏️  Umbenennen")
        export = menu.addAction("💾  Als Markdown exportieren")
        menu.addSeparator()
        delete = menu.addAction("🗑️  Löschen")
        chosen = menu.exec(self.chat_list.viewport().mapToGlobal(pos))
        if chosen is rename:
            self.rename_chat(chat_id)
        elif chosen is export:
            self.export_chat(chat_id)
        elif chosen is delete:
            self.delete_chat(chat_id)

    def rename_chat(self, chat_id: int) -> None:
        chat = self.db.get_chat(chat_id)
        if not chat:
            return
        title, ok = QInputDialog.getText(self, "Chat umbenennen", "Neuer Name:", text=chat["title"])
        if ok and title.strip():
            self.db.rename_chat(chat_id, title.strip())
            if chat_id == self.chat_id:
                self.title_label.setText(title.strip())
            self.refresh_chat_list()

    def export_chat(self, chat_id: int) -> None:
        chat = self.db.get_chat(chat_id)
        if not chat:
            return
        safe = re.sub(r"[^\w\- ]+", "", chat["title"]).strip() or f"chat-{chat_id}"
        path, _ = QFileDialog.getSaveFileName(self, "Chat exportieren", str(export_dir() / f"{safe}.md"),
                                              "Markdown (*.md)")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.db.export_chat_markdown(chat_id))

    def delete_chat(self, chat_id: int) -> None:
        chat = self.db.get_chat(chat_id)
        if not chat:
            return
        answer = QMessageBox.question(self, "Chat löschen?",
                                      f"«{chat['title']}» wirklich löschen? Das kann man nicht rückgängig machen.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if chat_id == self.chat_id:
            self.new_chat()
        self.db.delete_chat(chat_id)
        self.refresh_chat_list()

    # ------------------------------------------------------------ sending
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.busy:
            return
        if text.startswith("/") and self._handle_command(text):
            self.input.clear()
            return
        self.input.clear()
        chat_id = self._ensure_chat(text)
        self.db.add_message(chat_id, "user", text)
        self.view.add_message("user", text, now_stamp())
        self.view.scroll_to_bottom()
        engine = self.engine

        def job(worker: Worker) -> Reply:
            return engine.respond(chat_id, on_chunk=worker.emit_chunk, on_event=worker.emit_event,
                                  token=worker.token, confirm=worker.ask)

        self._start(job, chat_id)
        self.refresh_chat_list()

    def _start(self, job, chat_id: int) -> None:
        worker = Worker(job)
        worker.context["chat_id"] = chat_id
        worker.chunk.connect(self._on_chunk)
        worker.event.connect(self._on_event)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.confirm_request.connect(self._on_confirm)
        self._workers.add(worker)
        self._active = worker
        self._bubble = None
        self._raw = ""
        self._segment_start = 0
        self._set_busy(True)
        worker.start()

    def _set_busy(self, busy: bool) -> None:
        self.send_button.setVisible(not busy)
        self.stop_button.setVisible(busy)
        self.status.setText("✍️ Connor denkt nach …" if busy else self.idle_status)
        if busy:
            self._render_timer.start()
        else:
            self._render_timer.stop()

    def _flush(self) -> None:
        if self._bubble is not None:
            segment = self._raw[self._segment_start:]
            if segment != self._bubble.raw:
                self._bubble.set_text(segment)

    def _on_chunk(self, worker: Worker, text: str) -> None:
        if worker is not self._active:
            return
        if self._bubble is None:
            self._bubble = self.view.add_message("assistant", "", now_stamp())
            self.status.setText("✍️ Connor schreibt …")
        self._raw += text

    def _on_event(self, worker: Worker, text: str) -> None:
        if worker is not self._active:
            return
        self.db.add_message(worker.context["chat_id"], "event", text)
        self._flush()
        if self._bubble is not None and not self._bubble.visible_text():
            self.view.remove_bubble(self._bubble)
        self._bubble = None
        self._segment_start = len(self._raw)
        if worker.context.get("chat_id") == self.chat_id:
            self.view.add_event(text)
        self.status.setText(text)

    def _finish_worker(self, worker: Worker) -> bool:
        """Returns True if the worker was the active one."""
        self._workers.discard(worker)
        if worker is not self._active:
            return False
        self._active = None
        self._flush()
        self._set_busy(False)
        return True

    def _on_finished(self, worker: Worker, reply: Reply | None) -> None:
        if not self._finish_worker(worker):
            return
        text = reply.text if reply else ""
        if text:
            self.db.add_message(worker.context["chat_id"], "assistant", text)
        else:
            if self._bubble is not None:
                self.view.remove_bubble(self._bubble)
                self._bubble = None
            if not worker.cancelled:
                self.view.add_event("Connor hat keine Antwort geliefert. Versuch es nochmal oder wähle "
                                    "oben rechts ein anderes Modell.", error=True)
        self._bubble = None
        if reply and reply.changes:
            self.data_changed.emit(reply.changes)
        self.refresh_chat_list()

    def _on_failed(self, worker: Worker, message: str) -> None:
        if not self._finish_worker(worker):
            return
        partial = strip_think(self._raw).strip()
        if partial:
            self.db.add_message(worker.context["chat_id"], "assistant", partial)
        self._bubble = None
        self.view.add_event(f"⚠️ {message}", error=True)
        self.connection_problem.emit()

    def _on_confirm(self, worker: Worker, question: str) -> None:
        if worker is not self._active:
            worker.answer(False)
            return
        answer = QMessageBox.question(
            self, "Ins Internet?", f"{question}\n\nNur diese Anfrage geht ins Internet – deine Chats bleiben hier."
        )
        worker.answer(answer == QMessageBox.StandardButton.Yes)

    def stop(self) -> None:
        worker = self._active
        if worker is None:
            return
        worker.stop()
        self._active = None
        self._flush()
        self._set_busy(False)
        partial = strip_think(self._raw).strip()
        if partial:
            self.db.add_message(worker.context["chat_id"], "assistant", partial + " …")
        elif self._bubble is not None:
            self.view.remove_bubble(self._bubble)
        self._bubble = None
        self.view.add_event("⏹ Abgebrochen")

    def shutdown(self) -> None:
        for worker in list(self._workers):
            worker.stop()

    # ----------------------------------------------------------- commands
    def _local_event(self, chat_text: str, message: str, error: bool = False) -> None:
        chat_id = self._ensure_chat(chat_text)
        self.db.add_message(chat_id, "user", chat_text)
        self.db.add_message(chat_id, "event", message)
        self.view.add_message("user", chat_text, now_stamp())
        self.view.add_event(message, error=error)
        self.view.scroll_to_bottom()
        self.refresh_chat_list()

    def _handle_command(self, text: str) -> bool:
        command, _, argument = text.partition(" ")
        command = command.lower()
        argument = argument.strip()
        if command in ("/hilfe", "/help", "/?"):
            self.view.add_message("assistant", HELP_TEXT, now_stamp())
            self.view.scroll_to_bottom()
            return True
        if command == "/merke":
            return self._command_remember(text, argument)
        if command == "/erinnere":
            return self._command_remind(text, argument)
        if command == "/suche":
            return self._command_search(text, argument)
        return False

    def _command_remember(self, text: str, argument: str) -> bool:
        if not argument:
            self.view.add_event("So geht's: /merke Titel: Inhalt   (z. B. /merke Lira: Elfe, 19, hat Angst vor Wasser)")
            return True
        title, separator, content = argument.partition(":")
        if not separator or not content.strip():
            words = argument.split()
            title, content = " ".join(words[:4]) + (" …" if len(words) > 4 else ""), argument
        memory_id, created = self.db.save_memory(title.strip()[:80], content.strip(), "notiz", self.project())
        verb = "Gemerkt" if created else "Ergänzt"
        self._local_event(text, f"🧠 {verb}: «{title.strip()}» (#{memory_id}) – du findest es im Tab «Gedächtnis».")
        self.data_changed.emit({"memory", "projects"})
        return True

    def _command_remind(self, text: str, argument: str) -> bool:
        if "|" in argument:
            when_part, _, what = argument.partition("|")
            due, what = parse_when(when_part), what.strip()
        else:
            due, what = extract_when(argument)
        if not argument or due is None or not what:
            self.view.add_event("So geht's: /erinnere morgen 18:00 Mama anrufen   (oder: /erinnere 20.11. 9 Uhr | Zahnarzt)")
            return True
        if due < datetime.now():
            self.view.add_event(f"{format_dt(due)} liegt in der Vergangenheit – gib einen späteren Zeitpunkt an.",
                                error=True)
            return True
        reminder_id = self.db.add_reminder(what, due)
        self._local_event(text, f"⏰ Erinnerung #{reminder_id}: {format_dt(due)} – {what}")
        self.data_changed.emit({"reminder"})
        return True

    def _command_search(self, text: str, query: str) -> bool:
        if not query:
            self.view.add_event("So geht's: /suche wie funktioniert ein Langbogen")
            return True
        if not self.settings.web_enabled:
            self.view.add_event("Die Websuche ist ausgeschaltet. Du kannst sie in den Einstellungen (⚙) aktivieren.",
                                error=True)
            return True
        chat_id = self._ensure_chat(text)
        self.db.add_message(chat_id, "user", text)
        self.view.add_message("user", text, now_stamp())
        self.view.scroll_to_bottom()
        engine, db, settings = self.engine, self.db, self.settings

        def job(worker: Worker) -> Reply:
            executor = ToolExecutor(db, settings, confirm=worker.ask)
            worker.emit_event(executor.describe("web_search", {"query": query}))
            results = executor.execute("web_search", {"query": query})
            if results.startswith("FEHLER") or results.startswith("Der User hat"):
                raise RuntimeError(results.removeprefix("FEHLER: "))
            augment = ("Ich habe gerade im Internet gesucht. Hier sind die Ergebnisse:\n\n" + results +
                       "\n\nFasse das Wichtigste für mich zusammen und nenne die Quellen als Links.")
            try:
                return engine.respond(chat_id, on_chunk=worker.emit_chunk, on_event=worker.emit_event,
                                      token=worker.token, confirm=worker.ask, augment=augment)
            except OllamaError as exc:
                fallback = f"Zusammenfassen klappt gerade nicht ({exc}). Hier sind die Treffer:\n\n{results}"
                worker.emit_chunk(fallback)
                return Reply(fallback)

        self._start(job, chat_id)
        self.refresh_chat_list()
        return True
