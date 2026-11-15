"""Drives the real window (offscreen) against the fake Ollama server."""
import time
from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from connor import voice, websearch  # noqa: E402
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
    assert wait_until(lambda: win._warmed_key is not None), "model warm-up should finish"
    fake.requests.clear()
    yield win
    win.settings.minimize_to_tray = False
    win.close()


def test_startup_detects_model_and_warms_up(window):
    assert window.settings.model == "qwen3:8b"
    assert "qwen3:8b" in window.chat_tab.status.text()
    assert not window.chat_tab.banner.isVisible()
    assert [window.tabs.tabText(i).split()[-1] for i in range(window.tabs.count())] == [
        "Chat", "Schreibwerkstatt", "Dokumente", "Gedächtnis", "Erinnerungen"]


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


def test_modes(window, db, fake):
    chat = window.chat_tab
    chat.set_mode("assistant")
    assert window.settings.default_mode == "assistant"
    assert "Allrounder" in chat.view._welcome.text()
    assert chat.chips.itemAt(0).widget().text() == "💡 Erklär mir"
    chat.input.setPlainText("Was ist ein Langbogen?")
    chat.send()
    assert wait_until(lambda: not chat.busy)
    assert db.get_chat(chat.chat_id)["mode"] == "assistant"
    assert "Allrounder" in fake.requests[-1]["messages"][0]["content"]

    chat.set_mode("writer")
    assert db.get_chat(chat.chat_id)["mode"] == "writer"
    assert window.settings.default_mode == "assistant", "switching an existing chat keeps the default"
    chat.new_chat()
    assert chat.mode_buttons["assistant"].isChecked()


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


def test_web_button_forces_search(window, db, fake, monkeypatch):
    searches = []
    monkeypatch.setattr(websearch, "search", lambda query, **kw: searches.append(query) or [
        websearch.SearchResult("Quelle", "https://quelle.example", "Snippet")])
    monkeypatch.setattr(websearch, "read_top_pages", lambda results, query, **kw: [])
    chat = window.chat_tab
    chat.web_button.setChecked(True)
    assert "Websuche ist an" in chat.status.text()
    fake.script += [{"content": "Wetter Zürich morgen"}, {"content": "Morgen wird es sonnig ([Quelle](https://quelle.example))."}]
    chat.input.setPlainText("Wie wird das Wetter morgen in Zürich?")
    chat.send()
    assert wait_until(lambda: not chat.busy)
    assert searches == ["Wetter Zürich morgen"]
    roles = [m["role"] for m in db.get_messages(chat.chat_id)]
    assert roles == ["user", "event", "assistant"]

    window.settings.web_enabled = False
    chat.apply_settings()
    assert not chat.web_button.isChecked() and not chat.web_button.isEnabled()


def test_files_in_chat(window, db, fake, tmp_path):
    note = tmp_path / "welt.md"
    note.write_text("# Welt\n\nIn Eisenfels schneit es das ganze Jahr.", encoding="utf-8")
    chat = window.chat_tab
    window.handle_dropped([str(note)])
    assert wait_until(lambda: chat._import_worker is None)
    assert chat.attachment_bar.isVisibleTo(window) and chat.pending_docs
    chat.input.setPlainText("Wie ist das Klima in Eisenfels?")
    chat.send()
    assert wait_until(lambda: not chat.busy)
    assert [d["name"] for d in db.chat_documents(chat.chat_id)] == ["welt.md"]
    assert db.get_messages(chat.chat_id)[0]["content"] == "📎 Angehängt: welt.md"
    assert "schneit es das ganze Jahr" in fake.requests[-1]["messages"][-1]["content"]
    assert window.documents_tab.table.rowCount() == 1

    doc_id = db.chat_documents(chat.chat_id)[0]["id"]
    chat.detach(doc_id)
    assert db.chat_documents(chat.chat_id) == [] and not chat.attachment_bar.isVisibleTo(window)


