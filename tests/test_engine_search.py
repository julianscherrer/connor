"""The search and tool fixes: Connor must not stop after «Ich suche jetzt …»."""
from connor import websearch
from connor.engine import (ConnorEngine, announces_search, clean_query, missing_tool_nudge, query_from_announcement,
                           sanitize_query)
from connor.settings import Settings
from fake_ollama import tool_call


def make_engine(db, fake, **overrides):
    settings = Settings(ollama_url=fake.url, **overrides)
    return ConnorEngine(db, settings)


def fake_web(monkeypatch, searches=None):
    searches = [] if searches is None else searches

    def search(query, **_kwargs):
        searches.append(query)
        return [websearch.SearchResult("WM 2026 – Wikipedia", "https://de.wikipedia.org/wiki/WM_2026",
                                       "Spanien gewinnt das Finale")]

    monkeypatch.setattr(websearch, "search", search)
    monkeypatch.setattr(websearch, "read_top_pages", lambda results, query, **kw: [
        (results[0], "Im Finale von New Jersey gewann Spanien mit 2:1.")])
    return searches


def test_announced_search_is_done_automatically(db, fake, monkeypatch):
    searches = fake_web(monkeypatch)
    engine = make_engine(db, fake)
    chat = db.create_chat("WM", mode="assistant")
    db.add_message(chat, "user", "Wer hat die WM 2026 gewonnen?")
    fake.script += [
        {"content": "Moment, ich schaue kurz im Internet nach!"},
        {"content": "WM 2026 Sieger"},
        {"content": "Spanien hat gewonnen ([Wikipedia](https://de.wikipedia.org/wiki/WM_2026))."},
    ]
    events = []
    reply = engine.respond(chat, on_event=events.append)
    assert searches == ["WM 2026 Sieger"]
    assert events == ["🔎 Suche im Internet: «WM 2026 Sieger»"]
    assert reply.searched
    assert reply.text.endswith("Spanien hat gewonnen ([Wikipedia](https://de.wikipedia.org/wiki/WM_2026)).")
    final = fake.requests[2]["messages"]
    assert final[-2]["tool_calls"][0]["function"]["arguments"] == {"query": "WM 2026 Sieger"}
    assert final[-1]["role"] == "tool" and "gewann Spanien" in final[-1]["content"]
    assert "Suchanfrage" in fake.requests[1]["messages"][-1]["content"]


def test_quoted_announcement_needs_no_extra_question(db, fake, monkeypatch):
    searches = fake_web(monkeypatch)
    engine = make_engine(db, fake)
    chat = db.create_chat("WM")
    db.add_message(chat, "user", "Wer war Weltmeister 2026?")
    fake.script += [{"content": "Ich suche nach «Weltmeister 2026» …"}, {"content": "Spanien."}]
    engine.respond(chat)
    assert searches == ["Weltmeister 2026"]
    assert len(fake.requests) == 2


def test_web_button_searches_first(db, fake, monkeypatch):
    searches = fake_web(monkeypatch)
    engine = make_engine(db, fake)
    chat = db.create_chat("x", mode="assistant")
    db.add_message(chat, "user", "Hey Connor, wer hat eigentlich die WM 2026 gewonnen?")
    fake.script += [{"tool_calls": [tool_call("web_search", query="Fussball WM 2026 Sieger")]},
                    {"content": "Spanien!"}]
    reply = engine.respond(chat, force_web=True)
    assert searches == ["Fussball WM 2026 Sieger"]
    assert reply.text == "Spanien!" and reply.searched
    answer_request = fake.requests[1]["messages"][-1]["content"]
    assert answer_request.startswith("<kontext>") and "<websuche>" in answer_request
    assert answer_request.index("wer hat eigentlich") < answer_request.index("<websuche>")
    assert fake.requests[1]["options"]["temperature"] == 0.5


def test_slash_search_uses_given_query(db, fake, monkeypatch):
    searches = fake_web(monkeypatch)
    engine = make_engine(db, fake)
    chat = db.create_chat("x")
    db.add_message(chat, "user", "/suche Langbogen Geschichte")
    fake.script.append({"content": "Der Langbogen …"})
    engine.respond(chat, force_web=True, web_query="Langbogen Geschichte")
    assert searches == ["Langbogen Geschichte"] and len(fake.requests) == 1


