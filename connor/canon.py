"""The canon bible: all facts of a project in one clean document – e.g. to upload into a Claude project."""
from __future__ import annotations

from datetime import datetime

from .database import Database

SECTIONS = [
    ("charakter", "Figuren"),
    ("ort", "Orte"),
    ("welt", "Welt und Regeln"),
    ("story", "Geschichte"),
    ("plot", "Plot"),
    ("idee", "Ideen"),
    ("notiz", "Notizen"),
]
STATUS_ORDER = {"kanon": 0, "entwurf": 1, "offen": 2}
STATUS_NOTE = {"entwurf": " *(Entwurf)*", "offen": " *(noch offen)*"}


def build_canon_markdown(db: Database, project: str, *, include_drafts: bool = True,
                         now: datetime | None = None) -> str:
    now = now or datetime.now()
    memories = [m for m in db.list_memories(project=project) if m["status"] != "verworfen"
                and (include_drafts or m["status"] == "kanon")]
    lines = [f"# Kanon-Bibel – {project or 'Allgemein'}", "",
             f"*Stand: {now:%d.%m.%Y}. Alles mit «Entwurf» oder «noch offen» ist nicht fix.*", ""]
    if not memories:
        lines.append("Zu diesem Projekt ist noch nichts gespeichert.")
        return "\n".join(lines) + "\n"
    known = {key for key, _label in SECTIONS}
    for key, label in SECTIONS:
        entries = [m for m in memories if m["category"] == key or (key == "notiz" and m["category"] not in known)]
        if not entries:
            continue
        lines += [f"## {label}", ""]
        for memory in sorted(entries, key=lambda m: (STATUS_ORDER.get(m["status"], 3), m["title"].casefold())):
            lines += [f"### {memory['title']}{STATUS_NOTE.get(memory['status'], '')}", "", memory["content"].strip(), ""]
    return "\n".join(lines).rstrip() + "\n"
