"""Where Connor keeps its files. Everything lives in the local data folder."""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    custom = os.environ.get("CONNOR_DATA_DIR")
    path = Path(custom) if custom else APP_DIR / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "connor.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def log_path() -> Path:
    return data_dir() / "connor.log"


def export_dir() -> Path:
    path = data_dir() / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    """Downloaded speech models. Not part of your data backup – they can be downloaded again."""
    custom = os.environ.get("CONNOR_MODELS_DIR")
    path = Path(custom) if custom else APP_DIR / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path
