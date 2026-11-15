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
STORY_CATEGORIES = frozenset({"charakter", "welt", "ort", "plot", "idee", "story"})
GENERIC_TITLE_WORDS = frozenset(
    "origin story stories backstory hintergrund hintergrundgeschichte ursprung geschichte charakter steckbrief "
    "profil charakterbogen bogen notiz notizen idee ideen teil kapitel szene info infos fakten über".split()
)
MEMORY_STATUSES = ("kanon", "entwurf", "offen", "verworfen")
REPEAT_OPTIONS = ("none", "daily", "weekly")
CHAT_MODES = ("writer", "assistant")
CHUNK_SIZE = 1200

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


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Splits a document into pieces of about ``size`` characters along paragraph borders.
    Pieces from inside a Markdown section remember the section title."""
    chunks: list[str] = []
    current = ""
    heading = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for para in (p.strip() for p in re.split(r"\n\s*\n", text)):
        if not para:
            continue
        if para.startswith("#"):
            heading = para.splitlines()[0].lstrip("#").strip()
        while len(para) > size:
            cut = para.rfind(". ", 0, size)
            if cut < size // 2:
                cut = para.rfind(" ", 0, size)
            if cut <= 0:
                cut = size
            flush()
            piece = para[: cut + 1].strip()
            chunks.append(f"({heading})\n{piece}" if heading and not piece.startswith("#") else piece)
            para = para[cut + 1:].strip()
        if current and len(current) + len(para) + 2 > size:
            flush()
        if not current and heading and not para.startswith("#"):
            current = f"({heading})\n"
        current = f"{current}\n\n{para}" if current and not current.endswith("\n") else current + para
    flush()
    return chunks


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
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path);
CREATE TABLE IF NOT EXISTS doc_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON doc_chunks(doc_id, position);
CREATE TABLE IF NOT EXISTS chat_documents (
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    PRIMARY KEY (chat_id, doc_id)
);
CREATE TABLE IF NOT EXISTS message_images (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_images ON message_images(message_id, position);
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
CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
    content, content='doc_chunks', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS doc_chunks_ai AFTER INSERT ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS doc_chunks_ad AFTER DELETE ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS doc_chunks_au AFTER UPDATE ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO doc_chunks_fts(rowid, content) VALUES (new.id, new.content);
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
            self._migrate(conn)
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
    def _migrate(conn: sqlite3.Connection) -> None:
        """Brings databases from older Connor versions up to date."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(chats)")}
        if "mode" not in columns:
            conn.execute("ALTER TABLE chats ADD COLUMN mode TEXT NOT NULL DEFAULT 'writer'")

    @staticmethod
    def _init_fts(conn: sqlite3.Connection) -> bool:
        try:
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            conn.executescript(FTS_SCHEMA)
            for table in ("memories_fts", "messages_fts", "doc_chunks_fts"):
                if table not in existing:
                    conn.execute(f"INSERT INTO {table}({table}) VALUES ('rebuild')")
            return True
        except sqlite3.OperationalError as exc:
            log.warning("Volltextsuche (FTS5) nicht verfügbar, nutze einfache Suche: %s", exc)
            return False

    # ------------------------------------------------------------------ chats
    def create_chat(self, title: str = "Neuer Chat", project: str = "", mode: str = "writer") -> int:
        now = now_str()
        mode = mode if mode in CHAT_MODES else "writer"
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO chats(title, project, mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (title, project, mode, now, now),
            )
            return int(cur.lastrowid)

    def set_chat_mode(self, chat_id: int, mode: str) -> None:
        if mode in CHAT_MODES:
            with self._connect() as conn:
                conn.execute("UPDATE chats SET mode = ? WHERE id = ?", (mode, chat_id))

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

    def delete_chat(self, chat_id: int) -> list[str]:
        """Deletes the chat; returns the image files that belonged to it."""
        with self._connect() as conn:
            images = [r[0] for r in conn.execute(
                "SELECT path FROM message_images WHERE message_id IN (SELECT id FROM messages WHERE chat_id = ?)",
                (chat_id,)).fetchall()]
            conn.execute("DELETE FROM message_images WHERE message_id IN (SELECT id FROM messages WHERE chat_id = ?)",
                         (chat_id,))
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM chat_documents WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        return images

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

    def add_message_images(self, message_id: int, paths: list[str]) -> None:
        with self._connect() as conn:
            conn.executemany("INSERT INTO message_images(message_id, position, path) VALUES (?, ?, ?)",
                             [(message_id, position, str(path)) for position, path in enumerate(paths)])

    def message_images(self, message_id: int) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT path FROM message_images WHERE message_id = ? ORDER BY position",
                                (message_id,)).fetchall()
        return [r[0] for r in rows]

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
                        include_discarded: bool = True, strict: bool = False) -> list[dict]:
        """Relevance search. Entries of the current project come first, then general ones.
        ``strict``: inside a project, never return story facts of other projects (no mixing up of stories)."""
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
        if strict and current:
            results = [m for m in results if m["project"].strip().casefold() == current
                       or (not m["project"].strip() and m["category"] not in STORY_CATEGORIES)]

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
                   UNION SELECT project FROM chats WHERE project <> ''
                   UNION SELECT project FROM documents WHERE project <> ''"""
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

    # -------------------------------------------------------------- documents
    def add_document(self, name: str, content: str, *, path: str = "", kind: str = "",
                     project: str = "") -> tuple[int, bool]:
        """Stores a document and its search chunks. Importing the same file again replaces the
        old version. Returns (id, created)."""
        now = now_str()
        chunks = chunk_text(content)
        with self._connect() as conn:
            row = conn.execute("SELECT id, project FROM documents WHERE path = ? AND path <> ''",
                               (path,)).fetchone() if path else None
            if row is None:
                cur = conn.execute(
                    """INSERT INTO documents(name, path, kind, project, content, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (name, path, kind, project, content, now, now),
                )
                doc_id, created = int(cur.lastrowid), True
            else:
                doc_id, created = int(row["id"]), False
                conn.execute(
                    "UPDATE documents SET name = ?, kind = ?, project = ?, content = ?, updated_at = ? WHERE id = ?",
                    (name, kind, project or row["project"], content, now, doc_id),
                )
                conn.execute("DELETE FROM doc_chunks WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO doc_chunks(doc_id, position, content) VALUES (?, ?, ?)",
                [(doc_id, position, chunk) for position, chunk in enumerate(chunks)],
            )
        return doc_id, created

    def get_document(self, doc_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT *, length(content) AS chars FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def list_documents(self, search: str = "", project: str | None = None) -> list[dict]:
        clauses, params = [], []
        if project is not None:
            clauses.append("project = ?")
            params.append(project)
        if search.strip():
            clauses.append("(name LIKE ? OR project LIKE ? OR content LIKE ?)")
            params += [f"%{search.strip()}%"] * 3
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT id, name, path, kind, project, created_at, updated_at, length(content) AS chars
                    FROM documents {where} ORDER BY updated_at DESC, id DESC""",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def update_document(self, doc_id: int, name: str | None = None, project: str | None = None) -> bool:
        values = {k: v for k, v in (("name", name), ("project", project)) if v is not None}
        if not values:
            return self.get_document(doc_id) is not None
        values["updated_at"] = now_str()
        columns = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as conn:
            cur = conn.execute(f"UPDATE documents SET {columns} WHERE id = ?", (*values.values(), doc_id))
            return cur.rowcount > 0

    def delete_document(self, doc_id: int) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM doc_chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM chat_documents WHERE doc_id = ?", (doc_id,))
            return conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,)).rowcount > 0

    def document_chunks(self, doc_id: int, limit: int = 1000) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT c.id, c.doc_id, c.position, c.content, d.name, d.project
                   FROM doc_chunks c JOIN documents d ON d.id = c.doc_id
                   WHERE c.doc_id = ? ORDER BY c.position LIMIT ?""",
                (doc_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_chunks(self, query: str, *, doc_ids: list[int] | None = None, exclude: list[int] | None = None,
                      project: str = "", limit: int = 6, strict: bool = False) -> list[dict]:
        """Finds the document passages that fit best. Each result has ``hits``: how many of the
        search words it contains."""
        words = keywords(query)
        if not words or doc_ids == []:
            return []
        filters, params = [], []
        if doc_ids:
            filters.append(f"c.doc_id IN ({','.join('?' * len(doc_ids))})")
            params += doc_ids
        if exclude:
            filters.append(f"c.doc_id NOT IN ({','.join('?' * len(exclude))})")
            params += exclude
        extra = "".join(f" AND {f}" for f in filters)
        with self._connect() as conn:
            if self.fts:
                rows = conn.execute(
                    f"""SELECT c.id, c.doc_id, c.position, c.content, d.name, d.project,
                               bm25(doc_chunks_fts) AS score
                        FROM doc_chunks_fts
                        JOIN doc_chunks c ON c.id = doc_chunks_fts.rowid
                        JOIN documents d ON d.id = c.doc_id
                        WHERE doc_chunks_fts MATCH ?{extra}
                        ORDER BY score LIMIT 60""",
                    (_fts_match(words), *params),
                ).fetchall()
            else:
                like = " OR ".join("c.content LIKE ?" for _ in words)
                rows = conn.execute(
                    f"""SELECT c.id, c.doc_id, c.position, c.content, d.name, d.project, 0 AS score
                        FROM doc_chunks c JOIN documents d ON d.id = c.doc_id
                        WHERE ({like}){extra} LIMIT 300""",
                    (*[f"%{w}%" for w in words], *params),
                ).fetchall()
        results = []
        for row in rows:
            chunk = dict(row)
            text = chunk["content"].lower()
            chunk["hits"] = sum(1 for w in words if w in text)
            if not self.fts:
                chunk["score"] = -chunk["hits"]
            results.append(chunk)
        current = project.strip().casefold()
        if strict and current:
            results = [c for c in results if c["project"].strip().casefold() in (current, "")]

        def priority(chunk: dict) -> int:
            own = chunk["project"].strip().casefold()
            if current and own == current:
                return 0
            return 1 if not own else 2

        results.sort(key=lambda c: (priority(c), -c["hits"], c["score"]))
        return results[:limit]

    def named_memories(self, text: str, project: str = "", strict: bool = True) -> tuple[list[dict], set[str]]:
        """Entries whose title names something the text mentions («Unstoppable», «Lira»).
        Returns (entries, the matching names). Generic title words like «Origin Story» do not count."""
        words = set(_WORD_RE.findall(text.casefold()))
        current = project.strip().casefold()
        found: list[dict] = []
        names: set[str] = set()
        for memory in self.list_memories():
            if memory["status"] == "verworfen":
                continue
            own = memory["project"].strip().casefold()
            if strict and current and (own not in (current, "") or (not own and memory["category"] in STORY_CATEGORIES)):
                continue
            title_names = {t for t in keywords(memory["title"], 8) if t not in GENERIC_TITLE_WORDS}
            hits = title_names & words
            if hits:
                found.append(memory)
                names |= hits
        return found, names

    def suggest_project(self, text: str) -> str:
        """If the text names characters/places of exactly one project, that project – else ""."""
        low = text.casefold()
        found: set[str] = set()
        for memory in self.list_memories():
            title = memory["title"].strip().casefold()
            if memory["project"].strip() and memory["category"] in STORY_CATEGORIES and len(title) >= 3 \
                    and re.search(rf"(?<!\w){re.escape(title)}(?!\w)", low):
                found.add(memory["project"].strip())
        return found.pop() if len(found) == 1 else ""

    def attach_document(self, chat_id: int, doc_id: int) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO chat_documents(chat_id, doc_id) VALUES (?, ?)", (chat_id, doc_id))

    def detach_document(self, chat_id: int, doc_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chat_documents WHERE chat_id = ? AND doc_id = ?", (chat_id, doc_id))

    def chat_documents(self, chat_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT d.id, d.name, d.kind, d.project, length(d.content) AS chars
                   FROM chat_documents cd JOIN documents d ON d.id = cd.doc_id
                   WHERE cd.chat_id = ? ORDER BY d.name""",
                (chat_id,),
            ).fetchall()
        return [dict(r) for r in rows]
