"""User settings, stored as JSON in the local data folder."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .paths import settings_path

log = logging.getLogger(__name__)


@dataclass
class Settings:
    ollama_url: str = "http://localhost:11434"
    model: str = ""
    temperature: float = 0.8
    context_size: int = 8192
    thinking: str = "off"  # auto | off | on
    use_tools: bool = True
    user_name: str = ""
    extra_instructions: str = ""
    web_enabled: bool = True
    web_confirm: bool = False
    search_provider: str = "duckduckgo"  # duckduckgo | searxng
    searxng_url: str = "http://localhost:8888"
    minimize_to_tray: bool = True
    tray_hint_shown: bool = False
    window_geometry: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("Einstellungen konnten nicht gelesen werden: %s", exc)
            return cls()
        if not isinstance(data, dict):
            return cls()

        defaults = cls()
        values = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            default = getattr(defaults, f.name)
            if isinstance(default, bool):
                if isinstance(value, bool):
                    values[f.name] = value
            elif isinstance(default, int):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[f.name] = int(value)
            elif isinstance(default, float):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[f.name] = float(value)
            elif isinstance(value, str):
                values[f.name] = value
        return cls(**values)

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def think_value(self) -> bool | None:
        return {"off": False, "on": True}.get(self.thinking)
