"""Your files: everything Connor has read – notes, Word, PDF, e-books, transcripts."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..database import Database
from ..documents import KIND_LABELS, ImportResult, import_paths
from ..voice import Transcriber
from .chat_tab import file_filter
from .common import Worker, pretty_stamp

ALL = "__all__"
GENERAL = "__general__"


class DocumentViewer(QDialog):
    def __init__(self, parent: QWidget, doc: dict):
        super().__init__(parent)
        self.setWindowTitle(doc["name"])
        self.resize(760, 640)
        layout = QVBoxLayout(self)
        info = QLabel(f"{KIND_LABELS.get(doc['kind'], doc['kind'])}  ·  ~{doc['chars'] // 6:,} Wörter  ·  "
                      f"{doc['path'] or 'ohne Pfad'}".replace(",", "'"))
        info.setObjectName("small")
        info.setWordWrap(True)
        layout.addWidget(info)
        text = QPlainTextEdit(doc["content"][:400_000])
        text.setReadOnly(True)
        font = QFont("Georgia")
        font.setPointSizeF(11.5)
        text.setFont(font)
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schliessen")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class DocumentsTab(QWidget):
    changed = Signal()

    COLUMNS = ["Datei", "Typ", "Projekt", "Wörter", "Hinzugefügt"]

    def __init__(self, db: Database, transcriber: Callable[[], Transcriber], parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.transcriber = transcriber
        self._worker: Worker | None = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.refresh)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 12)
        layout.setSpacing(10)
        intro = QLabel("📚 <b>Deine Dateien</b> – alles, was Connor gelesen hat. Zieh Dateien oder ganze Ordner "
                       "ins Fenster (Markdown, Text, Word, PDF, E-Book, Audio). Connor findet darin die passenden "
                       "Stellen, wenn ihr darüber redet – im Chat und in der Schreibwerkstatt.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔎  Suchen …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        row.addWidget(self.search, 2)
        self.project_filter = QComboBox()
        self.project_filter.setToolTip("Neue Dateien landen im gewählten Projekt.")
        self.project_filter.currentIndexChanged.connect(lambda _index: self.refresh())
        row.addWidget(self.project_filter, 1)
        add_files = QPushButton("＋  Dateien …")
        add_files.setObjectName("primary")
        add_files.clicked.connect(self.choose_files)
        row.addWidget(add_files)
        add_folder = QPushButton("📁  Ordner …")
        add_folder.clicked.connect(self.choose_folder)
        row.addWidget(add_folder)
        view_button = QPushButton("👁  Anzeigen")
        view_button.clicked.connect(self.view_selected)
        row.addWidget(view_button)
        project_button = QPushButton("🏷️  Projekt …")
        project_button.clicked.connect(self.set_project_selected)
        row.addWidget(project_button)
        delete_button = QPushButton("🗑️  Entfernen")
        delete_button.clicked.connect(self.delete_selected)
        row.addWidget(delete_button)
        layout.addLayout(row)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 140), (2, 160), (3, 90), (4, 120)):
            self.table.setColumnWidth(column, width)
        self.table.doubleClicked.connect(lambda _index: self.view_selected())
        layout.addWidget(self.table, 1)

        self.footer = QLabel("")
        self.footer.setObjectName("small")
        self.footer.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.footer)

        self.refresh_projects()
        self.refresh()

    # --------------------------------------------------------------- data
    def _target_project(self) -> str:
        choice = self.project_filter.currentData()
        return "" if choice in (None, ALL, GENERAL) else choice

    def refresh_projects(self) -> None:
        current = self.project_filter.currentData()
        self.project_filter.blockSignals(True)
        self.project_filter.clear()
        self.project_filter.addItem("Alle Projekte", ALL)
        self.project_filter.addItem("Allgemein (ohne Projekt)", GENERAL)
        for project in self.db.list_projects():
            self.project_filter.addItem(f"📚 {project}", project)
        index = self.project_filter.findData(current) if current is not None else 0
        self.project_filter.setCurrentIndex(max(0, index))
        self.project_filter.blockSignals(False)

    def refresh(self) -> None:
        choice = self.project_filter.currentData()
        project = None if choice in (None, ALL) else ("" if choice == GENERAL else choice)
        docs = self.db.list_documents(self.search.text(), project=project)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(docs))
        for row, doc in enumerate(docs):
            words = QTableWidgetItem()
            words.setData(Qt.ItemDataRole.DisplayRole, doc["chars"] // 6)
            cells = [
                QTableWidgetItem(doc["name"]),
                QTableWidgetItem(KIND_LABELS.get(doc["kind"], doc["kind"] or "–")),
                QTableWidgetItem(doc["project"] or "–"),
                words,
                QTableWidgetItem(pretty_stamp(doc["created_at"])),
            ]
            cells[0].setToolTip(doc["path"])
            for column, item in enumerate(cells):
                item.setData(Qt.ItemDataRole.UserRole, doc["id"])
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        if self._worker is None:
            total = len(self.db.list_documents())
            target = self._target_project()
            where = f"  ·  Neue Dateien landen im Projekt «{target}»" if target else ""
            self.footer.setText(f"{len(docs)} von {total} Dateien  ·  🔒 lokal gespeichert{where}")

    def _selected_ids(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) for row in rows if self.table.item(row, 0)]

    # ------------------------------------------------------------ actions
    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Dateien für Connor", "", file_filter())
        if paths:
            self.import_paths(paths)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Ordner für Connor")
        if folder:
            self.import_paths([folder])

    def import_paths(self, paths: list[str]) -> None:
        if self._worker is not None:
            QMessageBox.information(self, "Moment", "Ich lese gerade noch andere Dateien – gleich fertig.")
            return
        db, project, transcriber = self.db, self._target_project(), self.transcriber()

        def job(worker: Worker) -> ImportResult:
            return import_paths(db, paths, project=project, transcribe=transcriber.file_transcriber(),
                                progress=worker.emit_event, should_stop=lambda: worker.cancelled)

        worker = Worker(job)
        worker.event.connect(lambda _w, text: self.footer.setText(text))
        worker.finished.connect(self._on_imported)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        self.footer.setText("📥 Lese Dateien …")
        worker.start()

    def _on_imported(self, worker: Worker, result: ImportResult | None) -> None:
        if worker is not self._worker:
            return
        self._worker = None
        self.refresh_projects()
        self.refresh()
        if result is not None:
            self.footer.setText(result.summary().splitlines()[0])
            if result.failed or not result.imported:
                QMessageBox.information(self, "Dateien gelesen", result.summary())
        self.changed.emit()

    def _on_failed(self, worker: Worker, message: str) -> None:
        if worker is not self._worker:
            return
        self._worker = None
        self.refresh()
        QMessageBox.warning(self, "Hat nicht geklappt", message)

    def view_selected(self) -> None:
        ids = self._selected_ids()
        doc = self.db.get_document(ids[0]) if ids else None
        if doc is not None:
            DocumentViewer(self, doc).exec()

    def set_project_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        projects = ["", *self.db.list_projects()]
        current = (self.db.get_document(ids[0]) or {}).get("project", "")
        project, ok = QInputDialog.getItem(self, "Projekt zuordnen", "Zu welchem Projekt gehören die Dateien?\n"
                                           "(leer = allgemein)", projects, projects.index(current)
                                           if current in projects else 0, True)
        if ok:
            for doc_id in ids:
                self.db.update_document(doc_id, project=project.strip())
            self.refresh_projects()
            self.refresh()
            self.changed.emit()

    def delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        text = "diese Datei" if len(ids) == 1 else f"diese {len(ids)} Dateien"
        answer = QMessageBox.question(self, "Entfernen?", f"Soll Connor {text} vergessen?\n\n"
                                      "Die Originaldateien auf deinem PC bleiben unverändert.")
        if answer == QMessageBox.StandardButton.Yes:
            for doc_id in ids:
                self.db.delete_document(doc_id)
            self.refresh()
            self.changed.emit()

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.stop()
