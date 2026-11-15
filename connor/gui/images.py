"""Images for the chat: prepared once (scaled down, saved locally) so Connor can look at them."""
from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from ..paths import data_dir

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MAX_SIDE = 1600


def images_dir() -> Path:
    return data_dir() / "images"


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def remove_images(paths: list[str]) -> None:
    """Deletes Connor's own copies (never files outside the images folder)."""
    folder = images_dir().resolve()
    for path in paths:
        target = Path(path).resolve()
        if target.parent == folder:
            target.unlink(missing_ok=True)


def prepare_image(source: str | Path) -> Path:
    """Copies an image into Connor's data folder – scaled to at most 1600 px, as JPEG.
    Big scans would otherwise be slow for the local model."""
    image = QImage(str(source))
    if image.isNull():
        raise ValueError(f"«{Path(source).name}» kann ich nicht als Bild öffnen.")
    if max(image.width(), image.height()) > MAX_SIDE:
        image = image.scaled(MAX_SIDE, MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    if image.hasAlphaChannel():
        flat = QImage(image.size(), QImage.Format.Format_RGB32)
        flat.fill(Qt.GlobalColor.white)
        painter = QPainter(flat)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flat
    folder = images_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{uuid.uuid4().hex}.jpg"
    if not image.save(str(target), "JPG", 90):
        raise OSError(f"Das Bild konnte nicht gespeichert werden: {target}")
    return target