def test_search_errors_reach_the_model(db, fake, monkeypatch):
    def broken(query, **_kwargs):
        raise websearch.WebError("Die Websuche hat nicht geklappt. Ist der PC online?")

    monkeypatch.setattr(websearch, "search", broken)
    engine = make_engine(db, fake)
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Was gibt's Neues?")
    fake.script += [{"content": "News heute"}, {"content": "Sorry, ich komme gerade nicht ins Internet."}]
    reply = engine.respond(chat, force_web=True)
    assert "FEHLER" in fake.requests[1]["messages"][-1]["content"]
    assert reply.text == "Sorry, ich komme gerade nicht ins Internet."


def test_tool_call_written_as_text_is_executed(db, fake):
    engine = make_engine(db, fake)
    chat = db.create_chat("x", "Drachenreiter")
    db.add_message(chat, "user", "Merk dir: Tom ist Schmied.")
    fake.script += [
        {"content": '<tool_call>{"name": "save_memory", "arguments": {"title": "Tom", "content": "Schmied", '
                    '"category": "charakter"}}</tool_call>'},
        {"content": "Hab ich mir gemerkt!"},
    ]
    reply = engine.respond(chat)
    assert db.search_memories("Tom")[0]["content"] == "Schmied"
    assert reply.text == "Hab ich mir gemerkt!"


def test_claimed_memory_without_tool_gets_nudged(db, fake):
    engine = make_engine(db, fake)
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Merk dir bitte: Lira hat rote Haare.")
    fake.script += [
        {"content": "Hab ich mir gemerkt! 🧠"},
        {"tool_calls": [tool_call("save_memory", title="Lira", content="Hat rote Haare", category="charakter")]},
        {"content": "Jetzt wirklich gespeichert."},
    ]
    reply = engine.respond(chat)
    assert db.search_memories("Lira")[0]["content"] == "Hat rote Haare"
    assert "save_memory" in fake.requests[1]["messages"][-1]["content"]
    assert reply.text == "Hab ich mir gemerkt! 🧠\n\nJetzt wirklich gespeichert."
    assert len(fake.requests) == 3


def test_attached_documents_are_in_context(db, fake):
    engine = make_engine(db, fake)
    doc_id, _ = db.add_document("welt.md", "# Welt\n\nIn Eisenfels schneit es immer. Die Schmiede glühen.")
    db.add_document("anderes.md", "Ganz anderes Thema über Kochrezepte.")
    chat = db.create_chat("x")
    db.attach_document(chat, doc_id)
    db.add_message(chat, "user", "Wie ist das Wetter in Eisenfels?")
    engine.respond(chat)
    context = fake.requests[0]["messages"][-1]["content"]
    assert "[Datei: welt.md · ganzes Dokument]" in context and "schneit es immer" in context
    assert "Kochrezepte" not in context


def test_big_attachment_uses_matching_passages(db, fake):
    engine = make_engine(db, fake, context_size=4096)
    filler = "\n\n".join(f"Absatz {i}: Belangloses Geplauder über das Wetter und den Alltag." for i in range(400))
    text = filler + "\n\nDer geheime Schlüssel liegt unter der alten Eiche im Nebelwald.\n\n" + filler
    doc_id, _ = db.add_document("roman.md", text)
    chat = db.create_chat("x")
    db.attach_document(chat, doc_id)
    db.add_message(chat, "user", "Wo liegt der geheime Schlüssel?")
    engine.respond(chat)
    context = fake.requests[0]["messages"][-1]["content"]
    assert "unter der alten Eiche" in context
    assert len(context) < engine.context_budget() + 3000


def test_library_passages_need_matching_words(db, fake):
    engine = make_engine(db, fake)
    db.add_document("orte.md", "Der Nebelwald liegt nördlich von Eisenfels und ist voller Irrlichter.")
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Erzähl mir vom Nebelwald bei Eisenfels")
    engine.respond(chat)
    assert "Irrlichter" in fake.requests[0]["messages"][-1]["content"]

    db.add_message(chat, "assistant", "…")
    db.add_message(chat, "user", "Wie wird das Wetter morgen im Wald?")
    engine.respond(chat)
    assert "Irrlichter" not in fake.requests[1]["messages"][-1]["content"]


