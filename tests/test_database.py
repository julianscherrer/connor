from datetime import datetime, timedelta

from connor.database import Database, keywords


def test_chats_and_messages(db):
    chat = db.create_chat("Drachen", "Drachenreiter")
    db.add_message(chat, "user", "Lira fliegt auf Funkenflug")
    db.add_message(chat, "assistant", "Funkenflug ist ein roter Drache")
    db.add_message(chat, "event", "🧠 Merke mir: Funkenflug")
    assert [m["role"] for m in db.get_messages(chat)] == ["user", "assistant", "event"]
    assert db.list_chats()[0]["id"] == chat
    assert db.list_chats("funkenflug")[0]["id"] == chat
    assert db.list_chats("gibtsnicht") == []

    found = db.search_messages("Funkenflug Drache")
    assert {r["role"] for r in found} == {"user", "assistant"}
    assert found[0]["chat_title"] == "Drachen"

    markdown = db.export_chat_markdown(chat)
    assert "# Drachen" in markdown and "### Connor" in markdown

    db.rename_chat(chat, "Neu")
    db.set_chat_project(chat, "Anderes")
    assert db.get_chat(chat)["title"] == "Neu"
    db.delete_chat(chat)
    assert db.get_chat(chat) is None
    assert db.search_messages("Funkenflug") == []


def test_save_memory_appends_instead_of_duplicating(db):
    first, created = db.save_memory("Lira", "Elfe, 19 Jahre", "charakter", "Drachenreiter", "kanon")
    second, created_again = db.save_memory("lira", "Hat Angst vor Wasser", "charakter", "Drachenreiter")
    db.save_memory("Lira", "hat angst vor wasser", "charakter", "Drachenreiter")
    assert created and not created_again and first == second
    memory = db.get_memory(first)
    assert memory["content"] == "Elfe, 19 Jahre\nHat Angst vor Wasser"
    assert memory["status"] == "kanon"
    other, created_other = db.save_memory("Lira", "Andere Lira", "charakter", "Sternenstaub")
    assert created_other and other != first


def test_search_prefers_current_project_and_hides_discarded(db):
    other, _ = db.save_memory("Tom", "Schmied mit Drachenangst", "charakter", "Sternenstaub")
    own = db.add_memory("Lira", "Reitet einen Drachen", "charakter", "Drachenreiter", "kanon")
    general = db.add_memory("Drachen allgemein", "Drachen speien Feuer", "notiz", "")
    old = db.add_memory("Alter Drache", "Verworfene Drachenidee", "idee", "Drachenreiter", "verworfen")

    ids = [m["id"] for m in db.search_memories("Erzähl mir was über Drachen", project="Drachenreiter")]
    assert set(ids[:2]) == {own, old}
    assert ids[2:] == [general, other]
    without = [m["id"] for m in db.search_memories("Drachen", project="Drachenreiter", include_discarded=False)]
    assert without == [own, general, other]
    assert db.search_memories("") == []


def test_search_without_fts(tmp_path, monkeypatch):
    database = Database(tmp_path / "plain.db")
    database.fts = False
    database.add_memory("Lira", "Elfe mit Bogen", "charakter")
    assert [m["title"] for m in database.search_memories("Bogen")] == ["Lira"]
    chat = database.create_chat("x")
    database.add_message(chat, "user", "Wir reden über Bogenschiessen")
    assert database.search_messages("bogenschiessen")


def test_update_and_delete_memory(db):
    memory_id = db.add_memory("Idee", "Ein sprechender Kühlschrank", "idee")
    assert db.update_memory(memory_id, status="kanon", content="Ein sprechender, launischer Kühlschrank")
    assert db.get_memory(memory_id)["status"] == "kanon"
    assert not db.update_memory(9999, status="kanon")
    assert db.search_memories("launischer")[0]["id"] == memory_id
    assert db.delete_memory(memory_id)
    assert db.search_memories("launischer") == []


def test_projects(db):
    db.add_memory("A", "x", project="Zebra")
    db.create_story("Story", project="apfel")
    db.create_chat("Chat", project="Mond")
    assert db.list_projects() == ["apfel", "Mond", "Zebra"]


def test_reminders_due_and_repeat(db):
    now = datetime(2026, 11, 15, 20, 0)
    once = db.add_reminder("Einmal", now - timedelta(minutes=1))
    daily = db.add_reminder("Täglich", now - timedelta(days=2, minutes=5), "daily")
    future = db.add_reminder("Später", now + timedelta(hours=1))
    assert {r["id"] for r in db.due_reminders(now)} == {once, daily}

    db.acknowledge_reminder(once, now)
    db.acknowledge_reminder(daily, now)
    assert db.get_reminder(once)["done"] == 1
    assert db.get_reminder(daily)["done"] == 0
    assert db.get_reminder(daily)["due_at"] == "2026-11-16 19:55:00"
    assert db.due_reminders(now) == []
    assert [r["id"] for r in db.list_reminders()] == [future, daily]
    assert len(db.list_reminders(include_done=True)) == 3
    assert db.delete_reminder(future)


def test_stories(db):
    story = db.create_story("Anfang", "Drachenreiter", "Es war einmal")
    db.update_story(story, content="Es war einmal ein Drache.")
    assert db.get_story(story)["content"] == "Es war einmal ein Drache."
    assert db.list_stories()[0]["chars"] == len("Es war einmal ein Drache.")
    assert db.delete_story(story)
    assert db.list_stories() == []


def test_keywords_skip_stopwords():
    assert keywords("Was weisst du über Lira und den Drachen?") == ["lira", "drachen"]


def test_fts_index_is_rebuilt_for_existing_rows(tmp_path):
    path = tmp_path / "old.db"
    database = Database(path)
    database.add_memory("Lira", "Elfe", "charakter")
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.executescript("DROP TABLE memories_fts; DROP TRIGGER memories_ai; DROP TRIGGER memories_ad; "
                           "DROP TRIGGER memories_au;")
    reopened = Database(path)
    assert reopened.search_memories("Lira")[0]["title"] == "Lira"
