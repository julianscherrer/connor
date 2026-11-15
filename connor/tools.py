"""Tools Connor can use on his own: memory, reminders and the web."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Callable

from . import websearch
from .database import MEMORY_CATEGORIES, MEMORY_STATUSES, Database, str_to_dt
from .prompts import format_memory
from .settings import Settings
from .timeparse import extract_when, format_dt

log = logging.getLogger(__name__)

REPEAT_ALIASES = {
    "": "none", "none": "none", "no": "none", "once": "none", "keine": "none", "nein": "none", "einmalig": "none",
    "daily": "daily", "day": "daily", "täglich": "daily", "taeglich": "daily", "jeden tag": "daily",
    "weekly": "weekly", "week": "weekly", "wöchentlich": "weekly", "woechentlich": "weekly", "jede woche": "weekly",
}
WEB_TOOLS = ("web_search", "read_webpage")


def _fn(name: str, description: str, properties: dict | None = None, required: tuple = ()) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": list(required)},
        },
    }


def tool_schemas(web: bool = True) -> list[dict]:
    categories = {"type": "string", "enum": list(MEMORY_CATEGORIES)}
    statuses = {
        "type": "string",
        "enum": list(MEMORY_STATUSES),
        "description": "kanon = fixed fact, entwurf = idea in progress, offen = open question, verworfen = discarded",
    }
    tools = [
        _fn("save_memory",
            "Save something to long-term memory: story facts, characters, places, worlds, plot ideas or user "
            "preferences. If an entry with the same title exists in the same project, the content is appended.",
            {"title": {"type": "string", "description": "Short title, e.g. the character's name"},
             "content": {"type": "string", "description": "The facts to remember, in German, understandable alone"},
             "category": categories,
             "project": {"type": "string", "description": "Name of the story/project, empty for general facts"},
             "status": statuses},
            ("title", "content", "category")),
        _fn("search_memory", "Search long-term memory for stories, characters, ideas and preferences.",
            {"query": {"type": "string", "description": "Keywords, e.g. a name or topic"}}, ("query",)),
        _fn("update_memory",
            "Change an existing memory entry by id, e.g. correct it or change its status. "
            "'content' replaces the old content completely.",
            {"memory_id": {"type": "integer"}, "title": {"type": "string"}, "content": {"type": "string"},
             "category": categories, "status": statuses, "project": {"type": "string"}},
            ("memory_id",)),
        _fn("delete_memory", "Delete a memory entry by id when the user wants something forgotten.",
            {"memory_id": {"type": "integer"}}, ("memory_id",)),
        _fn("search_chats", "Search earlier saved conversations with the user.",
            {"query": {"type": "string"}}, ("query",)),
        _fn("create_reminder", "Create a reminder that pops up on the user's PC at the given local time.",
            {"text": {"type": "string", "description": "What to remind the user of, in German"},
             "when": {"type": "string", "description": "Local date and time, format YYYY-MM-DD HH:MM"},
             "repeat": {"type": "string", "enum": ["none", "daily", "weekly"]}},
            ("text", "when")),
        _fn("list_reminders", "List the user's upcoming reminders."),
        _fn("delete_reminder", "Delete a reminder by id.", {"reminder_id": {"type": "integer"}}, ("reminder_id",)),
    ]
    if web:
        tools += [
            _fn("web_search", "Search the internet. Only send general search terms, never private information.",
                {"query": {"type": "string"}}, ("query",)),
            _fn("read_webpage", "Read the text of a public web page (http/https URL), e.g. a search result.",
                {"url": {"type": "string"}}, ("url",)),
        ]
    return tools


def _text(value) -> str:
    if value is None:
        return ""
    return value.strip() if isinstance(value, str) else str(value).strip()


def _int(value) -> int | None:
    try:
        return int(_text(value).lstrip("#"))
    except ValueError:
        return None


def _choice(value, options, default):
    candidate = _text(value).lower()
    return candidate if candidate in options else default


class ToolExecutor:
    def __init__(self, db: Database, settings: Settings, project: str = "",
                 confirm: Callable[[str], bool] | None = None,
                 now: Callable[[], datetime] = datetime.now):
        self.db = db
        self.settings = settings
        self.project = project
        self.confirm = confirm
        self.now = now
        self.changed: set[str] = set()

    def describe(self, name: str, args: dict) -> str:
        a = args if isinstance(args, dict) else {}
        labels = {
            "save_memory": lambda: f"🧠 Merke mir: {_text(a.get('title'))}",
            "search_memory": lambda: f"🧠 Durchsuche mein Gedächtnis: «{_text(a.get('query'))}»",
            "update_memory": lambda: f"🧠 Aktualisiere Erinnerung #{_text(a.get('memory_id'))}",
            "delete_memory": lambda: f"🧠 Vergesse Eintrag #{_text(a.get('memory_id'))}",
            "search_chats": lambda: f"💬 Durchsuche alte Chats: «{_text(a.get('query'))}»",
            "create_reminder": lambda: f"⏰ Neue Erinnerung: {_text(a.get('text'))}",
            "list_reminders": lambda: "⏰ Schaue in deine Erinnerungen",
            "delete_reminder": lambda: f"⏰ Lösche Erinnerung #{_text(a.get('reminder_id'))}",
            "web_search": lambda: f"🔎 Suche im Internet: «{_text(a.get('query'))}»",
            "read_webpage": lambda: f"🌐 Lese Webseite: {_text(a.get('url'))}",
        }
        label = labels.get(name)
        return label() if label else f"🔧 Werkzeug: {name}"

    def execute(self, name: str, args) -> str:
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return f"FEHLER: Das Werkzeug «{name}» gibt es nicht."
        if name in WEB_TOOLS and not self.settings.web_enabled:
            return "FEHLER: Die Websuche ist in den Einstellungen ausgeschaltet."
        try:
            return handler(args)
        except websearch.WebError as exc:
            return f"FEHLER: {exc}"
        except Exception as exc:  # noqa: BLE001 - a tool must never crash the conversation
            log.exception("Werkzeug %s fehlgeschlagen", name)
            return f"FEHLER: {exc}"

    # ---------------------------------------------------------------- memory
    def _tool_save_memory(self, a: dict) -> str:
        title, content = _text(a.get("title")), _text(a.get("content"))
        if not title or not content:
            return "FEHLER: title und content sind nötig."
        category = _choice(a.get("category"), MEMORY_CATEGORIES, "notiz")
        status = _choice(a.get("status"), MEMORY_STATUSES, None)
        project = _text(a.get("project")) or ("" if category == "vorliebe" else self.project)
        memory_id, created = self.db.save_memory(title, content, category=category, project=project, status=status)
        self.changed.add("memory")
        return f"OK: {'Neu gespeichert' if created else 'Ergänzt'} als Erinnerung #{memory_id} «{title}»."

    def _tool_search_memory(self, a: dict) -> str:
        query = _text(a.get("query"))
        results = self.db.search_memories(query, project=self.project, limit=8)
        if not results:
            return f"Nichts gefunden zu «{query}»."
        return "Gefunden:\n" + "\n".join(format_memory(m) for m in results)

    def _tool_update_memory(self, a: dict) -> str:
        memory_id = _int(a.get("memory_id"))
        if memory_id is None:
            return "FEHLER: memory_id fehlt."
        fields = {key: _text(a.get(key)) for key in ("title", "content", "project") if _text(a.get(key))}
        if a.get("category"):
            fields["category"] = _choice(a.get("category"), MEMORY_CATEGORIES, "notiz")
        if a.get("status"):
            fields["status"] = _choice(a.get("status"), MEMORY_STATUSES, "entwurf")
        if not self.db.update_memory(memory_id, **fields):
            return f"FEHLER: Eine Erinnerung #{memory_id} gibt es nicht."
        self.changed.add("memory")
        return f"OK: Erinnerung #{memory_id} aktualisiert."

    def _tool_delete_memory(self, a: dict) -> str:
        memory_id = _int(a.get("memory_id"))
        memory = self.db.get_memory(memory_id) if memory_id is not None else None
        if memory is None:
            return f"FEHLER: Eine Erinnerung #{memory_id} gibt es nicht."
        self.db.delete_memory(memory_id)
        self.changed.add("memory")
        return f"OK: Erinnerung #{memory_id} «{memory['title']}» gelöscht."

    def _tool_search_chats(self, a: dict) -> str:
        results = self.db.search_messages(_text(a.get("query")), limit=8)
        if not results:
            return "In alten Chats habe ich nichts dazu gefunden."
        lines = []
        for r in results:
            who = "User" if r["role"] == "user" else "Connor"
            lines.append(f"- [{r['created_at'][:16]} · Chat «{r['chat_title']}»] {who}: {r['snippet']}")
        return "Gefunden:\n" + "\n".join(lines)

    # ------------------------------------------------------------- reminders
    def _tool_create_reminder(self, a: dict) -> str:
        text, when_raw = _text(a.get("text")), _text(a.get("when"))
        if not text or not when_raw:
            return "FEHLER: text und when sind nötig."
        now = self.now()
        due, _ = extract_when(when_raw, now)
        if due is None:
            return "FEHLER: Den Zeitpunkt habe ich nicht verstanden. Nutze das Format YYYY-MM-DD HH:MM."
        if due < now - timedelta(minutes=1):
            return f"FEHLER: {format_dt(due)} liegt in der Vergangenheit. Frag den User nach einem neuen Zeitpunkt."
        repeat = REPEAT_ALIASES.get(_text(a.get("repeat")).lower(), "none")
        reminder_id = self.db.add_reminder(text, due, repeat)
        self.changed.add("reminder")
        suffix = {"daily": " (täglich)", "weekly": " (wöchentlich)"}.get(repeat, "")
        return f"OK: Erinnerung #{reminder_id} gesetzt für {format_dt(due)}{suffix}: {text}"

    def _tool_list_reminders(self, a: dict) -> str:
        items = self.db.list_reminders(include_done=False)
        if not items:
            return "Es gibt keine offenen Erinnerungen."
        lines = []
        for r in items[:20]:
            repeat = {"daily": " (täglich)", "weekly": " (wöchentlich)"}.get(r["repeat"], "")
            lines.append(f"- #{r['id']} {format_dt(str_to_dt(r['due_at']))}{repeat}: {r['text']}")
        return "Offene Erinnerungen:\n" + "\n".join(lines)

    def _tool_delete_reminder(self, a: dict) -> str:
        reminder_id = _int(a.get("reminder_id"))
        if reminder_id is None or not self.db.delete_reminder(reminder_id):
            return f"FEHLER: Eine Erinnerung #{reminder_id} gibt es nicht."
        self.changed.add("reminder")
        return f"OK: Erinnerung #{reminder_id} gelöscht."

    # ------------------------------------------------------------------- web
    def _allowed(self, question: str) -> bool:
        if not self.settings.web_confirm or self.confirm is None:
            return True
        return bool(self.confirm(question))

    def _tool_web_search(self, a: dict) -> str:
        query = _text(a.get("query"))
        if not query:
            return "FEHLER: query fehlt."
        if not self._allowed(f"Connor möchte im Internet suchen nach:\n\n«{query}»"):
            return "Der User hat die Websuche abgelehnt."
        results = websearch.search(query, provider=self.settings.search_provider,
                                   searxng_url=self.settings.searxng_url)
        if not results:
            return f"Keine Treffer für «{query}»."
        lines = [f"Suchergebnisse für «{query}»:"]
        for number, r in enumerate(results, 1):
            lines.append(f"{number}. {r.title}\n   {r.url}\n   {r.snippet[:300]}")
        lines.append("Nenne die Quellen als Links. Für Details kannst du read_webpage nutzen.")
        return "\n".join(lines)

    def _tool_read_webpage(self, a: dict) -> str:
        url = _text(a.get("url"))
        if not url:
            return "FEHLER: url fehlt."
        if not self._allowed(f"Connor möchte diese Webseite öffnen:\n\n{url}"):
            return "Der User hat das Öffnen der Webseite abgelehnt."
        final_url, title, text = websearch.fetch_page(url)
        if not text:
            return f"Auf {final_url} habe ich keinen lesbaren Text gefunden."
        heading = f"Inhalt von {final_url}" + (f" («{title}»)" if title else "")
        return f"{heading}:\n\n{text}"