def test_modes_change_prompt_and_temperature(db, fake):
    engine = make_engine(db, fake)
    writer = db.create_chat("w", mode="writer")
    helper = db.create_chat("a", mode="assistant")
    for chat in (writer, helper):
        db.add_message(chat, "user", "Hallo")
        engine.respond(chat)
    writer_request, helper_request = fake.requests
    assert "Schreibpartner" in writer_request["messages"][0]["content"]
    assert "Allrounder" in helper_request["messages"][0]["content"]
    assert writer_request["options"]["temperature"] == 0.8
    assert helper_request["options"]["temperature"] == 0.5


def test_warm_up_loads_model_with_same_prompt(db, fake):
    engine = make_engine(db, fake)
    assert engine.warm_up() == "qwen3:8b"
    request = fake.requests[0]
    assert [m["role"] for m in request["messages"]] == ["system"]
    assert request["options"]["num_predict"] == 1 and request["options"]["num_ctx"] == 8192
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Hallo")
    engine.respond(chat)
    assert fake.requests[1]["messages"][0] == request["messages"][0]
    assert fake.requests[1]["tools"] == request["tools"]


def test_announcement_detection():
    for text in ("Moment, ich schaue kurz nach!", "Ich suche jetzt im Internet nach dem Ergebnis.",
                 "Lass mich das kurz recherchieren.", "Ich mache eine kurze Websuche dazu.",
                 "Ich schau mal schnell.", "Okay! Ich werde das online nachschauen."):
        assert announces_search(text), text
    for text in ("Lira ging zum See.", "Die Suche im Wald war lang.", "Spanien hat 2:1 gewonnen.",
                 "x" * 600 + " Ich schaue kurz nach!"):
        assert not announces_search(text), text
    assert query_from_announcement("Ich suche nach «WM 2026 Sieger».") == "WM 2026 Sieger"
    assert query_from_announcement("Ich suche kurz nach.") == ""


def test_nudge_rules():
    assert missing_tool_nudge("Hab ich mir gemerkt!", set(), "Lira hat rote Haare")
    assert missing_tool_nudge("Ist gespeichert ✅", set(), "merk dir das")
    assert not missing_tool_nudge("Hab ich mir gemerkt!", {"save_memory"}, "merk dir das")
    assert not missing_tool_nudge("Ich habe nichts gespeichert.", set(), "merk dir das")
    assert missing_tool_nudge("Klar, ich erinnere dich morgen um 9!", set(), "Erinnere mich morgen an den Bus")
    assert not missing_tool_nudge("«Ich erinnere dich an alles», sagte sie.", set(), "Schreib weiter")


def test_query_helpers():
    assert clean_query("Hey Connor, kannst du mir sagen wer die WM 2026 gewonnen hat?") == \
        "sagen wer die WM 2026 gewonnen hat"
    assert clean_query("<kontext>\nJetzt: …\n</kontext>\n\n/suche Langbogen") == "Langbogen"
    assert sanitize_query("Suchanfrage: «Fussball WM 2026 Sieger»", "x") == "Fussball WM 2026 Sieger"
    assert sanitize_query("", "fallback") == "fallback"
    assert sanitize_query("Das ist eine sehr lange Antwort " * 10, "fallback") == "fallback"


def test_create_file_tool_shows_link(db, fake, tmp_path):
    from pathlib import Path

    from connor.files import FILE_EVENT

    engine = make_engine(db, fake, files_dir=str(tmp_path))
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Mach mir einen Charakterbogen für Lira als Word")
    fake.script += [
        {"tool_calls": [tool_call("create_file", title="Lira – Charakterbogen",
                                  content="# Lira\n\n| Alter | 19 |\n|---|---|\n\n- mutig\n- stur")]},
        {"content": "Fertig – dein Charakterbogen liegt bereit!"},
    ]
    events = []
    reply = engine.respond(chat, on_event=events.append)
    assert events == [FILE_EVENT + str(tmp_path / "Lira – Charakterbogen.docx")]
    assert Path(events[0].removeprefix(FILE_EVENT)).exists()
    assert reply.changes == {"files"}


