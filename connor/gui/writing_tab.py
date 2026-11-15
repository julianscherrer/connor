"""The writing desk: your stories, with Connor continuing, brainstorming or giving feedback."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTextBrowser, QVBoxLayout,
                               QWidget)

from ..database import Database
from ..documents import ImportResult, collect_files, import_paths, normalize, read_document
from ..engine import ConnorEngine, Reply
from ..llm import strip_think
from ..prompts import build_writing_prompt
from ..settings import Settings
from ..voice import Transcriber
from .common import SUBTITLE_ROLE, TwoLineDelegate, Worker, pretty_stamp
from .voice_drop import MicButton

log = logging.getLogger(__name__)

ACTION_TITLES = {
    "continue": "✨ Connor schreibt weiter",
    "ideas": "💡 Ideen, wie es weitergehen könnte",
    "feedback": "🔎 Ehrliches Feedback",
}
LENGTHS = [("kurz", "Kurz (1 Absatz)"), ("mittel", "Mittel (2–3 Absätze)"), ("lang", "Lang (ganze Szene)")]


def continuation_separator(story: str) -> str:
    """How Connor's continuation is glued to the end of the story."""
    if not story.strip() or story.endswith("\n\n"):
        return ""
    if story.endswith("\n"):
        return "\n"
    if story.rstrip()[-1:] in ".!?…»«\"“”')":
        return "\n\n"
    return "" if story.endswith(" ") else " "


