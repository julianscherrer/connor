"""Reminders: Connor pops up at the right time – as long as he is running (also in the tray)."""
from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, Qt, QTime, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..database import REPEAT_OPTIONS, Database, str_to_dt
from ..timeparse import extract_when, format_dt
from .common import REPEAT_LABELS


class ReminderDialog(QDialog):
    def __init__(self, parent: QWidget, text: str = "", due: datetime | None = None, repeat: str = "none"):
        super().__init__(parent)
        self.setWindowTitle("Erinnerung")
        self.setMinimumWidth(460)
        form = QFormLayout()
        form.setSpacing(10)
        self.text = QLineEdit(text)
        self.text.setPlaceholderText("Woran soll ich dich erinnern?")
        form.addRow("Was", self.text)
        due = due or (datetime.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        self.when = QDateTimeEdit(QDateTime(QDate(due.year, due.month, due.day), QTime(due.hour, due.minute)))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("dd.MM.yyyy  HH:mm")
        form.addRow("Wann", self.when)
        self.repeat = QComboBox()
        for key in REPEAT_OPTIONS:
            self.repeat.addItem(REPEAT_LABELS[key], key)
        self.repeat.setCurrentIndex(REPEAT_OPTIONS.index(repeat) if repeat in REPEAT_OPTIONS else 0)
        form.addRow("Wiederholen", self.repeat)

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
        if not self.text.text().strip():
            QMessageBox.warning(self, "Fast fertig", "Schreib noch, woran ich dich erinnern soll.")
            return
        if self.due() < datetime.now() - timedelta(minutes=1):
            QMessageBox.warning(self, "Zu spät", "Dieser Zeitpunkt liegt in der Vergangenheit.")
            return
        self.accept()

    def due(self) -> datetime:
        value = self.when.dateTime()
        day, clock = value.date(), value.time()
        return datetime(day.year(), day.month(), day.day(), clock.hour(), clock.minute())

    def values(self) -> tuple[str, datetime, str]:
        return self.text.text().strip(), self.due(), self.repeat.currentData()


class RemindersTab(QWidget):
    changed = Signal()

    COLUMNS = ["Wann", "Was", "Wiederholung", "Status"]

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 12)
        layout.setSpacing(10)

        intro = QLabel("⏰ <b>Erinnerungen</b> – tippe einfach, wann und woran, z. B. "
                       "<i>morgen 18:00 Mama anrufen</i> oder <i>in 2 Stunden Pause machen</i>. "
                       "Du kannst Connor im Chat auch sagen: «Erinnere mich …».")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        quick = QHBoxLayout()
        quick.setSpacing(8)
        self.quick_edit = QLineEdit()
        self.quick_edit.setPlaceholderText("z. B. «Freitag 19 Uhr Kapitel 4 fertig schreiben»")
        self.quick_edit.returnPressed.connect(self.quick_add)
        quick.addWidget(self.quick_edit, 1)
        add_button = QPushButton("＋  Hinzufügen")
        add_button.setObjectName("primary")
        add_button.clicked.connect(self.quick_add)
        quick.addWidget(add_button)
        layout.addLayout(quick)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        manual = QPushButton("📅  Mit Datum wählen …")
        manual.clicked.connect(lambda: self.open_dialog())
        tools.addWidget(manual)
        edit_button = QPushButton("✏️  Bearbeiten")
        edit_button.clicked.connect(self.edit_selected)
        tools.addWidget(edit_button)
        done_button = QPushButton("✓  Erledigt")
        done_button.clicked.connect(self.mark_done)
        tools.addWidget(done_button)
        delete_button = QPushButton("🗑️  Löschen")
        delete_button.clicked.connect(self.delete_selected)
        tools.addWidget(delete_button)
        tools.addStretch(1)
        self.show_done = QCheckBox("Erledigte anzeigen")
        self.show_done.toggled.connect(lambda _checked: self.refresh())
        tools.addWidget(self.show_done)
        layout.addLayout(tools)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 120)
        self.table.doubleClicked.connect(lambda _index: self.edit_selected())
        layout.addWidget(self.table, 1)

        footer = QLabel("Erinnerungen erscheinen als Benachrichtigung, solange Connor läuft – auch minimiert "
                        "unten rechts im Tray. Verpasste Erinnerungen zeigt er beim nächsten Start.")
        footer.setObjectName("small")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        self.refresh()

    def refresh(self) -> None:
        reminders = self.db.list_reminders(include_done=self.show_done.isChecked())
        now = datetime.now()
        self.table.setRowCount(len(reminders))
        for row, reminder in enumerate(reminders):
            due = str_to_dt(reminder["due_at"])
            if reminder["done"]:
                status = "✓ erledigt"
            elif due <= now:
                status = "⚠️ fällig"
            else:
                status = "geplant"
            cells = [format_dt(due), reminder["text"], REPEAT_LABELS.get(reminder["repeat"], ""), status]
            for column, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, reminder["id"])
                if reminder["done"]:
                    item.setForeground(Qt.GlobalColor.gray)
                self.table.setItem(row, column, item)

    def _selected_ids(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) for row in rows if self.table.item(row, 0)]

    def quick_add(self) -> None:
        text = self.quick_edit.text().strip()
        if not text:
            return
        due, what = extract_when(text)
        if due is None or not what:
            self.open_dialog(text=what or text, due=due)
            return
        if due < datetime.now() - timedelta(minutes=1):
            QMessageBox.warning(self, "Zu spät", f"{format_dt(due)} liegt in der Vergangenheit.")
            return
        self.db.add_reminder(what, due)
        self.quick_edit.clear()
        self._after_change()

    def open_dialog(self, text: str = "", due: datetime | None = None) -> None:
        dialog = ReminderDialog(self, text=text, due=due)
        if dialog.exec():
            what, when, repeat = dialog.values()
            self.db.add_reminder(what, when, repeat)
            self.quick_edit.clear()
            self._after_change()

    def edit_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        reminder = self.db.get_reminder(ids[0])
        if reminder is None:
            return
        dialog = ReminderDialog(self, reminder["text"], str_to_dt(reminder["due_at"]), reminder["repeat"])
        if dialog.exec():
            what, when, repeat = dialog.values()
            self.db.update_reminder(reminder["id"], text=what, due_at=when, repeat=repeat, done=False)
            self._after_change()

    def mark_done(self) -> None:
        for reminder_id in self._selected_ids():
            self.db.update_reminder(reminder_id, done=True)
        self._after_change()

    def delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        answer = QMessageBox.question(self, "Löschen?", f"{len(ids)} Erinnerung(en) wirklich löschen?")
        if answer == QMessageBox.StandardButton.Yes:
            for reminder_id in ids:
                self.db.delete_reminder(reminder_id)
            self._after_change()

    def _after_change(self) -> None:
        self.refresh()
        self.changed.emit()