def test_drop_on_documents_tab(window, db, tmp_path):
    folder = tmp_path / "Notizen"
    folder.mkdir()
    (folder / "a.md").write_text("Alpha", encoding="utf-8")
    (folder / "b.txt").write_text("Beta", encoding="utf-8")
    (folder / "bild.png").write_bytes(b"png")
    window.tabs.setCurrentWidget(window.memory_tab)
    window.show_drop_overlay()
    assert window.drop_overlay.isVisible() and "Dokumente" in window.drop_overlay.text()
    window.drop_overlay.hide()
    window.drop_overlay.dropped.emit([str(folder)])
    assert window.tabs.currentWidget() is window.memory_tab, "handled after the drop finished"
    assert wait_until(lambda: window.tabs.currentWidget() is window.documents_tab)
    assert wait_until(lambda: window.documents_tab._worker is None)
    assert window.documents_tab.table.rowCount() == 2
    assert sorted(d["name"] for d in db.list_documents()) == ["Notizen/a.md", "Notizen/b.txt"]


def test_open_file_as_story(window, db, tmp_path):
    draft = tmp_path / "Kapitel 1.md"
    draft.write_text("Lira stand am Ufer.", encoding="utf-8")
    desk = window.writing_tab
    desk._open_as_story(draft)
    assert wait_until(lambda: desk._import_worker is None)
    assert desk.title_edit.text() == "Kapitel 1"
    assert desk.editor.toPlainText() == "Lira stand am Ufer."


class FakeRecorder:
    def __init__(self):
        self.level = 0.05

    def start(self):
        pass

    @property
    def seconds(self):
        return 2.0

    def stop(self):
        import numpy

        return numpy.full(16000, 0.1, dtype=numpy.float32)

    def cancel(self):
        pass


def test_dictation(window, monkeypatch):
    pytest.importorskip("numpy")
    monkeypatch.setattr(voice, "missing_packages", lambda need_mic=True: [])
    monkeypatch.setattr(voice, "Recorder", FakeRecorder)
    monkeypatch.setattr(window.transcriber, "transcribe", lambda audio, **kwargs: "Hallo Welt")
    chat = window.chat_tab
    chat.input.setPlainText("Text:")
    chat.mic_button.click()
    assert chat.mic_button.busy and chat.mic_button.text().startswith("⏹ 0:02")
    chat.mic_button.click()
    assert wait_until(lambda: not chat.mic_button.busy)
    assert chat.input.toPlainText() == "Text: Hallo Welt"

    desk = window.writing_tab
    desk.editor.setPlainText("Es war")
    desk.editor.moveCursor(desk.editor.textCursor().MoveOperation.End)
    desk.mic_button.click()
    desk.mic_button.click()
    assert wait_until(lambda: not desk.mic_button.busy)
    assert desk.editor.toPlainText() == "Es war Hallo Welt"


def test_dictation_not_installed(window, monkeypatch):
    monkeypatch.setattr(voice, "missing_packages", lambda need_mic=True: ["faster_whisper"])
    messages = []
    window.chat_tab.mic_button.failed.connect(messages.append)
    window.chat_tab.mic_button.click()
    assert messages == [voice.INSTALL_HINT] and not window.chat_tab.mic_button.busy


def test_failure_shows_error(window, fake):
    fake.script.append({"error": "model 'x' not found", "status": 404})
    chat = window.chat_tab
    chat.input.setPlainText("Hallo")
    chat.send()
    assert wait_until(lambda: not chat.busy)
    assert chat.send_button.isVisibleTo(window)


def test_writing_desk_continue_and_accept(window, db, fake):
    db.add_document("welt.md", "Der Silbersee ist eiskalt und voller Irrlichter.", project="Drachenreiter")
    desk = window.writing_tab
    desk.new_story()
    desk.title_edit.setText("Der Drache")
    desk.project_box.setCurrentText("Drachenreiter")
    desk.editor.setPlainText("Lira stand am Ufer vom Silbersee.")
    desk.save_current()
    fake.script.append({"content": "Das Wasser glitzerte bedrohlich."})
    desk.run_action("continue")
    assert wait_until(lambda: desk._active is None)
    assert desk.suggestion_view.toPlainText() == "Das Wasser glitzerte bedrohlich."
    desk.accept_suggestion()
    assert desk.editor.toPlainText() == "Lira stand am Ufer vom Silbersee.\n\nDas Wasser glitzerte bedrohlich."
    assert db.get_story(desk.story_id)["content"].endswith("bedrohlich.")
    prompt = fake.requests[-1]["messages"][1]["content"]
    assert "Lira stand am Ufer" in prompt and "Gib NUR den neuen Text aus" in prompt
    assert "voller Irrlichter" in prompt, "project files are used while writing"


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


