import zipfile

import pytest

from connor.database import chunk_text
from connor.documents import DocumentError, collect_files, import_paths, read_document


def make_docx(path, paragraphs):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = ""
    for style, text in paragraphs:
        props = '<w:pPr><w:pStyle w:val="' + style + '"/></w:pPr>' if style else ""
        body += '<w:p>' + props + '<w:r><w:t xml:space="preserve">' + text + '</w:t></w:r></w:p>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {ns}><w:body>{body}</w:body></w:document>')


def make_pdf(path, text):
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))


def test_markdown_and_text(tmp_path):
    md = tmp_path / "welt.md"
    md.write_text("# Welt\n\nEisenfels ist kalt.", encoding="utf-8")
    assert read_document(md) == "# Welt\n\nEisenfels ist kalt."
    legacy = tmp_path / "alt.txt"
    legacy.write_bytes("Grüezi mitenand".encode("cp1252"))
    assert read_document(legacy) == "Grüezi mitenand"


def test_word_document(tmp_path):
    path = tmp_path / "kapitel.docx"
    make_docx(path, [("berschrift1", "Kapitel 1"), (None, "Lira stand am Ufer."), (None, "  "),
                     ("Heading2", "Teil B"), (None, "Funkenflug schnaubte.")])
    assert read_document(path) == "# Kapitel 1\n\nLira stand am Ufer.\n\n## Teil B\n\nFunkenflug schnaubte."


def test_open_document(tmp_path):
    path = tmp_path / "notiz.odt"
    xml = ('<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
           'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>'
           '<text:h text:outline-level="2">Orte</text:h><text:p>Es war<text:s text:c="2"/>einmal.</text:p>'
           '</office:text></office:body></office:document-content>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", xml)
    assert read_document(path) == "## Orte\n\nEs war  einmal."


def test_pdf(tmp_path):
    pytest.importorskip("pypdf")
    path = tmp_path / "buch.pdf"
    make_pdf(path, "Hallo Welt aus dem PDF")
    assert "Hallo Welt aus dem PDF" in read_document(path)


def test_epub(tmp_path):
    path = tmp_path / "buch.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml",
                         '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                         '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        archive.writestr("OEBPS/content.opf",
                         '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                         '<item id="c1" href="kapitel%201.xhtml"/></manifest><spine><itemref idref="c1"/></spine>'
                         '</package>')
        archive.writestr("OEBPS/kapitel 1.xhtml",
                         "<html><body><h1>Kapitel Eins</h1><p>Lira flog über den See.</p></body></html>")
    assert read_document(path) == "Kapitel Eins\n\nLira flog über den See."


def test_problems_are_reported(tmp_path):
    broken = tmp_path / "kaputt.docx"
    broken.write_bytes(b"kein zip")
    with pytest.raises(DocumentError, match="beschädigt"):
        read_document(broken)
    audio = tmp_path / "idee.mp3"
    audio.write_bytes(b"ID3")
    with pytest.raises(DocumentError, match="Spracherkennung"):
        read_document(audio)

    def failing(_path, _progress):
        raise RuntimeError("Mikrofon explodiert")

    with pytest.raises(DocumentError, match="Mikrofon explodiert"):
        read_document(audio, transcribe=failing)


def test_collect_files_walks_folders(tmp_path):
    vault = tmp_path / "Vault"
    (vault / "Welt").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    (vault / "Welt" / "Orte.md").write_text("Eisenfels", encoding="utf-8")
    (vault / "Lira.md").write_text("Elfe", encoding="utf-8")
    (vault / ".obsidian" / "config.json").write_text("{}", encoding="utf-8")
    (vault / "bild.png").write_bytes(b"png")
    (vault / "~$temp.docx").write_bytes(b"lock")
    files, skipped = collect_files([vault])
    assert [name for _path, name in files] == ["Vault/Lira.md", "Vault/Welt/Orte.md"]
    assert skipped == ["Vault/bild.png"]


def test_import_and_reimport(db, tmp_path):
    note = tmp_path / "lira.md"
    note.write_text("Lira ist eine Elfe.", encoding="utf-8")
    (tmp_path / "leer.txt").write_text("   ", encoding="utf-8")
    progress = []
    result = import_paths(db, [note, tmp_path / "leer.txt"], project="Drachenreiter", progress=progress.append)
    assert [name for _id, name in result.imported] == ["lira.md"]
    assert result.failed == [("leer.txt", "Darin habe ich keinen Text gefunden.")]
    assert progress[0].startswith("📥 Lese lira.md (1/2)")
    assert "1 Datei(en) gelesen: lira.md" in result.summary()

    note.write_text("Lira ist eine Elfe mit Bogen.", encoding="utf-8")
    again = import_paths(db, [note])
    assert again.imported[0][0] == result.imported[0][0]
    docs = db.list_documents()
    assert len(docs) == 1 and docs[0]["project"] == "Drachenreiter"
    assert db.search_chunks("Bogen")[0]["name"] == "lira.md"
    assert db.search_chunks("Elfe Bogen")[0]["hits"] == 2


def test_chunks_and_search(db):
    text = "# Orte\n\n" + "\n\n".join(f"Absatz {i} über den Nebelwald und seine Bewohner." for i in range(60))
    chunks = chunk_text(text, size=400)
    assert len(chunks) > 5 and all(len(c) <= 420 for c in chunks)
    assert chunks[1].startswith("(Orte)\n")
    long = chunk_text("Wort " * 1000, size=300)
    assert all(len(c) <= 310 for c in long)

    own, _ = db.add_document("eigen.md", "Der Nebelwald ist dunkel.", project="Drachenreiter")
    other, _ = db.add_document("fremd.md", "Der Nebelwald in der anderen Story.", project="Sternenstaub")
    general, _ = db.add_document("allgemein.md", "Wälder wie der Nebelwald sind feucht.")
    ids = [c["doc_id"] for c in db.search_chunks("Nebelwald", project="Drachenreiter")]
    assert ids == [own, general, other]
    assert [c["doc_id"] for c in db.search_chunks("Nebelwald", doc_ids=[other])] == [other]
    assert other not in [c["doc_id"] for c in db.search_chunks("Nebelwald", exclude=[other])]
    assert db.search_chunks("Nebelwald", doc_ids=[]) == []

    chat = db.create_chat("x")
    db.attach_document(chat, own)
    db.attach_document(chat, own)
    assert [d["id"] for d in db.chat_documents(chat)] == [own]
    db.detach_document(chat, own)
    assert db.chat_documents(chat) == []

    db.attach_document(chat, own)
    assert db.delete_document(own)
    assert db.chat_documents(chat) == []
    assert own not in [c["doc_id"] for c in db.search_chunks("Nebelwald")]
    assert db.update_document(other, project="Neu") and db.get_document(other)["project"] == "Neu"
    assert "Neu" in db.list_projects()


def test_old_database_gets_mode_column(tmp_path):
    import sqlite3

    from connor.database import Database

    path = tmp_path / "v1.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE chats (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT 'x', "
                     "project TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO chats(title, created_at, updated_at) VALUES ('Alt', '2026-01-01', '2026-01-01')")
    database = Database(path)
    assert database.get_chat(1)["mode"] == "writer"
    database.set_chat_mode(1, "assistant")
    assert database.get_chat(1)["mode"] == "assistant"
