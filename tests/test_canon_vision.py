import base64
from datetime import datetime

import pytest

from connor.canon import build_canon_markdown
from connor.engine import ConnorEngine
from connor.llm import OllamaError
from connor.settings import Settings
from fake_ollama import FakeOllama

VISION_CAPS = {"qwen3:8b": ["completion", "tools", "thinking"],
               "qwen3-vl:8b": ["completion", "vision", "tools", "thinking"],
               "llava:7b": ["completion", "vision"]}


def test_canon_bible_groups_and_filters(db):
    db.add_memory("Mania", "Rivalin, kann Gedanken lesen", "charakter", "Comic")
    db.add_memory("Unstoppable", "Kann nicht aufgehalten werden. Origin: Laborunfall", "charakter", "Comic",
                  status="kanon")
    db.add_memory("Zürich 2099", "Stadt unter einer Kuppel", "ort", "Comic", status="offen")
    db.add_memory("Altes Ende", "Alle sterben", "plot", "Comic", status="verworfen")
    db.add_memory("Schreibstil", "Kurze Sprechblasen", "vorliebe", "Comic", status="kanon")
    db.add_memory("Lira", "Elfe", "charakter", "Drachenreiter", status="kanon")

    text = build_canon_markdown(db, "Comic", now=datetime(2025, 3, 1))
    assert text.startswith("# Kanon-Bibel – Comic\n\n*Stand: 01.03.2025.")
    assert text.index("## Figuren") < text.index("## Orte") < text.index("## Notizen")
    assert text.index("### Unstoppable\n") < text.index("### Mania *(Entwurf)*")
    assert "### Zürich 2099 *(noch offen)*" in text and "Origin: Laborunfall" in text
    assert "Kurze Sprechblasen" in text.split("## Notizen")[1]
    assert "Altes Ende" not in text and "Lira" not in text and "## Plot" not in text

    only_canon = build_canon_markdown(db, "Comic", include_drafts=False)
    assert "Unstoppable" in only_canon and "Mania" not in only_canon
    assert "noch nichts gespeichert" in build_canon_markdown(db, "Leer")


@pytest.fixture
def vision_fake():
    with FakeOllama(models=["qwen3:8b", "qwen3-vl:8b"], capabilities=VISION_CAPS) as server:
        yield server


def picture(tmp_path, name="seite1.jpg", data=b"\xff\xd8\xff\xe0 fake jpeg"):
    path = tmp_path / name
    path.write_bytes(data)
    return path, base64.b64encode(data).decode("ascii")


def test_image_goes_to_vision_model(db, vision_fake, tmp_path):
    engine = ConnorEngine(db, Settings(ollama_url=vision_fake.url, model="qwen3:8b"))
    path, encoded = picture(tmp_path)
    chat = db.create_chat("Seite 1", "Comic")
    message = db.add_message(chat, "user", "Wie wirkt die Seite?")
    db.add_message_images(message, [str(path)])
    vision_fake.script.append({"content": "Panel 3 ist super dynamisch!"})
    events = []
    reply = engine.respond(chat, on_event=events.append)

    assert reply.model == "qwen3-vl:8b" and reply.text == "Panel 3 ist super dynamisch!"
    assert "🖼️ Ich schaue mir das Bild mit qwen3-vl:8b an …" in events
    request = vision_fake.requests[-1]
    assert request["model"] == "qwen3-vl:8b" and request["tools"]
    last = request["messages"][-1]
    assert last["images"] == [encoded] and last["content"].endswith("Wie wirkt die Seite?")
    assert all("images" not in m for m in request["messages"][:-1])

    db.add_message(chat, "assistant", reply.text)
    db.add_message(chat, "user", "Und was würdest du ändern?")
    vision_fake.script.append({"content": "Mehr Kontrast."})
    follow_up = engine.respond(chat)
    assert follow_up.model == "qwen3:8b", "without a new picture Connor goes back to the main model"
    messages = vision_fake.requests[-1]["messages"]
    assert all("images" not in m for m in messages)
    assert "[1 Bild(er) angehängt – nicht mehr sichtbar]" in messages[1]["content"]


def test_vision_model_without_tools_and_from_settings(db, tmp_path):
    with FakeOllama(models=["qwen3:8b", "llava:7b"], capabilities=VISION_CAPS) as fake:
        engine = ConnorEngine(db, Settings(ollama_url=fake.url, model="qwen3:8b", vision_model="llava:7b"))
        path, _encoded = picture(tmp_path)
        chat = db.create_chat("Skizze")
        db.add_message_images(db.add_message(chat, "user", "Was siehst du?"), [str(path)])
        reply = engine.respond(chat)
        request = fake.requests[-1]
        assert reply.model == "llava:7b" and request["model"] == "llava:7b"
        assert not request.get("tools") and "save_memory" not in request["messages"][0]["content"]
        assert request["messages"][-1]["images"]


def test_main_model_that_can_see_needs_no_switch(db, vision_fake, tmp_path):
    engine = ConnorEngine(db, Settings(ollama_url=vision_fake.url, model="qwen3-vl:8b"))
    path, _encoded = picture(tmp_path)
    chat = db.create_chat("Seite")
    db.add_message_images(db.add_message(chat, "user", "Was meinst du?"), [str(path)])
    events = []
    reply = engine.respond(chat, on_event=events.append)
    assert reply.model == "qwen3-vl:8b" and not any("🖼️" in e for e in events)


def test_no_vision_model_gives_install_hint(db, fake, tmp_path):
    engine = ConnorEngine(db, Settings(ollama_url=fake.url))
    path, _encoded = picture(tmp_path)
    chat = db.create_chat("Seite")
    db.add_message_images(db.add_message(chat, "user", "Schau mal"), [str(path)])
    with pytest.raises(OllamaError, match="ollama pull qwen3-vl:8b"):
        engine.respond(chat)
    assert not fake.requests


def test_missing_image_file_is_skipped(db, vision_fake, tmp_path):
    engine = ConnorEngine(db, Settings(ollama_url=vision_fake.url, model="qwen3-vl:8b"))
    chat = db.create_chat("Seite")
    db.add_message_images(db.add_message(chat, "user", "Schau mal"), [str(tmp_path / "weg.jpg")])
    engine.respond(chat)
    assert "images" not in vision_fake.requests[-1]["messages"][-1]


def test_deleting_chat_removes_image_links(db, tmp_path):
    chat = db.create_chat("Seite")
    message = db.add_message(chat, "user", "Bild")
    db.add_message_images(message, [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")])
    assert db.message_images(message) == [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
    db.delete_chat(chat)
    assert db.message_images(message) == []


def test_deleting_chat_returns_its_images(db, tmp_path):
    chat = db.create_chat("Seite")
    db.add_message_images(db.add_message(chat, "user", "Bild"), [str(tmp_path / "a.jpg")])
    assert db.delete_chat(chat) == [str(tmp_path / "a.jpg")]