def test_save_answer_as_word_and_export_story(window, db, fake, tmp_path):
    from connor.documents import read_document
    from connor.gui.widgets import MessageBubble

    window.settings.files_dir = str(tmp_path / "Connor")
    fake.script.append({"content": "# Lira\n\n**Alter:** 19\n\n- mutig\n- stur"})
    chat = window.chat_tab
    chat.input.setPlainText("Steckbrief für Lira bitte")
    chat.send()
    assert wait_until(lambda: not chat.busy)
    bubble = [b for b in chat.view.findChildren(MessageBubble) if b.role == "assistant"][-1]
    bubble.save_button.click()
    saved = tmp_path / "Connor" / "Lira.docx"
    assert saved.exists() and "Alter: 19" in read_document(saved)
    assert db.get_messages(chat.chat_id)[-1]["content"].endswith("Lira.docx")
    labels = [label.text() for label in chat.view.findChildren(QLabel)]
    assert any("Ordner öffnen" in text and "Lira.docx" in text for text in labels)

    desk = window.writing_tab
    desk.new_story()
    desk.title_edit.setText("Der Drache")
    desk.editor.setPlainText("Erster Absatz.\n\nZweiter Absatz.")
    desk.save_current()
    target = desk.export_to(tmp_path / "Export.docx")
    assert read_document(target) == "# Der Drache\n\nErster Absatz.\n\nZweiter Absatz."


def test_project_is_chosen_automatically(window, db, fake):
    db.add_memory("Lira", "Elfe", "charakter", "Drachenreiter", "kanon")
    db.add_memory("Zorg", "Roboter", "charakter", "Sternenstaub", "kanon")
    chat = window.chat_tab
    chat.input.setPlainText("Was würde Lira jetzt tun?")
    chat.send()
    assert wait_until(lambda: not chat.busy)
    assert chat.project() == "Drachenreiter" and db.get_chat(chat.chat_id)["project"] == "Drachenreiter"
    messages = db.get_messages(chat.chat_id)
    assert messages[0]["role"] == "event" and "Drachenreiter" in messages[0]["content"]
    context = fake.requests[-1]["messages"][-1]["content"]
    assert "Lira: Elfe" in context and "Zorg" not in context


def test_canon_check_and_backup(window, db, fake, tmp_path):
    db.add_memory("Lira", "Elfe mit grünen Augen", "charakter", "Drachenreiter", "kanon")
    desk = window.writing_tab
    desk.new_story()
    desk.project_box.setCurrentText("Drachenreiter")
    desk.editor.setPlainText("Lira blinzelte mit ihren blauen Augen.")
    fake.script.append({"content": "- «blauen Augen» – laut Erinnerung Lira hat sie grüne Augen."})
    desk.run_action("canon")
    assert wait_until(lambda: desk._active is None)
    assert "grüne Augen" in desk.suggestion_view.toPlainText()
    request = fake.requests[-1]
    assert request["options"]["temperature"] == 0.2
    assert "Elfe mit grünen Augen" in request["messages"][1]["content"]
    assert not desk.accept_button.isVisible()

    window.settings.backup_dir = str(tmp_path / "backups")
    window.run_backup()
    assert wait_until(lambda: not window._backing_up)
    assert list((tmp_path / "backups").glob("connor-*.db"))


def make_picture(path, width=2400, height=1200):
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(200, 30, 30, 128))
    assert image.save(str(path))
    return path


