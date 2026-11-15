"""Local SQLite storage: chats, memories, reminders and stories."""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

MEMORY_CATEGORIES = ("charakter", "welt", "ort", "plot", "idee", "story", "vorliebe", "notiz")
MEMORY_STATUSES = ("kanon", "entwurf", "offen", "verworfen")
REPEAT_OPTIONS = ("none", "daily", "weekly")

TIME_FMT = "%Y-%m-%d %H:%M:%S"

STOPWORDS = frozenset(
    """
    aber alle allem allen aller alles also als am an andere anderen auch auf aus bei beim bin bis bist da dabei
    damit dann das dass dein deine deinen deiner dem den denn der des dich die dies diese diesem diesen dieser dir
    doch dort du durch ein eine einem einen einer eines er es etwas euch euer für gegen gibt hab habe haben hast hat
    hatte hier hin hinter ich ihm ihn ihr ihre im in ins ist ja jetzt kann kannst kein keine können könnte machen mal
    man mehr mein meine meinen meiner mich mir mit muss nach nicht nichts noch nun nur ob oder ohne schon sehr sein
    seine sich sie sind so soll sollte über um und uns unser unter viel vom von vor war waren was weil welche wenn wer
    werden wie wieder wir wird wo zu zum zur zwar bitte danke hallo hoi connor okay gut genau einfach eigentlich gerade
    weiter weisst meinst denkst sag sagen gib schreib schreibe erzähl erzähle hilf
    the and for are but not you all any can her was one our out get has him his how its may new now see two who did
    let say she too use what with this that from have they will your about would there their which
    """.split()
)

_WORD_RE = re.compile(r"\w+")


def now_str() -> str:
    return datetime.now().strftime(TIME_FMT)


def dt_to_str(value: datetime) -> str:
    return value.strftime(TIME_FMT)


def str_to_dt(value: str) -> datetime:
    return datetime.strptime(value, TIME_FMT)


def keywords(text: str, limit: int = 12) -> list[str]:
    """Meaningful search words from a free text."""
    words: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        word = word.strip("_")
        if len(word) < 3 or word.isdigit() or word in STOPWORDS or word in words:
            continue
        words.append(word)
        if len(words) >= limit:
            break
    return words


SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'Neuer Chat',
    project TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'notiz',
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'entwurf',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,
    repeat TEXT NOT NULL DEFAULT 'none',
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(done, due_at);
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title, content, project, category,
    content='memories', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, content, project, category)
    VALUES (new.id, new.title, new.content, new.project, new.category);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, project, category)
    VALUES ('delete', old.id, old.title, old.content, old.project, old.category);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, project, category)
    VALUES ('delete', old.id, old.title, old.content, old.project, old.category);
    INSERT INTO memories_fts(rowid, title, content, project, category)
    VALUES (new.id, new.title, new.content, new.project, new.category);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def _fts_match(words: list[str]) -> str:
    return " OR ".join(f'"{w}"*' for w in words)


