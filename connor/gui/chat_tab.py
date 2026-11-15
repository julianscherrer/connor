"""The chat: saved chats on the left, the conversation with Connor on the right."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from ..database import Database
from ..documents import ImportResult, import_paths, supported_extensions
from ..engine import ConnorEngine, Reply
from ..llm import strip_think
from ..paths import export_dir
from ..prompts import HELP_TEXT, MODES, QUICK_ACTIONS
from ..settings import Settings
from ..timeparse import extract_when, format_dt, parse_when
from ..voice import Transcriber
from .common import SUBTITLE_ROLE, TwoLineDelegate, Worker, now_stamp, pretty_stamp
from .voice_drop import MicButton
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


def file_filter() -> str:
    patterns = " ".join(f"*{ext}" for ext in supported_extensions())
    return f"Dokumente und Audio ({patterns});;Alle Dateien (*)"


class ChatTab(QWidget):
    data_changed = Signal(object)  # set of changed areas: {"memory", "reminder", "projects", "documents"}
    connection_problem = Signal()

    def __init__(self, db: Database, engine: ConnorEngine, settings: Settings,
                 transcriber: Callable[[], Transcriber], parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.engine = engine
        self.settings = settings
        self.transcriber = transcriber
        self.chat_id: int | None = None
        self.mode = settings.default_mode if settings.default_mode in MODES else "writer"
        self.pending_docs: list[int] = []
        self.idle_status = "🔒 Läuft lokal auf deinem PC"

        self._active: Worker | None = None
        self._workers: set[Worker] = set()
        self._import_worker: Worker | None = None
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
        self.apply_settings()
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
        header.setContentsMargins(24, 12, 24, 8)
        header.setSpacing(10)
        self.title_label = QLabel("Neuer Chat")
        self.title_label.setObjectName("h2")
        header.addWidget(self.title_label, 1)
        segment_box = QFrame()
        segment_box.setObjectName("segmentBox")
        segment_layout = QHBoxLayout(segment_box)
        segment_layout.setContentsMargins(3, 3, 3, 3)
        segment_layout.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QPushButton] = {}
        tips = {"assistant": "Für alle Fragen – sachlich, klar, mit Websuche wenn nötig",
                "writer": "Kreativer Schreibpartner für Geschichten, Charaktere und Welten"}
        for key in ("assistant", "writer"):
            button = QPushButton(MODES[key])
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setToolTip(tips[key])
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=key: self.set_mode(k))
            self.mode_group.addButton(button)
            segment_layout.addWidget(button)
            self.mode_buttons[key] = button
        header.addWidget(segment_box)
        project_label = QLabel("Projekt:")
        project_label.setObjectName("muted")
        header.addWidget(project_label)
        self.project_box = QComboBox()
        self.project_box.setEditable(True)
        self.project_box.setMinimumWidth(170)
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

        self.attachment_bar = QWidget()
        self.attachment_layout = QHBoxLayout(self.attachment_bar)
        self.attachment_layout.setContentsMargins(24, 8, 24, 0)
        self.attachment_layout.setSpacing(6)
        self.attachment_bar.hide()
        main.addWidget(self.attachment_bar)

        self.chips = QHBoxLayout()
        self.chips.setContentsMargins(24, 8, 24, 0)
        self.chips.setSpacing(6)
        main.addLayout(self.chips)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(24, 8, 24, 4)
        bottom.setSpacing(6)
        self.attach_button = QPushButton("📎")
        self.attach_button.setObjectName("toggle")
        self.attach_button.setMinimumHeight(44)
        self.attach_button.setToolTip("Dateien anhängen (Markdown, Word, PDF, Text, E-Book, Audio).\n"
                                      "Du kannst sie auch einfach ins Fenster ziehen.")
        self.attach_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_button.clicked.connect(self.choose_files)
        bottom.addWidget(self.attach_button, 0, Qt.AlignmentFlag.AlignBottom)
        self.web_button = QPushButton("🌐")
        self.web_button.setObjectName("toggle")
        self.web_button.setCheckable(True)
        self.web_button.setMinimumHeight(44)
        self.web_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.web_button.toggled.connect(self._on_web_toggled)
        bottom.addWidget(self.web_button, 0, Qt.AlignmentFlag.AlignBottom)
        self.input = ChatInput()
        self.input.submitted.connect(self.send)
        bottom.addWidget(self.input, 1)
        self.mic_button = MicButton(self.transcriber)
        self.mic_button.setObjectName("toggle")
        self.mic_button.setMinimumHeight(44)
        self.mic_button.transcribed.connect(self._on_dictated)
        self.mic_button.failed.connect(lambda message: self.view.add_event(f"🎤 {message}", error=True))
        self.mic_button.status.connect(lambda text: self.status.setText(text or self._status_text()))
        bottom.addWidget(self.mic_button, 0, Qt.AlignmentFlag.AlignBottom)
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

    def _status_text(self) -> str:
        if self.web_button.isChecked():
            return self.idle_status + "  ·  🌐 Websuche ist an"
        return self.idle_status

    def set_idle_status(self, text: str) -> None:
        self.idle_status = text
        if not self.busy and not self.mic_button.busy and self._import_worker is None:
            self.status.setText(self._status_text())

    def apply_settings(self) -> None:
        web = self.settings.web_enabled
        self.web_button.setEnabled(web)
        if not web:
            self.web_button.setChecked(False)
        self.web_button.setToolTip(
            "Websuche erzwingen: Connor sucht bei jeder Nachricht zuerst im Internet.\n"
            "Nur die Suchbegriffe gehen ins Netz." if web else "Die Websuche ist in den Einstellungen ausgeschaltet."
        )

    def _on_web_toggled(self, _checked: bool) -> None:
        if not self.busy:
            self.status.setText(self._status_text())

    def insert_template(self, template: str) -> None:
        self.input.setPlainText(template)
        cursor = self.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input.setTextCursor(cursor)
        self.input.setFocus()

    def _rebuild_chips(self) -> None:
        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        for label, template in QUICK_ACTIONS[self.mode]:
            chip = QPushButton(label)
            chip.setObjectName("chip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, t=template: self.insert_template(t))
            self.chips.addWidget(chip)
        self.chips.addStretch(1)

    def welcome_text(self) -> str:
        name = self.settings.user_name.strip()
        hello = f"Hoi {name}! 👋" if name else "Hoi! 👋"
        tips = ("- **📎** Dateien anhängen oder einfach ins Fenster ziehen – ich lese sie\n"
                "- **🌐** Websuche einschalten für aktuelle Infos\n"
                "- **🎤** diktieren statt tippen\n"
                "- `/hilfe` zeigt alle Befehle\n")
        if self.mode == "assistant":
            return (f"## {hello}\n\n"
                    "Ich bin **Connor** im Modus **💬 Allrounder** – frag mich, was du willst: "
                    "Wissen, Alltag, Schule, Technik, Planung, Texte …\n\n" + tips)
        return (f"## {hello}\n\n"
                "Ich bin **Connor** im Modus **✍️ Schreibpartner** – woran schreiben wir heute?\n\n"
                "- Erzähl mir von deiner Story-Idee, ich spinne mit dir weiter\n"
                "- Lass uns einen Charakter, eine Welt oder einen Plot-Twist bauen\n" + tips)

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            return
        self.mode = mode
        self.mode_buttons[mode].setChecked(True)
        if self.chat_id is not None:
            self.db.set_chat_mode(self.chat_id, mode)
        else:
            if self.settings.default_mode != mode:
                self.settings.default_mode = mode
                try:
                    self.settings.save()
                except OSError:
                    log.exception("Einstellungen konnten nicht gespeichert werden")
            self.view.show_welcome(self.welcome_text())
        self._rebuild_chips()
        self.input.setFocus()

    def _show_mode(self, mode: str) -> None:
        self.mode = mode if mode in MODES else "writer"
        self.mode_buttons[self.mode].setChecked(True)
        self._rebuild_chips()

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
            icon = "💬" if chat.get("mode") == "assistant" else "✍️"
            subtitle = f"{icon}  {pretty_stamp(chat['updated_at'])}"
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

    # -------------------------------------------------------- attachments
    def _attached(self) -> list[dict]:
        if self.chat_id is not None:
            return self.db.chat_documents(self.chat_id)
        return [doc for doc in (self.db.get_document(i) for i in self.pending_docs) if doc]

    def refresh_attachments(self) -> None:
        while self.attachment_layout.count():
            item = self.attachment_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        docs = self._attached()
        if self.chat_id is None:
            self.pending_docs = [doc["id"] for doc in docs]
        label = QLabel("Angehängt:")
        label.setObjectName("small")
        self.attachment_layout.addWidget(label)
        for doc in docs[:8]:
            chip = QPushButton(f"📎 {doc['name'].rsplit('/', 1)[-1]}  ✕")
            chip.setObjectName("attachment")
            chip.setToolTip(f"{doc['name']}\n~{doc['chars'] // 6:,} Wörter – klicken zum Entfernen aus diesem Chat"
                            .replace(",", "'"))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, i=doc["id"]: self.detach(i))
            self.attachment_layout.addWidget(chip)
        if len(docs) > 8:
            more = QLabel(f"+ {len(docs) - 8} weitere")
            more.setObjectName("small")
            self.attachment_layout.addWidget(more)
        self.attachment_layout.addStretch(1)
        self.attachment_bar.setVisible(bool(docs))

    def detach(self, doc_id: int) -> None:
        if self.chat_id is not None:
            self.db.detach_document(self.chat_id, doc_id)
        elif doc_id in self.pending_docs:
            self.pending_docs.remove(doc_id)
        self.refresh_attachments()

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Dateien für Connor", "", file_filter())
        if paths:
            self.add_files(paths)

    def add_files(self, paths: list[str]) -> None:
        if self._import_worker is not None:
            QMessageBox.information(self, "Moment", "Ich lese gerade noch andere Dateien – gleich fertig.")
            return
        project = self.project()
        db = self.db
        transcriber = self.transcriber()

        def job(worker: Worker) -> ImportResult:
            return import_paths(db, paths, project=project, transcribe=transcriber.file_transcriber(),
                                progress=worker.emit_event, should_stop=lambda: worker.cancelled)

        worker = Worker(job)
        worker.context["chat_id"] = self.chat_id
        worker.event.connect(lambda _w, text: self.status.setText(text))
        worker.finished.connect(self._on_imported)
        worker.failed.connect(self._on_import_failed)
        self._import_worker = worker
        self.status.setText("📥 Lese Dateien …")
        worker.start()

    def _on_imported(self, worker: Worker, result: ImportResult | None) -> None:
        self._import_worker = None
        self.status.setText(self._status_text())
        if result is None:
            return
        ids = [doc_id for doc_id, _name in result.imported]
        summary = result.summary()
        large = [d for d in (self.db.get_document(i) for i in ids) if d and d["chars"] > self.engine.context_budget()]
        if large:
            summary += ("\nℹ️ Grosse Dateien kann ich nicht auf einmal lesen – ich nutze jeweils die Stellen, "
                        "die zu deiner Frage passen.")
        target = worker.context.get("chat_id")
        if target is not None:
            for doc_id in ids:
                self.db.attach_document(target, doc_id)
            self.db.add_message(target, "event", summary)
        else:
            self.pending_docs += [i for i in ids if i not in self.pending_docs]
        if target == self.chat_id:
            self.view.add_event(summary, error=not result.imported)
            self.view.scroll_to_bottom()
        self.refresh_attachments()
        self.data_changed.emit({"documents", "projects"})

    def _on_import_failed(self, _worker: Worker, message: str) -> None:
        self._import_worker = None
        self.status.setText(self._status_text())
        self.view.add_event(f"⚠️ Dateien konnten nicht gelesen werden: {message}", error=True)

    def _on_dictated(self, text: str) -> None:
        current = self.input.toPlainText()
        if current and not current.endswith((" ", "\n")):
            text = " " + text
        cursor = self.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.input.setTextCursor(cursor)
        self.input.setFocus()
        if self.settings.voice_autosend:
            self.send()

    # -------------------------------------------------------------- chats
    def new_chat(self) -> None:
        if self.busy:
            self.stop()
        self.chat_id = None
        self.title_label.setText("Neuer Chat")
        self._show_mode(self.settings.default_mode)
        self.view.show_welcome(self.welcome_text())
        self.chat_list.blockSignals(True)
        self.chat_list.setCurrentItem(None)
        self.chat_list.clearSelection()
        self.chat_list.blockSignals(False)
        self.refresh_attachments()
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
        self._show_mode(chat.get("mode", "writer"))
        self.project_box.blockSignals(True)
        self.project_box.setCurrentText(chat["project"])
        self.project_box.blockSignals(False)
        self.view.clear()
        for message in self.db.get_messages(chat_id):
            if message["role"] in ("user", "assistant"):
                self.view.add_message(message["role"], message["content"], pretty_stamp(message["created_at"]))
            elif message["role"] == "event":
                self.view.add_event(message["content"])
        self.refresh_attachments()
        self.view.scroll_to_bottom()
        self.input.setFocus()

    def _ensure_chat(self, first_text: str) -> int:
        if self.chat_id is None:
            title = make_title(first_text)
            self.chat_id = self.db.create_chat(title, self.project(), self.mode)
            self.title_label.setText(title)
            pending = [doc for doc in (self.db.get_document(i) for i in self.pending_docs) if doc]
            for doc in pending:
                self.db.attach_document(self.chat_id, doc["id"])
            if pending:
                self.db.add_message(self.chat_id, "event",
                                    "📎 Angehängt: " + ", ".join(doc["name"] for doc in pending))
            self.pending_docs = []
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
        self._ask(text, force_web=self.web_button.isChecked() and self.settings.web_enabled)

    def _ask(self, text: str, *, force_web: bool = False, web_query: str | None = None) -> None:
        chat_id = self._ensure_chat(text)
        self.db.add_message(chat_id, "user", text)
        self.open_chat_view_if_needed(chat_id)
        self.view.add_message("user", text, now_stamp())
        self.view.scroll_to_bottom()
        self.refresh_attachments()
        engine = self.engine

        def job(worker: Worker) -> Reply:
            return engine.respond(chat_id, on_chunk=worker.emit_chunk, on_event=worker.emit_event,
                                  token=worker.token, confirm=worker.ask, force_web=force_web, web_query=web_query)

        self._start(job, chat_id)
        self.refresh_chat_list()

    def open_chat_view_if_needed(self, chat_id: int) -> None:
        """A brand-new chat may have stored attachment notes – show them before the first message."""
        if self.view.has_welcome():
            self.view.clear()
            for message in self.db.get_messages(chat_id):
                if message["role"] == "event":
                    self.view.add_event(message["content"])

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
        self.status.setText("✍️ Connor denkt nach …" if busy else self._status_text())
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
            if self._bubble is not None and not self._bubble.visible_text():
                self.view.remove_bubble(self._bubble)
        else:
            if self._bubble is not None:
                self.view.remove_bubble(self._bubble)
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
        self.mic_button.cancel()
        for worker in [*self._workers, self._import_worker]:
            if worker is not None:
                worker.stop()

    # ----------------------------------------------------------- commands
    def _local_event(self, chat_text: str, message: str, error: bool = False) -> None:
        chat_id = self._ensure_chat(chat_text)
        self.open_chat_view_if_needed(chat_id)
        self.db.add_message(chat_id, "user", chat_text)
        self.db.add_message(chat_id, "event", message)
        self.view.add_message("user", chat_text, now_stamp())
        self.view.add_event(message, error=error)
        self.view.scroll_to_bottom()
        self.refresh_attachments()
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
        self._ask(text, force_web=True, web_query=query)
        return True
