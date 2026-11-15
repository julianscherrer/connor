from datetime import datetime, timedelta

import pytest

from connor import websearch
from connor.engine import ConnorEngine
from connor.llm import CancelToken, ModelNotFound, OllamaClient, OllamaError, OllamaUnavailable, strip_think
from connor.settings import Settings
from connor.tools import ToolExecutor, tool_schemas
from fake_ollama import tool_call


def make_engine(db, fake, **overrides):
    settings = Settings(ollama_url=fake.url, **overrides)
    return ConnorEngine(db, settings), settings


def test_plain_answer_is_streamed(db, fake):
    engine, _ = make_engine(db, fake)
    chat = db.create_chat("Hallo")
    db.add_message(chat, "user", "Hoi Connor!")
    fake.script.append({"content": "Hoi! Schön, dass du da bist."})
    chunks = []
    reply = engine.respond(chat, on_chunk=chunks.append)
    assert reply.text == "Hoi! Schön, dass du da bist."
    assert "".join(chunks) == reply.text
    assert reply.model == "qwen3:8b"

    request = fake.requests[0]
    system = request["messages"][0]
    assert system["role"] == "system" and "Connor" in system["content"] and "save_memory" in system["content"]
    last = request["messages"][-1]
    assert last["role"] == "user" and last["content"].startswith("<kontext>")
    assert last["content"].endswith("\n\nHoi Connor!")
    assert request["options"]["num_ctx"] == 8192
    assert request["keep_alive"] == "30m"
    assert {t["function"]["name"] for t in request["tools"]} >= {"save_memory", "create_reminder", "web_search",
                                                                 "search_documents"}
    assert request["think"] is False


def test_tool_calls_are_executed_and_answer_continues(db, fake):
    engine, _ = make_engine(db, fake)
    chat = db.create_chat("Story", "Drachenreiter")
    db.add_message(chat, "user", "Lira hat übrigens Angst vor Wasser. Und erinnere mich morgen um 18 Uhr ans Kapitel.")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d") + " 18:00"
    fake.script += [
        {"content": "Moment …", "tool_calls": [
            tool_call("save_memory", title="Lira", content="Hat Angst vor Wasser", category="charakter"),
            tool_call("create_reminder", text="Kapitel schreiben", when=tomorrow),
        ]},
        {"content": "Hab ich mir gemerkt und die Erinnerung steht!"},
    ]
    events = []
    reply = engine.respond(chat, on_event=events.append)
    assert reply.text == "Moment …\n\nHab ich mir gemerkt und die Erinnerung steht!"
    assert reply.changes == {"memory", "reminder"}
    assert events == ["🧠 Merke mir: Lira", "⏰ Neue Erinnerung: Kapitel schreiben"]

    memory = db.search_memories("Lira")[0]
    assert memory["project"] == "Drachenreiter" and memory["content"] == "Hat Angst vor Wasser"
    assert db.list_reminders()[0]["due_at"].endswith("18:00:00")

    followup = fake.requests[1]["messages"]
    assert followup[-3]["tool_calls"] and followup[-2]["role"] == "tool"
    assert followup[-2]["content"].startswith("OK: Neu gespeichert") and followup[-1]["content"].startswith("OK:")


def test_relevant_memories_are_injected(db, fake):
    engine, _ = make_engine(db, fake, user_name="Julian")
    db.add_memory("Lira", "Elfe, hat Angst vor Wasser", "charakter", "Drachenreiter", "kanon")
    db.add_memory("Kaffee", "Mag Kaffee beim Schreiben", "vorliebe")
    db.add_memory("Zorg", "Bösewicht aus einer anderen Story", "charakter", "Sternenstaub")
    chat = db.create_chat("x", "Drachenreiter")
    db.add_message(chat, "user", "Was weisst du über Lira?")
    engine.respond(chat)
    system = fake.requests[0]["messages"][0]["content"]
    context = fake.requests[0]["messages"][-1]["content"]
    assert "Lira: Elfe, hat Angst vor Wasser" in context and "Aktuelles Projekt: Drachenreiter" in context
    assert "Kaffee" in system and "Julian" in system
    assert "Zorg" not in system + context

    db.add_message(chat, "assistant", "Lira ist eine Elfe.")
    db.add_message(chat, "user", "Und sonst?")
    engine.respond(chat)
    assert fake.requests[1]["messages"][0]["content"] == system, "system prompt must stay stable for Ollama's cache"


def test_model_without_tools_falls_back(db, fake):
    fake.capabilities = None
    engine, _ = make_engine(db, fake)
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Hallo")
    fake.script += [{"error": "registry.ollama.ai/library/tiny does not support tools", "status": 400},
                    {"content": "Hallo ohne Werkzeuge"}]
    reply = engine.respond(chat)
    assert reply.text == "Hallo ohne Werkzeuge"
    assert "tools" in fake.requests[0] and "tools" not in fake.requests[1]
    assert "/merke" in fake.requests[1]["messages"][0]["content"]

    db.add_message(chat, "user", "Nochmal")
    engine.respond(chat)
    assert "tools" not in fake.requests[2]


def test_capabilities_without_tools_skip_tools(db, fake):
    fake.capabilities = ["completion"]
    engine, _ = make_engine(db, fake)
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Hallo")
    engine.respond(chat)
    assert "tools" not in fake.requests[0]