def test_save_previous_answer_as_word(db, fake, tmp_path):
    from connor.documents import read_document

    engine = make_engine(db, fake, files_dir=str(tmp_path))
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Schreib mir einen Steckbrief für Tom")
    db.add_message(chat, "assistant", "# Tom\n\nSchmied aus Eisenfels, 34 Jahre, brummig aber herzlich.")
    db.add_message(chat, "user", "Speicher das bitte als Word")
    fake.script += [{"tool_calls": [tool_call("create_file", title="Tom – Steckbrief")]}, {"content": "Erledigt!"}]
    engine.respond(chat)
    assert "Schmied aus Eisenfels" in read_document(tmp_path / "Tom – Steckbrief.docx")


def test_claimed_file_without_tool_gets_nudged(db, fake, tmp_path):
    engine = make_engine(db, fake, files_dir=str(tmp_path))
    chat = db.create_chat("x")
    db.add_message(chat, "user", "Erstell mir ein Word mit Liras Steckbrief")
    fake.script += [
        {"content": "Hier ist dein Word-Dokument! 📄"},
        {"tool_calls": [tool_call("create_file", title="Lira",
                                  content="# Lira\n\nElfe, 19 Jahre alt, hat Angst vor Wasser und liebt den Wald.")]},
        {"content": "Jetzt liegt es wirklich bereit."},
    ]
    engine.respond(chat)
    assert (tmp_path / "Lira.docx").exists()
    assert "create_file" in fake.requests[1]["messages"][-1]["content"]
    assert not missing_tool_nudge("Ein Dokument ist eine gespeicherte Datei.", set(), "Was ist ein Dokument?")


def test_tool_call_with_raw_newlines_in_text_is_parsed():
    from connor.llm import parse_text_tool_calls

    raw = '<tool_call>{"name": "create_file", "arguments": {"title": "X", "content": "Zeile 1\nZeile 2"}}</tool_call>'
    calls = parse_text_tool_calls(raw, {"create_file"})
    assert calls[0]["function"]["arguments"]["content"] == "Zeile 1\nZeile 2"


def test_named_character_wins_over_generic_words(db, fake):
    from connor.engine import SOURCES_EVENT

    engine = make_engine(db, fake)
    db.add_memory("Mania – Origin Story", "Origin Story: Mania wurde in einem Labor erschaffen. Ihre Origin "
                  "Story beginnt mit einem Unfall.", "charakter", "Comic")
    db.add_memory("Unstoppable", "Ehemaliger Feuerwehrmann, wurde nach einem Brand unverwundbar.", "charakter", "Comic")
    db.add_memory("Rivalen", "Unstoppable und Mania sind Rivalen seit Heft 1.", "plot", "Comic")
    chat = db.create_chat("x", "Comic")
    db.add_message(chat, "user", "Nenne mir die Origin Story von Unstoppable")
    events = []
    engine.respond(chat, on_event=events.append)
    context = fake.requests[0]["messages"][-1]["content"]
    assert "Gefragt ist nach: Unstoppable" in context
    assert "Feuerwehrmann" in context and "Rivalen" in context
    assert "Labor" not in context, "Mania's story must not be handed over for Unstoppable"
    assert events[0].startswith(SOURCES_EVENT) and "Unstoppable" in events[0] and "Mania" not in events[0]
    assert "erfinde nichts" in fake.requests[0]["messages"][0]["content"]


def test_unknown_name_gets_no_foreign_story(db, fake):
    engine = make_engine(db, fake)
    db.add_memory("Mania", "Origin Story: im Labor erschaffen.", "charakter", "Comic")
    db.add_memory("Unstoppable", "Ex-Feuerwehrmann.", "charakter", "Comic")
    chat = db.create_chat("x", "Comic")
    db.add_message(chat, "user", "Wie geht die Origin Story von Unstoppable?")
    engine.respond(chat)
    context = fake.requests[0]["messages"][-1]["content"]
    assert "Ex-Feuerwehrmann" in context and "Labor" not in context