class WritingTab(QWidget):
    data_changed = Signal(object)

    def __init__(self, db: Database, engine: ConnorEngine, settings: Settings,
                 transcriber: Callable[[], Transcriber], parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.engine = engine
        self.settings = settings
        self.transcriber = transcriber
        self.story_id: int | None = None
        self._loading = False
        self._dirty = False
        self._active: Worker | None = None
        self._workers: set[Worker] = set()
        self._import_worker: Worker | None = None
        self._suggestion = ""
        self._action = ""

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1200)
        self._save_timer.timeout.connect(self.save_current)
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(80)
        self._render_timer.timeout.connect(self._render_suggestion)

        self._build_ui()
        self.refresh_projects()
        self.refresh_list()
        stories = self.db.list_stories()
        if stories:
            self.load_story(stories[0]["id"])
        else:
            self._update_count()

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
        new_button = QPushButton("＋  Neue Geschichte")
        new_button.setObjectName("primary")
        new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        new_button.clicked.connect(self.new_story)
        side_layout.addWidget(new_button)
        self.story_list = QListWidget()
        self.story_list.setObjectName("sideList")
        self.story_list.setItemDelegate(TwoLineDelegate(self.story_list))
        self.story_list.setMouseTracking(True)
        self.story_list.currentItemChanged.connect(self._on_story_selected)
        side_layout.addWidget(self.story_list, 1)
        delete_button = QPushButton("🗑️  Geschichte löschen")
        delete_button.clicked.connect(self.delete_story)
        side_layout.addWidget(delete_button)
        root.addWidget(side)

        main = QVBoxLayout()
        main.setContentsMargins(24, 12, 24, 12)
        main.setSpacing(8)

        top = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("titleEdit")
        self.title_edit.setPlaceholderText("Titel deiner Geschichte")
        self.title_edit.textEdited.connect(self._on_edit)
        top.addWidget(self.title_edit, 1)
        project_label = QLabel("Projekt:")
        project_label.setObjectName("muted")
        top.addWidget(project_label)
        self.project_box = QComboBox()
        self.project_box.setEditable(True)
        self.project_box.setMinimumWidth(200)
        self.project_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.project_box.lineEdit().setPlaceholderText("kein Projekt")
        self.project_box.setToolTip("Connor nutzt die gespeicherten Fakten (Charaktere, Orte …) dieses Projekts.")
        self.project_box.lineEdit().textEdited.connect(self._on_edit)
        self.project_box.activated.connect(lambda _index: self._on_edit())
        top.addWidget(self.project_box)
        main.addLayout(top)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("storyEditor")
        self.editor.setPlaceholderText("Es war einmal …\n\nSchreib einfach los. Wenn du feststeckst, hilft dir Connor unten weiter.")
        editor_font = QFont("Georgia")
        editor_font.setPointSizeF(12.5)
        self.editor.setFont(editor_font)
        self.editor.textChanged.connect(self._on_text_changed)
        self.splitter.addWidget(self.editor)

        self.panel = QFrame()
        self.panel.setObjectName("panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(14, 10, 14, 10)
        panel_header = QHBoxLayout()
        self.panel_title = QLabel("✨ Connors Vorschlag")
        self.panel_title.setObjectName("h2")
        panel_header.addWidget(self.panel_title, 1)
        self.panel_status = QLabel("")
        self.panel_status.setObjectName("small")
        panel_header.addWidget(self.panel_status)
        panel_layout.addLayout(panel_header)
        self.suggestion_view = QTextBrowser()
        self.suggestion_view.setOpenExternalLinks(True)
        self.suggestion_view.setFont(editor_font)
        panel_layout.addWidget(self.suggestion_view, 1)
        buttons = QHBoxLayout()
        self.accept_button = QPushButton("✓  Übernehmen")
        self.accept_button.setObjectName("primary")
        self.accept_button.clicked.connect(self.accept_suggestion)
        self.remember_button = QPushButton("🧠  Als Idee merken")
        self.remember_button.clicked.connect(self.remember_suggestion)
        self.retry_button = QPushButton("↻  Nochmal")
        self.retry_button.clicked.connect(lambda: self.run_action(self._action))
        self.discard_button = QPushButton("✕  Verwerfen")
        self.discard_button.clicked.connect(self.hide_suggestion)
        for button in (self.accept_button, self.remember_button, self.retry_button, self.discard_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        panel_layout.addLayout(buttons)
        self.splitter.addWidget(self.panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.panel.hide()
        main.addWidget(self.splitter, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("small")
        main.addWidget(self.count_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.mic_button = MicButton(self.transcriber, "🎤  Diktieren")
        self.mic_button.setToolTip("Diktieren: klicken, sprechen, nochmals klicken – der Text landet an der "
                                   "Cursor-Position.\nDie Spracherkennung läuft lokal auf deinem PC.")
        self.mic_button.transcribed.connect(self._on_dictated)
        self.mic_button.failed.connect(lambda message: QMessageBox.information(self, "Diktieren", message))
        self.mic_button.status.connect(lambda text: self.count_label.setText(text) if text else self._update_count())
        actions.addWidget(self.mic_button)
        self.hint_edit = QLineEdit()
        self.hint_edit.setPlaceholderText("Wunsch an Connor (optional) – z. B. «mehr Spannung» oder «Dialog zwischen Lira und Tom»")
        actions.addWidget(self.hint_edit, 1)
        self.length_box = QComboBox()
        for key, label in LENGTHS:
            self.length_box.addItem(label, key)
        self.length_box.setCurrentIndex(1)
        actions.addWidget(self.length_box)
        self.continue_button = QPushButton("✨  Weiterschreiben")
        self.continue_button.setObjectName("primary")
        self.continue_button.clicked.connect(lambda: self.run_action("continue"))
        self.ideas_button = QPushButton("💡  Ideen")
        self.ideas_button.clicked.connect(lambda: self.run_action("ideas"))
        self.feedback_button = QPushButton("🔎  Feedback")
        self.feedback_button.clicked.connect(lambda: self.run_action("feedback"))
        self.stop_button = QPushButton("■  Stopp")
        self.stop_button.setObjectName("danger")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.hide()
        for button in (self.continue_button, self.ideas_button, self.feedback_button, self.stop_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        main.addLayout(actions)
        root.addLayout(main, 1)

    # ------------------------------------------------------------- stories
    def project(self) -> str:
        return self.project_box.currentText().strip()

    def refresh_projects(self) -> None:
        current = self.project_box.currentText()
        self.project_box.blockSignals(True)
        self.project_box.clear()
        self.project_box.addItem("")
        self.project_box.addItems(self.db.list_projects())
        self.project_box.setCurrentText(current)
        self.project_box.blockSignals(False)

    def refresh_list(self) -> None:
        self.story_list.blockSignals(True)
        self.story_list.clear()
        for story in self.db.list_stories():
            item = QListWidgetItem(story["title"] or "Ohne Titel")
            item.setData(Qt.ItemDataRole.UserRole, story["id"])
            words = f"{story['chars'] // 6:,}".replace(",", "'")
            subtitle = f"{pretty_stamp(story['updated_at'])}  ·  ~{words} Wörter"
            if story["project"]:
                subtitle += f"  ·  {story['project']}"
            item.setData(SUBTITLE_ROLE, subtitle)
            self.story_list.addItem(item)
            if story["id"] == self.story_id:
                self.story_list.setCurrentItem(item)
        self.story_list.blockSignals(False)

    def _on_story_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is not None and current.data(Qt.ItemDataRole.UserRole) != self.story_id:
            self.load_story(current.data(Qt.ItemDataRole.UserRole))

    def load_story(self, story_id: int) -> None:
        self.save_current()
        story = self.db.get_story(story_id)
        if story is None:
            return
        self.stop()
        self.hide_suggestion()
        self._loading = True
        self.story_id = story_id
        self.title_edit.setText(story["title"])
        self.project_box.setCurrentText(story["project"])
        self.editor.setPlainText(story["content"])
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.editor.ensureCursorVisible()
        self._loading = False
        self._dirty = False
        self._update_count(saved=True)
        self.refresh_list()

    def new_story(self) -> None:
        self.save_current()
        story_id = self.db.create_story("Neue Geschichte", self.project())
        self.load_story(story_id)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def delete_story(self) -> None:
        if self.story_id is None:
            return
        story = self.db.get_story(self.story_id)
        title = story["title"] if story else "diese Geschichte"
        answer = QMessageBox.question(self, "Geschichte löschen?",
                                      f"«{title}» wirklich löschen? Das kann man nicht rückgängig machen.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_story(self.story_id)
        self.story_id = None
        self._dirty = False
        self._loading = True
        self.title_edit.clear()
        self.editor.clear()
        self._loading = False
        stories = self.db.list_stories()
        if stories:
            self.load_story(stories[0]["id"])
        else:
            self.refresh_list()
            self._update_count()

    def _on_edit(self, *_args) -> None:
        if self._loading:
            return
        if self.story_id is None:
            title = self.title_edit.text().strip() or "Neue Geschichte"
            self.story_id = self.db.create_story(title, self.project())
        self._dirty = True
        self._update_count(saved=False)
        self._save_timer.start()

    def _on_text_changed(self) -> None:
        if not self._loading:
            self._on_edit()

    def save_current(self) -> None:
        self._save_timer.stop()
        if self.story_id is None or not self._dirty:
            return
        self.db.update_story(self.story_id, title=self.title_edit.text().strip() or "Ohne Titel",
                             project=self.project(), content=self.editor.toPlainText())
        self._dirty = False
        self._update_count(saved=True)
        self.refresh_list()

    def _update_count(self, saved: bool | None = None) -> None:
        words = len(self.editor.toPlainText().split())
        state = {True: "  ·  gespeichert ✓", False: "  ·  speichert …", None: ""}[saved]
        self.count_label.setText(f"{words:,} Wörter".replace(",", "'") + state)

    # ------------------------------------------------------------- Connor
    def _set_busy(self, busy: bool) -> None:
        for button in (self.continue_button, self.ideas_button, self.feedback_button):
            button.setEnabled(not busy)
        self.stop_button.setVisible(busy)
        for button in (self.accept_button, self.remember_button, self.retry_button):
            button.setEnabled(not busy)
        if busy:
            self._render_timer.start()
        else:
            self._render_timer.stop()

    def run_action(self, action: str) -> None:
        if self._active is not None or not action:
            return
        self.save_current()
        text = self.editor.toPlainText()
        if action in ("ideas", "feedback") and not text.strip():
            QMessageBox.information(self, "Noch leer", "Schreib zuerst ein paar Sätze – dann kann Connor dir helfen.")
            return
        title = self.title_edit.text().strip()
        project = self.project()
        hint = self.hint_edit.text()
        length = self.length_box.currentData()
        max_chars = max(3000, (self.settings.context_size - 3000) * 3 - 6000)
        engine = self.engine
        query = f"{title} {project} {hint} {text[-1500:]}"

        def job(worker: Worker) -> Reply:
            prompt = build_writing_prompt(action=action, title=title, text=text, hint=hint, length=length,
                                          max_chars=max_chars, context=engine.writing_context(project, query))
            return engine.generate(engine.writing_system_prompt(), prompt, on_chunk=worker.emit_chunk,
                                   token=worker.token)

        worker = Worker(job)
        worker.chunk.connect(self._on_chunk)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        self._workers.add(worker)
        self._active = worker
        self._action = action
        self._suggestion = ""
        self.panel_title.setText(ACTION_TITLES.get(action, "✨ Connors Vorschlag"))
        self.panel_status.setText("Connor denkt nach …")
        self.suggestion_view.clear()
        self.accept_button.setVisible(action == "continue")
        self.remember_button.setVisible(action == "ideas")
        self.panel.show()
        self._set_busy(True)
        worker.start()

    def _render_suggestion(self) -> None:
        text = strip_think(self._suggestion).strip()
        if self._action == "continue":
            if self.suggestion_view.toPlainText() != text:
                self.suggestion_view.setPlainText(text)
                self.suggestion_view.moveCursor(QTextCursor.MoveOperation.End)
        else:
            self.suggestion_view.setMarkdown(text)

    def _on_chunk(self, worker: Worker, text: str) -> None:
        if worker is self._active:
            if not self._suggestion:
                self.panel_status.setText("Connor schreibt …")
            self._suggestion += text

    def _on_finished(self, worker: Worker, reply: Reply | None) -> None:
        self._workers.discard(worker)
        if worker is not self._active:
            return
        self._active = None
        if reply is not None:
            self._suggestion = reply.text
        self._render_suggestion()
        self._set_busy(False)
        self.panel_status.setText("" if self._suggestion.strip() else "Keine Antwort erhalten – versuch es nochmal.")

    def _on_failed(self, worker: Worker, message: str) -> None:
        self._workers.discard(worker)
        if worker is not self._active:
            return
        self._active = None
        self._set_busy(False)
        self.panel_status.setText("")
        self.suggestion_view.setPlainText(f"⚠️ {message}")
        self.accept_button.hide()
        self.remember_button.hide()

    def stop(self) -> None:
        if self._active is None:
            return
        self._active.stop()
        self._active = None
        self._set_busy(False)
        self._render_suggestion()
        self.panel_status.setText("Abgebrochen")

    def accept_suggestion(self) -> None:
        text = strip_think(self._suggestion).strip()
        if not text:
            return
        story = self.editor.toPlainText()
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(continuation_separator(story) + text)
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self.hide_suggestion()
        self.save_current()

    def remember_suggestion(self) -> None:
        text = strip_think(self._suggestion).strip()
        if not text:
            return
        title = self.title_edit.text().strip() or "Ohne Titel"
        memory_id, _created = self.db.save_memory(f"Ideen zu «{title}»", text, "idee", self.project(), "offen")
        self.panel_status.setText(f"🧠 Gemerkt als Eintrag #{memory_id}")
        self.data_changed.emit({"memory", "projects"})

    def hide_suggestion(self) -> None:
        if self._active is not None:
            return
        self._suggestion = ""
        self.suggestion_view.clear()
        self.panel.hide()

    def shutdown(self) -> None:
        self.save_current()
        self.mic_button.cancel()
        for worker in [*self._workers, self._import_worker]:
            if worker is not None:
                worker.stop()

    # ---------------------------------------------------- dictation & files
    def _on_dictated(self, text: str) -> None:
        cursor = self.editor.textCursor()
        before = self.editor.toPlainText()[: cursor.position()]
        if before and not before.endswith((" ", "\n")):
            text = " " + text
        cursor.insertText(text)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def handle_drop(self, paths: list[str]) -> None:
        if self._import_worker is not None:
            QMessageBox.information(self, "Moment", "Ich lese gerade noch andere Dateien – gleich fertig.")
            return
        files, _skipped = collect_files(paths)
        if not files:
            QMessageBox.information(self, "Nichts gefunden", "Diese Dateien kann ich nicht lesen. Unterstützt sind "
                                    "z. B. Markdown, Text, Word, PDF, E-Books und Audio.")
            return
        project = self.project()
        if len(files) == 1:
            box = QMessageBox(self)
            box.setWindowTitle("Datei verwenden")
            box.setText(f"Was soll ich mit «{files[0][1]}» machen?")
            box.setInformativeText("Als Geschichte öffnen: Der Text landet im Editor, du kannst weiterschreiben.\n"
                                   "Als Wissen: Connor kennt den Inhalt beim Schreiben (z. B. Weltenbau-Notizen).")
            story_button = box.addButton("📖  Als Geschichte öffnen", QMessageBox.ButtonRole.AcceptRole)
            knowledge_button = box.addButton("📚  Als Wissen für Connor", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is story_button:
                self._open_as_story(files[0][0])
            elif box.clickedButton() is knowledge_button:
                self._import_knowledge(paths, project)
            return
        where = f"im Projekt «{project}»" if project else "ohne Projekt"
        answer = QMessageBox.question(self, "Als Wissen speichern?",
                                      f"{len(files)} Dateien als Wissen für Connor speichern ({where})?\n\n"
                                      "Connor nutzt sie dann beim Schreiben, z. B. Weltenbau- oder Charakter-Notizen.")
        if answer == QMessageBox.StandardButton.Yes:
            self._import_knowledge(paths, project)

    def _run_import(self, job, on_done) -> None:
        worker = Worker(job)
        worker.event.connect(lambda _w, text: self.count_label.setText(text))
        worker.finished.connect(lambda w, result: self._import_finished(w, result, on_done))
        worker.failed.connect(lambda w, message: self._import_finished(w, None, None, message))
        self._import_worker = worker
        self.count_label.setText("📥 Lese …")
        worker.start()

    def _import_finished(self, worker: Worker, result, on_done, error: str = "") -> None:
        if worker is not self._import_worker:
            return
        self._import_worker = None
        self._update_count()
        if error:
            QMessageBox.warning(self, "Hat nicht geklappt", error)
        elif on_done is not None and result is not None:
            on_done(result)

    def _open_as_story(self, path: Path) -> None:
        transcriber = self.transcriber()

        def job(worker: Worker) -> str:
            return normalize(read_document(path, transcribe=transcriber.file_transcriber(), progress=worker.emit_event))

        def done(text: str) -> None:
            self.save_current()
            story_id = self.db.create_story(path.stem, self.project(), text)
            self.load_story(story_id)

        self._run_import(job, done)

    def _import_knowledge(self, paths: list[str], project: str) -> None:
        db, transcriber = self.db, self.transcriber()

        def job(worker: Worker) -> ImportResult:
            return import_paths(db, paths, project=project, transcribe=transcriber.file_transcriber(),
                                progress=worker.emit_event, should_stop=lambda: worker.cancelled)

        def done(result: ImportResult) -> None:
            QMessageBox.information(self, "Dateien gelesen", result.summary() +
                                    "\n\nDu findest sie im Tab «📚 Dokumente».")
            self.data_changed.emit({"documents", "projects"})

        self._run_import(job, done)
