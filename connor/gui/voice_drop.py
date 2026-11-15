"""🎤 button for dictation and the window-wide drop zone for files."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from .. import voice
from .common import Worker


class MicButton(QPushButton):
    """Click, speak, click again – the text arrives via ``transcribed``."""

    transcribed = Signal(str)
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, transcriber: Callable[[], voice.Transcriber], idle_text: str = "🎤",
                 parent: QWidget | None = None):
        super().__init__(idle_text, parent)
        self._idle_text = idle_text
        self._get_transcriber = transcriber
        self._recorder: voice.Recorder | None = None
        self._worker: Worker | None = None
        self._state = "idle"
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._tick)
        self.setToolTip("Diktieren: klicken, sprechen, nochmals klicken.\nDie Spracherkennung läuft lokal auf deinem PC.")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self.toggle)

    @property
    def busy(self) -> bool:
        return self._state != "idle"

    def toggle(self) -> None:
        if self._state == "idle":
            self.start()
        elif self._state == "recording":
            self.finish()

    def start(self) -> None:
        if voice.missing_packages():
            self.failed.emit(voice.INSTALL_HINT)
            return
        recorder = voice.Recorder()
        try:
            recorder.start()
        except voice.VoiceError as exc:
            self.failed.emit(str(exc))
            return
        self._recorder = recorder
        self._set_state("recording")
        self.status.emit("🎤 Ich höre zu … klick nochmals auf ⏹, wenn du fertig bist.")

    def finish(self) -> None:
        recorder, self._recorder = self._recorder, None
        if recorder is None:
            return
        audio = recorder.stop()
        transcriber = self._get_transcriber()
        self._set_state("transcribing")

        def job(worker: Worker) -> str:
            if len(audio) < int(0.4 * voice.SAMPLE_RATE):
                raise voice.VoiceError("Die Aufnahme war zu kurz.")
            if float(abs(audio).max()) < 1e-4:
                raise voice.VoiceError(voice.SILENCE_HINT)
            return transcriber.transcribe(audio, should_stop=lambda: worker.cancelled)

        worker = Worker(job)
        worker.finished.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def cancel(self) -> None:
        if self._recorder is not None:
            self._recorder.cancel()
            self._recorder = None
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        self._set_state("idle")

    def _on_done(self, worker: Worker, text) -> None:
        if worker is not self._worker:
            return
        self._worker = None
        self._set_state("idle")
        text = (text or "").strip()
        if text:
            self.transcribed.emit(text)
        else:
            self.failed.emit("Ich habe nichts verstanden – sprich etwas lauter oder näher ans Mikrofon.")

    def _on_failed(self, worker: Worker, message: str) -> None:
        if worker is not self._worker:
            return
        self._worker = None
        self._set_state("idle")
        self.failed.emit(message)

    def _set_state(self, state: str) -> None:
        self._state = state
        self.setProperty("recording", state == "recording")
        self.style().unpolish(self)
        self.style().polish(self)
        if state == "idle":
            self._timer.stop()
            self.setText(self._idle_text)
            self.status.emit("")
        else:
            self._timer.start()
            self._tick()

    def _tick(self) -> None:
        if self._state == "recording" and self._recorder is not None:
            seconds = int(self._recorder.seconds)
            level = "▁▂▃▄▅▆▇█"[min(7, int(self._recorder.level * 60))]
            self.setText(f"⏹ {seconds // 60}:{seconds % 60:02d} {level}")
            if seconds >= voice.MAX_SECONDS:
                self.finish()
        elif self._state == "transcribing":
            self.setText("⏳")
            transcriber = self._get_transcriber()
            if transcriber.is_ready():
                self.status.emit("✍️ Schreibe auf, was du gesagt hast …")
            else:
                total = voice.MODEL_SIZES_MB.get(transcriber.model_name, 500)
                self.status.emit(f"⏳ Lade das Sprachmodell (einmalig ca. {total} MB) … {transcriber.downloaded_mb():.0f} MB")


def local_paths(event: QDragEnterEvent | QDropEvent) -> list[str]:
    mime = event.mimeData()
    if not mime.hasUrls():
        return []
    return [url.toLocalFile() for url in mime.urls() if url.isLocalFile() and url.toLocalFile()]


class DropOverlay(QLabel):
    """Covers the window while files are dragged over it."""

    dropped = Signal(list)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("dropOverlay")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setAcceptDrops(True)
        self.hide()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if local_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.hide()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = local_paths(event)
        self.hide()
        event.acceptProposedAction()
        if paths:
            self.dropped.emit(paths)


class DropWatcher(QObject):
    """Notices files being dragged over any part of the window and shows the overlay."""

    def __init__(self, window: QWidget, show_overlay: Callable[[], None]):
        super().__init__(window)
        self._window = window
        self._show_overlay = show_overlay

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.DragEnter and isinstance(obj, QWidget) and obj.window() is self._window \
                and not isinstance(obj, DropOverlay) and local_paths(event):
            self._show_overlay()
            event.acceptProposedAction()
            return True
        return False