class Database:
    """Thread-safe: every operation opens its own short-lived connection."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            self.fts = self._init_fts(conn)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _init_fts(conn: sqlite3.Connection) -> bool:
        try:
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            conn.executescript(FTS_SCHEMA)
            if "memories_fts" not in existing:
                conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")
            if "messages_fts" not in existing:
                conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
            return True
        except sqlite3.OperationalError as exc:
            log.warning("Volltextsuche (FTS5) nicht verfügbar, nutze einfache Suche: %s", exc)
            return False

    # ------------------------------------------------------------------ chats
    def create_chat(self, title: str = "Neuer Chat", project: str = "") -> int:
        now = now_str()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO chats(title, project, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (title, project, now, now),
            )
            return int(cur.lastrowid)

    def get_chat(self, chat_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        return dict(row) if row else None

    def list_chats(self, search: str = "", limit: int = 500) -> list[dict]:
        with self._connect() as conn:
            if not search.strip():
                rows = conn.execute(
                    "SELECT * FROM chats ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                like = f"%{search.strip()}%"
                rows = conn.execute(
                    """SELECT * FROM chats
                       WHERE title LIKE ? OR project LIKE ?
                          OR id IN (SELECT chat_id FROM messages WHERE content LIKE ?)
                       ORDER BY updated_at DESC, id DESC LIMIT ?""",
                    (like, like, like, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def rename_chat(self, chat_id: int, title: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))

    def set_chat_project(self, chat_id: int, project: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE chats SET project = ? WHERE id = ?", (project, chat_id))

    def delete_chat(self, chat_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))

    def add_message(self, chat_id: int, role: str, content: str) -> int:
        now = now_str()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages(chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, role, content, now),
            )
            conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
            return int(cur.lastrowid)

    def get_messages(self, chat_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def search_messages(self, query: str, limit: int = 8) -> list[dict]:
        words = keywords(query)
        if not words:
            return []
        with self._connect() as conn:
            if self.fts:
                rows = conn.execute(
                    """SELECT m.id, m.chat_id, m.role, m.created_at, c.title AS chat_title,
                              snippet(messages_fts, 0, '«', '»', ' … ', 24) AS snippet
                       FROM messages_fts
                       JOIN messages m ON m.id = messages_fts.rowid
                       JOIN chats c ON c.id = m.chat_id
                       WHERE messages_fts MATCH ? AND m.role IN ('user', 'assistant')
                       ORDER BY bm25(messages_fts) LIMIT ?""",
                    (_fts_match(words), limit),
                ).fetchall()
                return [dict(r) for r in rows]
            clause = " OR ".join("m.content LIKE ?" for _ in words)
            rows = conn.execute(
                f"""SELECT m.id, m.chat_id, m.role, m.created_at, c.title AS chat_title,
                           substr(m.content, 1, 200) AS snippet
                    FROM messages m JOIN chats c ON c.id = m.chat_id
                    WHERE m.role IN ('user', 'assistant') AND ({clause})
                    ORDER BY m.id DESC LIMIT ?""",
                (*[f"%{w}%" for w in words], limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def export_chat_markdown(self, chat_id: int) -> str:
        chat = self.get_chat(chat_id)
        if not chat:
            return ""
        lines = [f"# {chat['title']}", ""]
        if chat["project"]:
            lines += [f"*Projekt: {chat['project']}*", ""]
        names = {"user": "Du", "assistant": "Connor"}
        for msg in self.get_messages(chat_id):
            stamp = msg["created_at"][:16]
            if msg["role"] in names:
                lines += [f"### {names[msg['role']]} · {stamp}", "", msg["content"], ""]
            else:
                lines += [f"> {msg['content']}", ""]
        return "\n".join(lines).rstrip() + "\n"

    # --------------------------------------------------------------- memories
    def add_memory(self, title: str, content: str, category: str = "notiz", project: str = "",
                   status: str = "entwurf") -> int:
        now = now_str()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO memories(project, category, title, content, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (project, category, title, content, status, now, now),
            )
            return int(cur.lastrowid)

    def find_memory(self, title: str, project: str = "") -> dict | None:
        wanted = title.strip().casefold()
        for memory in self.list_memories(project=project):
            if memory["title"].strip().casefold() == wanted:
                return memory
        return None

    def save_memory(self, title: str, content: str, category: str = "notiz", project: str = "",
                    status: str | None = None) -> tuple[int, bool]:
        """Create a memory or extend an existing one with the same title. Returns (id, created)."""
        existing = self.find_memory(title, project)
        if existing is None:
            return self.add_memory(title, content.strip(), category, project, status or "entwurf"), True
        fields: dict[str, str] = {}
        addition = content.strip()
        if addition and addition.casefold() not in existing["content"].casefold():
            fields["content"] = existing["content"].rstrip() + "\n" + addition
        if status:
            fields["status"] = status
        if category and existing["category"] == "notiz" and category != "notiz":
            fields["category"] = category
        if fields:
            self.update_memory(existing["id"], **fields)
        return existing["id"], False

    def update_memory(self, memory_id: int, **fields) -> bool:
        allowed = ("title", "content", "category", "project", "status")
        values = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not values:
            return self.get_memory(memory_id) is not None
        values["updated_at"] = now_str()
        columns = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as conn:
            cur = conn.execute(f"UPDATE memories SET {columns} WHERE id = ?", (*values.values(), memory_id))
            return cur.rowcount > 0

    def delete_memory(self, memory_id: int) -> bool:
        with self._connect() as conn:
            return conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    def get_memory(self, memory_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return dict(row) if row else None

    def list_memories(self, project: str | None = None, category: str | None = None,
                      status: str | None = None, search: str = "") -> list[dict]:
        clauses, params = [], []
        if project is not None:
            clauses.append("project = ?")
            params.append(project)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if search.strip():
            clauses.append("(title LIKE ? OR content LIKE ? OR project LIKE ?)")
            params += [f"%{search.strip()}%"] * 3
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memories {where} ORDER BY updated_at DESC, id DESC", params
            ).fetchall()
        return [dict(r) for r in rows]

    def search_memories(self, query: str, project: str = "", limit: int = 8,
                        include_discarded: bool = True) -> list[dict]:
        """Relevance search. Entries of the current project come first, then general ones."""
        words = keywords(query)
        if not words:
            return []
        if self.fts:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT m.*, bm25(memories_fts, 10.0, 1.0, 2.0, 0.5) AS score
                       FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid
                       WHERE memories_fts MATCH ? ORDER BY score LIMIT 60""",
                    (_fts_match(words),),
                ).fetchall()
            results = [dict(r) for r in rows]
        else:
            results = []
            for memory in self.list_memories():
                title = memory["title"].lower()
                haystack = f"{title} {memory['content'].lower()} {memory['project'].lower()}"
                score = -sum(3 if w in title else 1 for w in words if w in haystack)
                if score < 0:
                    memory["score"] = score
                    results.append(memory)
            results.sort(key=lambda m: m["score"])

        if not include_discarded:
            results = [m for m in results if m["status"] != "verworfen"]
        current = project.strip().casefold()

        def priority(memory: dict) -> int:
            own = memory["project"].strip().casefold()
            if current and own == current:
                return 0
            return 1 if not own else 2

        results.sort(key=lambda m: (priority(m), m["score"]))
        return results[:limit]

    def list_projects(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT project FROM memories WHERE project <> ''
                   UNION SELECT project FROM stories WHERE project <> ''
                   UNION SELECT project FROM chats WHERE project <> ''"""
            ).fetchall()
        return sorted({r[0] for r in rows}, key=str.casefold)

    # -------------------------------------------------------------- reminders
    def add_reminder(self, text: str, due_at: datetime, repeat: str = "none") -> int:
        if repeat not in REPEAT_OPTIONS:
            repeat = "none"
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO reminders(text, due_at, repeat, done, created_at) VALUES (?, ?, ?, 0, ?)",
                (text, dt_to_str(due_at.replace(microsecond=0)), repeat, now_str()),
            )
            return int(cur.lastrowid)

    def get_reminder(self, reminder_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return dict(row) if row else None

    def list_reminders(self, include_done: bool = False) -> list[dict]:
        where = "" if include_done else "WHERE done = 0"
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM reminders {where} ORDER BY done, due_at").fetchall()
        return [dict(r) for r in rows]

    def update_reminder(self, reminder_id: int, text: str | None = None, due_at: datetime | None = None,
                        repeat: str | None = None, done: bool | None = None) -> bool:
        values: dict[str, object] = {}
        if text is not None:
            values["text"] = text
        if due_at is not None:
            values["due_at"] = dt_to_str(due_at.replace(microsecond=0))
        if repeat is not None:
            values["repeat"] = repeat if repeat in REPEAT_OPTIONS else "none"
        if done is not None:
            values["done"] = 1 if done else 0
        if not values:
            return self.get_reminder(reminder_id) is not None
        columns = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as conn:
            cur = conn.execute(f"UPDATE reminders SET {columns} WHERE id = ?", (*values.values(), reminder_id))
            return cur.rowcount > 0

    def delete_reminder(self, reminder_id: int) -> bool:
        with self._connect() as conn:
            return conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,)).rowcount > 0

    def due_reminders(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE done = 0 AND due_at <= ? ORDER BY due_at",
                (dt_to_str(now),),
            ).fetchall()
        return [dict(r) for r in rows]

    def acknowledge_reminder(self, reminder_id: int, now: datetime | None = None) -> None:
        """Called after a reminder fired: finish it or schedule the next repetition."""
        reminder = self.get_reminder(reminder_id)
        if not reminder:
            return
        steps = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}
        step = steps.get(reminder["repeat"])
        if step is None:
            self.update_reminder(reminder_id, done=True)
            return
        now = now or datetime.now()
        due = str_to_dt(reminder["due_at"])
        while due <= now:
            due += step
        self.update_reminder(reminder_id, due_at=due)

    # ---------------------------------------------------------------- stories
    def create_story(self, title: str = "Neue Geschichte", project: str = "", content: str = "") -> int:
        now = now_str()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO stories(title, project, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (title, project, content, now, now),
            )
            return int(cur.lastrowid)

    def get_story(self, story_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
        return dict(row) if row else None

    def list_stories(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, title, project, created_at, updated_at, length(content) AS chars
                   FROM stories ORDER BY updated_at DESC, id DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def update_story(self, story_id: int, title: str | None = None, project: str | None = None,
                     content: str | None = None) -> bool:
        values = {k: v for k, v in (("title", title), ("project", project), ("content", content)) if v is not None}
        if not values:
            return self.get_story(story_id) is not None
        values["updated_at"] = now_str()
        columns = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as conn:
            cur = conn.execute(f"UPDATE stories SET {columns} WHERE id = ?", (*values.values(), story_id))
            return cur.rowcount > 0

    def delete_story(self, story_id: int) -> bool:
        with self._connect() as conn:
            return conn.execute("DELETE FROM stories WHERE id = ?", (story_id,)).rowcount > 0
