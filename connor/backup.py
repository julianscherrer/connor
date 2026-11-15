"""Automatic backups: every day a copy of your data (chats, memory, stories, files) – it all lives only here."""
from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .paths import data_dir, db_path, settings_path

log = logging.getLogger(__name__)

PREFIX = "connor-"


def default_folder() -> Path:
    return data_dir() / "backups"


def backup_folder(custom: str = "") -> Path:
    folder = Path(custom).expanduser() if custom.strip() else default_folder()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def backup_due(folder: Path, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return not (folder / f"{PREFIX}{now:%Y-%m-%d}.db").exists()


def make_backup(folder: Path, *, source: Path | None = None, keep: int = 14, now: datetime | None = None) -> Path:
    """Copies the database safely (also while Connor is running) and keeps the newest ``keep`` backups."""
    now = now or datetime.now()
    source = source or db_path()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{PREFIX}{now:%Y-%m-%d}.db"
    temporary = target.with_suffix(".tmp")
    origin = sqlite3.connect(source)
    try:
        copy = sqlite3.connect(temporary)
        try:
            origin.backup(copy)
        finally:
            copy.close()
    finally:
        origin.close()
    temporary.replace(target)
    settings = settings_path()
    if settings.exists():
        shutil.copy2(settings, folder / f"{PREFIX}{now:%Y-%m-%d}-settings.json")
    for old in sorted(folder.glob(f"{PREFIX}????-??-??.db"))[:-max(1, keep)]:
        try:
            old.unlink(missing_ok=True)
            (old.parent / f"{old.stem}-settings.json").unlink(missing_ok=True)
        except OSError as exc:
            log.info("Altes Backup %s konnte nicht gelöscht werden: %s", old.name, exc)
    log.info("Backup erstellt: %s", target)
    return target


def latest_backup(folder: Path) -> Path | None:
    backups = sorted(folder.glob(f"{PREFIX}????-??-??.db"))
    return backups[-1] if backups else None
