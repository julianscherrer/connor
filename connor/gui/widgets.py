"""Chat building blocks: message bubbles, the scrolling chat view and the input box."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..llm import strip_think, strip_tool_markup


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str = "", stamp: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self.raw = ""
        self.setObjectName("bubbleUser" if role == "user" else "bubbleAssistant")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 12)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(8)
        name = QLabel("Du" if role == "user" else "Connor")
        name.setObjectName("bubbleName")
        header.addWidget(name)
        self.stamp = QLabel(stamp)
        self.stamp.setObjectName("small")
        header.addWidget(self.stamp)
        header.addStretch(1)
        self.copy_button = QPushButton("Kopieren")
        self.copy_button.setObjectName("flat")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self.copy)
        header.addWidget(self.copy_button)
        layout.addLayout(header)

        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.MarkdownText if role == "assistant" else Qt.TextFormat.PlainText)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.body.setOpenExternalLinks(True)
        self.body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(self.body)

        self._copy_reset = QTimer(self)
        self._copy_reset.setSingleShot(True)
        self._copy_reset.timeout.connect(lambda: self.copy_button.setText("Kopieren"))
        self.set_text(text)

    def visible_text(self) -> str:
        if self.role != "assistant":
            return self.raw.strip()
        return strip_tool_markup(strip_think(self.raw)).strip()

    def set_text(self, text: str) -> None:
        self.raw = text
        shown = self.visible_text()
        self.body.setText(shown if shown else "…")

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.visible_text())
        self.copy_button.setText("Kopiert ✓")
        self._copy_reset.start(1500)


class ChatView(QScrollArea):
    """Scrollable list of bubbles. Sticks to the bottom while Connor is typing,
    unless you scrolled up to read something."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("chatScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.container = QWidget()
        self.container.setObjectName("chatContainer")
        self.column = QVBoxLayout(self.container)
        self.column.setContentsMargins(24, 16, 24, 16)
        self.column.setSpacing(12)
        self.column.addStretch(1)
        self.setWidget(self.container)

        self._user_bubbles: list[MessageBubble] = []
        self._welcome: QLabel | None = None
        self._stick = True
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll)
        bar.rangeChanged.connect(self._on_range)

    def _on_scroll(self, value: int) -> None:
        self._stick = value >= self.verticalScrollBar().maximum() - 40

    def _on_range(self, _minimum: int, maximum: int) -> None:
        if self._stick:
            self.verticalScrollBar().setValue(maximum)

    def _insert(self, widget: QWidget) -> None:
        self.column.insertWidget(self.column.count() - 1, widget)

    def _bubble_width(self) -> int:
        return max(260, int(self.viewport().width() * 0.78))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        for bubble in self._user_bubbles:
            self._fit_user_bubble(bubble)

    def _fit_user_bubble(self, bubble: MessageBubble) -> None:
        """Short messages get a snug bubble, long ones wrap at ~78 % of the width."""
        max_width = self._bubble_width()
        bubble.setMaximumWidth(max_width)
        metrics = bubble.body.fontMetrics()
        longest = max((metrics.horizontalAdvance(line) for line in bubble.raw.splitlines()), default=0)
        inner = max_width - 32
        bubble.body.setMinimumWidth(max(60, min(longest + 6, inner)))

    def scroll_to_bottom(self) -> None:
        self._stick = True
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

    def clear(self) -> None:
        while self.column.count() > 1:
            item = self.column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._user_bubbles.clear()
        self._welcome = None
        self._stick = True

    def remove_bubble(self, bubble: MessageBubble) -> None:
        row = bubble.parentWidget()
        if row is None:
            return
        self.column.removeWidget(row)
        row.hide()
        row.deleteLater()
        if bubble in self._user_bubbles:
            self._user_bubbles.remove(bubble)

    def show_welcome(self, markdown: str) -> None:
        self.clear()
        label = QLabel()
        label.setObjectName("welcome")
        label.setTextFormat(Qt.TextFormat.MarkdownText)
        label.setText(markdown)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        label.setMaximumWidth(640)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 30, 0, 0)
        layout.addStretch(1)
        layout.addWidget(label, 3)
        layout.addStretch(1)
        self._welcome = label
        self._insert(row)

    def _remove_welcome(self) -> None:
        if self._welcome is not None:
            self.clear()

    def has_welcome(self) -> bool:
        return self._welcome is not None

    def add_message(self, role: str, text: str, stamp: str = "") -> MessageBubble:
        self._remove_welcome()
        bubble = MessageBubble(role, text, stamp)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        if role == "user":
            layout.addStretch(1)
            layout.addWidget(bubble)
            self._user_bubbles.append(bubble)
            self._fit_user_bubble(bubble)
        else:
            layout.addWidget(bubble, 1)
            layout.addSpacing(48)
        self._insert(row)
        return bubble

    def add_event(self, text: str, error: bool = False) -> QLabel:
        self._remove_welcome()
        label = QLabel(text)
        label.setObjectName("errorLabel" if error else "eventLabel")
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter if not error else Qt.AlignmentFlag.AlignLeft)
        self._insert(label)
        return label


class ChatInput(QPlainTextEdit):
    """Enter sends, Shift+Enter makes a new line. Grows with the text up to 8 lines."""

    submitted = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText("Schreib Connor etwas …   (Enter = senden, Shift+Enter = neue Zeile)")
        self.setTabChangesFocus(True)
        self.document().contentsChanged.connect(self._fit_height)
        self._fit_height()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def _fit_height(self) -> None:
        lines = max(1, min(8, int(self.document().size().height())))
        height = lines * self.fontMetrics().lineSpacing() + 26
        self.setFixedHeight(max(44, height))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_height()
