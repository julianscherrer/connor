"""Drives the real window (offscreen) against the fake Ollama server."""
import time
from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from connor.gui.main_window import MainWindow  # noqa: E402
from connor.gui.theme import apply_theme, make_icon  # noqa: E402
from connor.gui.writing_tab import continuation_separator  # noqa: E402
from connor.settings import Settings  # noqa: E402
from fake_ollama import tool_call  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    yield application


def wait_until(condition, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def window(app, db, fake):
    settings = Settings(ollama_url=fake.url)
    win = MainWindow(db, settings, make_icon())
    win.resize(1200, 800)
    win.show()
    assert wait_until(lambda: win.model_box.count() > 0)
    yield win
    win.settings.minimize_to_tray = False
    win.close()


def test_startup_detects_model(window):
    assert window.settings.model == "qwen3:8b"
    assert "qwen3:8b" in window.chat_tab.status.text()
    assert not window.chat_tab.banner.isVisible()


def test_chat_roundtrip_with_tools(window, db, fake):
    fake.script += [
        {"content": "", "tool_calls": [tool_call("save_memory", title="Lira", content="Elfe mit Bogen",
                                                 category="charakter")]},
        {"content": "Lira ist gespeichert! **Cool**", "delay": 0.005},
    ]
    chat = window.chat_tab
    chat.project_box.setCurrentText("Drachenreiter")
    chat.input.setPlainText("Merk dir: Lira ist eine Elfe mit Bogen")
    chat.send()
    assert chat.busy
    assert wait_until(lambda: not chat.busy)
    messages = db.get_messages(chat.chat_id)
    assert [m["role"] for m in messages] == ["user", "event", "assistant"]
    assert messages[-1]["content"] == "Lira ist gespeichert! **Cool**"
    assert db.get_chat(chat.chat_id)["project"] == "Drachenreiter"
    assert db.search_memories("Lira")[0]["project"] == "Drachenreiter"
    assert window.memory_tab.table.rowCount() == 1
    assert chat.chat_list.count() == 1

    chat.new_chat()
    assert chat.chat_id is None
    chat.open_chat(messages[0]["chat_id"])
    assert chat.chat_id == messages[0]["chat_id"]


def test_stop_keeps_partial_answer(window, db, fake):
    fake.script.append({"content": "Es war einmal ein Drache " * 100, "delay": 0.02})
    chat = window.chat_tab
    chat.input.setPlainText("Erzähl")
    chat.send()
    assert wait_until(lambda: "Es war" in chat._raw)
    chat.stop()
    assert not chat.busy
    saved = db.get_messages(chat.chat_id)[-1]
    assert saved["role"] == "assistant" and saved["content"].endswith(" …")


def test_commands(window, db):
    chat = window.chat_tab
    chat.input.setPlainText("/merke Funkenflug: roter Drache von Lira")
    chat.send()
    assert db.search_memories("Funkenflug")[0]["content"] == "roter Drache von Lira"

    chat.input.setPlainText("/erinnere morgen 18:00 Kapitel 3 fertig")
    chat.send()
    reminder = db.list_reminders()[0]
    assert reminder["text"] == "Kapitel 3 fertig"
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert reminder["due_at"] == f"{tomorrow} 18:00:00"
    assert window.reminders_tab.table.rowCount() == 1

    chat.input.setPlainText("/hilfe")
    chat.send()
    assert not chat.busy


def test_failure_shows_error(window, fake):
    fake.script.append({"error": "model 'x' not found", "status": 404})
    chat = window.chat_tab
    chat.input.setPlainText("Hallo")
    chat.send()
    assert wait_until(lambda: not chat.busy)
    assert chat.send_button.isVisibleTo(window)


def test_writing_desk_continue_and_accept(window, db, fake):
    desk = window.writing_tab
    desk.new_story()
    desk.title_edit.setText("Der Drache")
    desk.editor.setPlainText("Lira stand am Ufer.")
    desk.save_current()
    fake.script.append({"content": "Das Wasser glitzerte bedrohlich."})
    desk.run_action("continue")
    assert wait_until(lambda: desk._active is None)
    assert desk.suggestion_view.toPlainText() == "Das Wasser glitzerte bedrohlich."
    desk.accept_suggestion()
    assert desk.editor.toPlainText() == "Lira stand am Ufer.\n\nDas Wasser glitzerte bedrohlich."
    assert db.get_story(desk.story_id)["content"].endswith("bedrohlich.")
    prompt = fake.requests[-1]["messages"][1]["content"]
    assert "Lira stand am Ufer." in prompt and "Gib NUR den neuen Text aus" in prompt


def test_due_reminder_pops_up(window, db):
    db.add_reminder("Trinken!", datetime.now() - timedelta(seconds=5))
    window.check_reminders()
    assert db.list_reminders() == []
    popups = [w for w in QApplication.topLevelWidgets() if w.windowTitle() == "⏰ Connor erinnert dich" and w.isVisible()]
    assert popups
    for popup in popups:
        popup.close()


def test_offline_banner(app, db, monkeypatch):
    from connor import system

    monkeypatch.setattr(system, "try_start_ollama", lambda url: False)
    settings = Settings(ollama_url="http://127.0.0.1:9")
    win = MainWindow(db, settings, make_icon())
    try:
        assert wait_until(lambda: win.chat_tab.banner.text() != "", timeout=8)
        assert "Ollama" in win.chat_tab.banner.text()
    finally:
        win.settings.minimize_to_tray = False
        win.close()


def test_continuation_separator():
    assert continuation_separator("") == ""
    assert continuation_separator("Er ging.") == "\n\n"
    assert continuation_separator("Er ging und") == " "
    assert continuation_separator("Er ging und ") == ""
    assert continuation_separator("Absatz\n") == "\n"
    assert continuation_separator("Absatz\n\n") == ""