def test_thinking_is_hidden_and_unsupported_think_retries(db, fake):
    engine, _ = make_engine(db, fake, thinking="off")
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Hallo")
    fake.script += [{"error": '"tiny" does not support thinking', "status": 400},
                    {"content": "<think>hmm, was sage ich</think>Antwort!", "thinking": "intern"}]
    reply = engine.respond(chat)
    assert reply.text == "Antwort!"
    assert fake.requests[0]["think"] is False and "think" not in fake.requests[1]


def test_strip_think():
    assert strip_think("<think>a</think>Hallo") == "Hallo"
    assert strip_think("<think>noch am Denken") == ""
    assert strip_think("Gedanken</think>Antwort") == "Antwort"
    assert strip_think("Normal") == "Normal"


def test_errors_are_friendly(db, fake):
    engine, _ = make_engine(db, fake, model="gibtsnicht:1b")
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Hallo")
    fake.script.append({"error": "model 'gibtsnicht:1b' not found", "status": 404})
    with pytest.raises(ModelNotFound, match="ollama pull gibtsnicht:1b"):
        engine.respond(chat)

    fake.models = []
    engine.settings.model = ""
    with pytest.raises(OllamaError, match="ollama pull"):
        engine.respond(chat)

    with pytest.raises(OllamaUnavailable):
        OllamaClient("http://127.0.0.1:9").list_models(timeout=1)


def test_cancel_stops_streaming(db, fake):
    engine, _ = make_engine(db, fake)
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Erzähl eine lange Geschichte")
    fake.script.append({"content": "Es war einmal " * 200, "delay": 0.01})
    token = CancelToken()
    received = []

    def on_chunk(text):
        received.append(text)
        if len(received) == 5:
            token.cancel()

    reply = engine.respond(chat, on_chunk=on_chunk, token=token)
    assert len(received) < 20
    assert reply.text.startswith("Es war einmal")


def test_history_is_trimmed_to_context(db, fake):
    engine, _ = make_engine(db, fake, context_size=4096)
    chat = db.create_chat("lang")
    for i in range(60):
        db.add_message(chat, "user" if i % 2 == 0 else "assistant", f"Nachricht {i} " + "bla " * 100)
    db.add_message(chat, "user", "Letzte Frage")
    engine.respond(chat)
    messages = fake.requests[0]["messages"]
    assert messages[-1]["content"].endswith("Letzte Frage")
    assert 2 < len(messages) < 20


def test_generate_for_writing(db, fake):
    engine, _ = make_engine(db, fake)
    db.add_memory("Lira", "Elfe", "charakter", "Drachenreiter", "kanon")
    fake.script.append({"content": "Lira sprang ins Wasser – trotz allem."})
    context = engine.writing_context("Drachenreiter", "Lira am See")
    reply = engine.generate(engine.writing_system_prompt(), f"{context}\n\nSchreib weiter")
    assert reply.text == "Lira sprang ins Wasser – trotz allem."
    sent = fake.requests[0]
    assert "tools" not in sent and "Schreibmodus" in sent["messages"][0]["content"]
    assert "Lira: Elfe" in sent["messages"][1]["content"]


def test_web_tools(db, monkeypatch):
    settings = Settings()
    monkeypatch.setattr(websearch, "search", lambda query, **kw: [
        websearch.SearchResult("Langbogen", "https://example.org/langbogen", "Ein Bogen")])
    monkeypatch.setattr(websearch, "fetch_page", lambda url: (url, "Titel", "Der Inhalt"))
    executor = ToolExecutor(db, settings)
    assert "https://example.org/langbogen" in executor.execute("web_search", {"query": "Langbogen"})
    assert "Der Inhalt" in executor.execute("read_webpage", {"url": "https://example.org"})

    asked = []
    settings.web_confirm = True
    denying = ToolExecutor(db, settings, confirm=lambda question: asked.append(question) or False)
    assert "abgelehnt" in denying.execute("web_search", {"query": "Langbogen"})
    assert asked

    settings.web_enabled = False
    assert "ausgeschaltet" in executor.execute("web_search", {"query": "x"})
    assert {t["function"]["name"] for t in tool_schemas(web=False)}.isdisjoint({"web_search", "read_webpage"})


def test_tool_argument_robustness(db):
    executor = ToolExecutor(db, Settings(), now=lambda: datetime(2026, 11, 15, 19, 0))
    assert executor.execute("save_memory", '{"title": "Alpha", "content": "Beta", "category": "quatsch"}').startswith("OK")
    assert db.search_memories("Alpha")[0]["category"] == "notiz"
    assert executor.execute("save_memory", {"title": "", "content": "x"}).startswith("FEHLER")
    assert executor.execute("update_memory", {"memory_id": "#1", "status": "kanon"}).startswith("OK")
    assert executor.execute("delete_memory", {"memory_id": "abc"}).startswith("FEHLER")
    assert executor.execute("create_reminder", {"text": "X", "when": "morgen um 9"}).startswith("OK")
    assert executor.execute("create_reminder", {"text": "X", "when": "irgendwann"}).startswith("FEHLER")
    assert "#1 Mo, 16.11.2026, 09:00 Uhr: X" in executor.execute("list_reminders", {})
    assert executor.execute("delete_reminder", {"reminder_id": 1}).startswith("OK")
    assert executor.execute("unbekannt", {}).startswith("FEHLER")
