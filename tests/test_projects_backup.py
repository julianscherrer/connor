import sqlite3
from datetime import datetime, timedelta

from connor import backup
from connor.database import Database
from connor.prompts import build_writing_prompt
from connor.settings import Settings
from connor.tools import ToolExecutor


def test_projects_do_not_mix(db):
    lira = db.add_memory("Lira", "Elfe mit Angst vor Wasser", "charakter", "Drachenreiter")
    db.add_memory("Lira", "Roboterin aus dem Jahr 3000", "charakter", "Sternenstaub")
    stray = db.add_memory("Lira Hintergrund", "Wuchs im Waisenhaus auf", "charakter", "")
    note = db.add_memory("Schreibstil Lira", "Kurze Sätze bei Lira-Szenen", "notiz", "")
    ids = [m["id"] for m in db.search_memories("Was weisst du über Lira?", project="Drachenreiter", strict=True)]
    assert ids[0] == lira and note in ids
    assert stray not in ids, "general story facts must not leak into a project"
    assert all(db.get_memory(i)["project"] in ("Drachenreiter", "") for i in ids)
    loose = db.search_memories("Lira", project="", strict=True)
    assert len(loose) == 4, "without a project Connor may look everywhere"


def test_documents_do_not_mix(db):
    own, _ = db.add_document("welt.md", "Der Nebelwald ist dunkel.", project="Drachenreiter")
    other, _ = db.add_document("andere.md", "Der Nebelwald ist ein Raumhafen.", project="Sternenstaub")
    general, _ = db.add_document("notizen.md", "Nebelwald: Ideen sammeln.")
    ids = [c["doc_id"] for c in db.search_chunks("Nebelwald", project="Drachenreiter", strict=True)]
    assert ids == [own, general] and other not in ids


def test_suggest_project(db):
    db.add_memory("Lira", "Elfe", "charakter", "Drachenreiter")
    db.add_memory("Eisenfels", "Stadt", "ort", "Drachenreiter")
    db.add_memory("Zorg", "Bösewicht", "charakter", "Sternenstaub")
    db.add_memory("Kaffee", "mag Kaffee", "vorliebe", "")
    assert db.suggest_project("Wie fühlt sich Lira in Eisenfels?") == "Drachenreiter"
    assert db.suggest_project("Was macht zorg heute?") == "Sternenstaub"
    assert db.suggest_project("Lira trifft Zorg") == "", "two projects – Connor does not guess"
    assert db.suggest_project("Liraland ist schön") == ""
    assert db.suggest_project("Ich mag Kaffee") == ""


def test_saving_without_project_warns(db):
    executor = ToolExecutor(db, Settings())
    result = executor.execute("save_memory", {"title": "Tom", "content": "Schmied", "category": "charakter"})
    assert "kein Projekt gewählt" in result
    project = ToolExecutor(db, Settings(), project="Drachenreiter")
    assert "kein Projekt" not in project.execute("save_memory", {"title": "Tom", "content": "Schmied",
                                                                 "category": "charakter"})


def test_canon_check_prompt():
    prompt = build_writing_prompt(action="canon", title="Kapitel 2", text="Liras blaue Augen …", context="<kontext>x")
    assert prompt.startswith("<kontext>x") and "Widersprüche" in prompt and "Liras blaue Augen" in prompt


def test_backup(tmp_path):
    source = tmp_path / "connor.db"
    database = Database(source)
    database.add_memory("Lira", "Elfe", "charakter")
    folder = tmp_path / "backups"
    day = datetime(2026, 11, 15, 22, 0)
    assert backup.backup_due(folder, day)
    target = backup.make_backup(folder, source=source, now=day)
    assert target.name == "connor-2026-11-15.db" and not backup.backup_due(folder, day)
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("SELECT title FROM memories").fetchone()[0] == "Lira"
    finally:
        conn.close()
    for offset in range(1, 6):
        backup.make_backup(folder, source=source, keep=3, now=day + timedelta(days=offset))
    assert [p.name for p in sorted(folder.glob("connor-????-??-??.db"))] == [
        "connor-2026-11-18.db", "connor-2026-11-19.db", "connor-2026-11-20.db"]
    assert backup.latest_backup(folder).name == "connor-2026-11-20.db"