def test_picture_in_chat(window, db, fake, tmp_path, monkeypatch):
    from pathlib import Path

    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QMessageBox, QPushButton

    from connor.gui.images import images_dir
    from connor.gui.widgets import MessageBubble

    fake.models.append("qwen3-vl:8b")
    fake.capabilities = {"qwen3:8b": ["completion", "tools"], "qwen3-vl:8b": ["completion", "vision", "tools"]}
    window.engine._capabilities.clear()
    page = make_picture(tmp_path / "seite1.png")
    broken = tmp_path / "kaputt.jpg"
    broken.write_bytes(b"kein Bild")

    window.tabs.setCurrentWidget(window.writing_tab)
    window.handle_dropped([str(page), str(broken)])
    chat = window.chat_tab
    assert window.tabs.currentWidget() is chat, "pictures are looked at in the chat"
    assert [name for name, _path in chat.pending_images] == ["seite1.png"]
    prepared = Path(chat.pending_images[0][1])
    assert prepared.parent == images_dir() and prepared.suffix == ".jpg"
    assert QImage(str(prepared)).width() == 1600, "big scans are scaled down"
    chips = [b.text() for b in chat.attachment_bar.findChildren(QPushButton) if b.isVisibleTo(window)]
    assert any("seite1.png" in text for text in chips) and chat.attachment_bar.isVisibleTo(window)

    fake.script.append({"content": "Starke Seite! Panel 2 knallt."})
    chat.send()
    assert wait_until(lambda: not chat.busy)
    request = fake.requests[-1]
    assert request["model"] == "qwen3-vl:8b" and len(request["messages"][-1]["images"]) == 1
    assert request["messages"][-1]["content"].endswith("Was sagst du dazu?")
    assert not chat.pending_images and not chat.attachment_bar.isVisibleTo(window)
    events = [m["content"] for m in db.get_messages(chat.chat_id) if m["role"] == "event"]
    assert any("qwen3-vl:8b" in text for text in events)
    bubble = [b for b in chat.view.findChildren(MessageBubble) if b.role == "user"][-1]
    assert bubble.images == [str(prepared)]

    chat_id = chat.chat_id
    chat.new_chat()
    chat.open_chat(chat_id)

    def shown_user_bubbles():
        return [b for b in chat.view.findChildren(MessageBubble) if b.role == "user" and b.isVisibleTo(window)]

    assert wait_until(lambda: shown_user_bubbles())
    bubble = shown_user_bubbles()[-1]
    assert bubble.images == [str(prepared)]

    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kw: QMessageBox.StandardButton.Yes)
    chat.delete_chat(chat_id)
    assert not prepared.exists() and page.exists(), "only Connor's copy is deleted"


def test_remove_picture_before_sending(window, tmp_path):
    chat = window.chat_tab
    chat.add_files([str(make_picture(tmp_path / "skizze.png", 300, 200))])
    assert len(chat.pending_images) == 1
    chat.remove_image(0)
    assert not chat.pending_images and not chat.attachment_bar.isVisibleTo(window)


def test_canon_bible_and_comic_export(window, db, tmp_path):
    from connor.documents import read_document

    db.add_memory("Unstoppable", "Origin: Laborunfall in Zürich", "charakter", "Comic", "kanon")
    db.add_memory("Mania", "Rivalin", "charakter", "Comic")
    target = window.memory_tab.export_canon_to("Comic", tmp_path / "Kanon.docx")
    text = read_document(target)
    assert text.startswith("# Kanon-Bibel – Comic") and "## Figuren" in text
    assert "Laborunfall" in text and "Mania (Entwurf)" in text
    markdown = window.memory_tab.export_canon_to("Comic", tmp_path / "Kanon.md")
    assert markdown.read_text(encoding="utf-8").startswith("# Kanon-Bibel – Comic")

    desk = window.writing_tab
    desk.new_story()
    desk.title_edit.setText("Unstoppable #1")
    desk.editor.setPlainText("SEITE 1\n\nPANEL 1\nEine Stadt bei Nacht.\nUNSTOPPABLE: Los geht's.")
    desk.save_current()
    comic = desk.export_to(tmp_path / "Comic.docx", layout="comic")
    import zipfile

    with zipfile.ZipFile(comic) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    assert '<w:pStyle w:val="ComicLettering"/>' in document and "UNSTOPPABLE: " in document

def test_settings_dialog_vision_model(window, fake):
    from connor.gui.settings_dialog import SettingsDialog

    fake.models.append("qwen3-vl:8b")
    window.settings.vision_model = "llava:7b"
    dialog = SettingsDialog(window.settings, window)
    assert dialog.vision_model.currentText() == "llava:7b"
    dialog.test_connection()
    items = [dialog.vision_model.itemText(i) for i in range(dialog.vision_model.count())]
    assert "qwen3-vl:8b" in items and dialog.vision_model.currentText() == "llava:7b"
    dialog.vision_model.setCurrentText("qwen3-vl:8b")
    dialog.apply()
    assert window.settings.vision_model == "qwen3-vl:8b" and window.settings.model == "qwen3:8b"
    dialog.vision_model.setCurrentText("  ")
    dialog.apply()
    assert window.settings.vision_model == ""
    dialog.deleteLater()