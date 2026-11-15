"""Shared GUI helpers: labels, date formatting, list delegate and background workers."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QModelIndex, QObject, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..llm import CancelToken, OllamaError
from . import theme

log = logging.getLogger(__name__)

CATEGORY_LABELS = {
    "charakter": "🧙 Charakter",
    "welt": "🌍 Welt",
    "ort": "📍 Ort",
    "plot": "🌀 Plot",
    "idee": "💡 Idee",
    "story": "📖 Story",
    "vorliebe": "❤️ Vorliebe",
    "notiz": "📝 Notiz",
}
STATUS_LABELS = {
    "kanon": "✅ Kanon",
    "entwurf": "✏️ Entwurf",
    "offen": "❓ Offen",
    "verworfen": "🗑️ Verworfen",
}
REPEAT_LABELS = {"none": "Einmalig", "daily": "Täglich", "weekly": "Wöchentlich"}

SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 1


def pretty_stamp(value: str, now: datetime | None = None) -> str:
    """'2026-11-15 19:45:00' -> '19:45' (today), 'gestern 19:45' or '12.11. 19:45'."""
    try:
        stamp = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return value or ""
    now = now or datetime.now()
    days = (now.date() - stamp.date()).days
    if days == 0:
        return stamp.strftime("%H:%M")
    if days == 1:
        return stamp.strftime("gestern %H:%M")
    if stamp.year == now.year:
        return stamp.strftime("%d.%m. %H:%M")
    return stamp.strftime("%d.%m.%Y")


def now_stamp() -> str:
    return datetime.now().strftime("%H:%M")


class TwoLineDelegate(QStyledItemDelegate):
    """List entry with a bold title and a small grey subtitle."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT_SOFT if selected else theme.SURFACE_2))
            painter.drawRoundedRect(rect, 8, 8)

        inner = rect.adjusted(10, 6, -10, -6)
        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.DemiBold)
        title_metrics = QFontMetrics(title_font)
        painter.setFont(title_font)
        painter.setPen(QColor(theme.TEXT))
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.drawText(QRect(inner.left(), inner.top(), inner.width(), title_metrics.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, inner.width()))

        small_font = QFont(option.font)
        small_font.setPointSizeF(max(7.0, option.font.pointSizeF() * 0.85))
        small_metrics = QFontMetrics(small_font)
        painter.setFont(small_font)
        painter.setPen(QColor(theme.MUTED))
        subtitle = str(index.data(SUBTITLE_ROLE) or "")
        top = inner.top() + title_metrics.height() + 2
        painter.drawText(QRect(inner.left(), top, inner.width(), small_metrics.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         small_metrics.elidedText(subtitle, Qt.TextElideMode.ElideRight, inner.width()))
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        title_height = QFontMetrics(option.font).height()
        return QSize(200, int(title_height * 2 + 18))


class Worker(QObject):
    """Runs a job in a daemon thread. Every signal carries the worker itself as first argument,
    so the UI can ignore results of workers it no longer cares about."""

    chunk = Signal(object, str)
    event = Signal(object, str)
    finished = Signal(object, object)
    failed = Signal(object, str)
    confirm_request = Signal(object, str)

    def __init__(self, job: Callable[["Worker"], object]):
        super().__init__()
        self.job = job
        self.token = CancelToken()
        self.context: dict = {}
        self._answer = False
        self._answered = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="connor-worker")

    @property
    def cancelled(self) -> bool:
        return self.token.cancelled

    def start(self) -> None:
        self._thread.start()

    def is_running(self) -> bool:
        return self._thread.is_alive()

    def stop(self) -> None:
        self.token.cancel()
        self._answer = False
        self._answered.set()

    def emit_chunk(self, text: str) -> None:
        self.chunk.emit(self, text)

    def emit_event(self, text: str) -> None:
        self.event.emit(self, text)

    def ask(self, question: str) -> bool:
        """Called from the worker thread: waits until the UI answered a yes/no question."""
        if self.token.cancelled:
            return False
        self._answered.clear()
        self.confirm_request.emit(self, question)
        self._answered.wait()
        return self._answer and not self.token.cancelled

    def answer(self, value: bool) -> None:
        self._answer = bool(value)
        self._answered.set()

    def _run(self) -> None:
        try:
            result = self.job(self)
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            if self.token.cancelled:
                self.finished.emit(self, None)
                return
            if isinstance(exc, OllamaError):
                log.warning("%s", exc)
            else:
                log.exception("Fehler in einer Hintergrundaufgabe")
            self.failed.emit(self, str(exc) or exc.__class__.__name__)
            return
        self.finished.emit(self, result)
