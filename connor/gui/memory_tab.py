"""Connor's memory: everything he knows about your stories, characters, worlds and you."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..database import MEMORY_CATEGORIES, MEMORY_STATUSES, Database
from .common import CATEGORY_LABELS, STATUS_LABELS, pretty_stamp

ALL = "__all__"
GENERAL = "__general__"


class MemoryDialog(QDialog):
    def __init__(self, parent: QWidget, projects: list[str], memory: dict | None = None,
                 default_project: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Erinnerung bearbeiten" if memory else "Neue Erinnerung")
        self.setMinimumWidth(520)
        form = QFormLayout()
        form.setSpacing(10)

        self.title = QLineEdit(memory["title"] if memory else "")
        self.title.setPlaceholderText("z. B. der Name eines Charakters")
        form.addRow("Titel", self.title)

        self.category = QComboBox()
        for key in MEMORY_CATEGORIES:
            self.category.addItem(CATEGORY_LABELS[key], key)
        self.category.setCurrentIndex(MEMORY_CATEGORIES.index(memory["category"]) if memory and
                                      memory["category"] in MEMORY_CATEGORIES else MEMORY_CATEGORIES.index("notiz"))
        form.addRow("Kategorie", self.category)

        self.project = QComboBox()
        self.project.setEditable(True)
        self.project.addItem("")
        self.project.addItems(projects)
        self.project.lineEdit().setPlaceholderText("allgemein (kein Projekt)")
        self.project.setCurrentText(memory["project"] if memory else default_project)
        form.addRow("Projekt", self.project)

        self.status = QComboBox()
        for key in MEMORY_STATUSES:
            self.status.addItem(STATUS_LABELS[key], key)
        self.status.setCurrentIndex(MEMORY_STATUSES.index(memory["status"]) if memory and
                                    memory["status"] in MEMORY_STATUSES else MEMORY_STATUSES.index("entwurf"))
        form.addRow("Status", self.status)

        self.content = QPlainTextEdit(memory["content"] if memory else "")
        self.content.setPlaceholderText("Was soll Connor sich merken?")
        self.content.setMinimumHeight(160)
        form.addRow("Inhalt", self.content)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Speichern")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.title.text().strip() or not self.content.toPlainText().strip():
            QMessageBox.warning(self, "Fast fertig", "Titel und Inhalt dürfen nicht leer sein.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "title": self.title.text().strip(),
            "content": self.content.toPlainText().strip(),
            "category": self.category.currentData(),
            "project": self.project.currentText().strip(),
            "status": self.status.currentData(),
        }


class MemoryTab(QWidget):
    changed = Signal()

    COLUMNS = ["Titel", "Kategorie", "Projekt", "Status", "Inhalt", "Geändert"]

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.refresh)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 12)
        layout.setSpacing(10)

        intro = QLabel("🧠 <b>Connors Gedächtnis</b> – alles, was er sich über deine Geschichten und dich merkt. "
                       "Connor holt passende Einträge automatisch hervor, wenn ihr darüber redet.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔎  Suchen …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        filters.addWidget(self.search, 2)
        self.project_filter = QComboBox()
        self.project_filter.currentIndexChanged.connect(lambda _index: self.refresh())
        filters.addWidget(self.project_filter, 1)
        self.category_filter = QComboBox()
        self.category_filter.addItem("Alle Kategorien", ALL)
        for key in MEMORY_CATEGORIES:
            self.category_filter.addItem(CATEGORY_LABELS[key], key)
        self.category_filter.currentIndexChanged.connect(lambda _index: self.refresh())
        filters.addWidget(self.category_filter, 1)
        new_button = QPushButton("＋  Neu")
        new_button.setObjectName("primary")
        new_button.clicked.connect(self.add)
        filters.addWidget(new_button)
        edit_button = QPushButton("✏️  Bearbeiten")
        edit_button.clicked.connect(self.edit_selected)
        filters.addWidget(edit_button)
        delete_button = QPushButton("🗑️  Löschen")
        delete_button.clicked.connect(self.delete_selected)
        filters.addWidget(delete_button)
        layout.addLayout(filters)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 180), (1, 130), (2, 140), (3, 110), (5, 110)):
            self.table.setColumnWidth(column, width)
        self.table.doubleClicked.connect(lambda _index: self.edit_selected())
        layout.addWidget(self.table, 1)

        self.footer = QLabel("")
        self.footer.setObjectName("small")
        self.footer.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.footer)

        self.refresh_projects()
        self.refresh()

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
        project_choice = self.project_filter.currentData()
        project = None if project_choice in (None, ALL) else ("" if project_choice == GENERAL else project_choice)
        category = self.category_filter.currentData()
        memories = self.db.list_memories(project=project, category=None if category == ALL else category,
                                         search=self.search.text())
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(memories))
        for row, memory in enumerate(memories):
            content = " / ".join(line.strip() for line in memory["content"].splitlines() if line.strip())
            cells = [
                memory["title"],
                CATEGORY_LABELS.get(memory["category"], memory["category"]),
                memory["project"] or "–",
                STATUS_LABELS.get(memory["status"], memory["status"]),
                content,
                pretty_stamp(memory["updated_at"]),
            ]
            for column, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, memory["id"])
                if column == 4:
                    item.setToolTip(memory["content"])
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        total = len(self.db.list_memories())
        self.footer.setText(f"{len(memories)} von {total} Einträgen  ·  🔒 lokal gespeichert in {self.db.path}")

    def _selected_ids(self) -> list[int]:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        ids = []
        for row in sorted(rows):
            item = self.table.item(row, 0)
            if item is not None:
                ids.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return ids

    def add(self) -> None:
        choice = self.project_filter.currentData()
        default_project = "" if choice in (None, ALL, GENERAL) else choice
        dialog = MemoryDialog(self, self.db.list_projects(), default_project=default_project)
        if dialog.exec():
            values = dialog.values()
            self.db.add_memory(values["title"], values["content"], values["category"], values["project"],
                               values["status"])
            self._after_change()

    def edit_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        memory = self.db.get_memory(ids[0])
        if memory is None:
            return
        dialog = MemoryDialog(self, self.db.list_projects(), memory)
        if dialog.exec():
            self.db.update_memory(memory["id"], **dialog.values())
            self._after_change()

    def delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        text = "diesen Eintrag" if len(ids) == 1 else f"diese {len(ids)} Einträge"
        answer = QMessageBox.question(self, "Vergessen?", f"Soll Connor {text} wirklich vergessen?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        for memory_id in ids:
            self.db.delete_memory(memory_id)
        self._after_change()

    def _after_change(self) -> None:
        self.refresh_projects()
        self.refresh()
        self.changed.emit()
