import xml.etree.ElementTree as ET
import zipfile

from connor import files
from connor.documents import read_document
from connor.settings import Settings
from connor.tools import ToolExecutor

SHEET = """# Lira – Charakterbogen

**Rolle:** Heldin · *Elfe*

## Steckbrief

| Merkmal | Wert |
|---|---|
| Alter | 19 |
| Angst | Wasser & Tiefe <3 |

## Stärken
- Bogenschiessen
  - auch im Dunkeln
1. Mut
2. Treue

> «Ich gehe da nicht rein.»

---

```
Notiz: kein Code
```
"""

SCRIPT = """# Das Ufer

INT. TAVERNE – NACHT

Lira sitzt allein am Fenster. Regen.

TOM
(leise)
Du bist nass geworden.

**Lira:** (zögert) Ich war nicht im Wasser.

SCHNITT ZU:

Szene 2: Am See
"""


def parts_of(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in names:
            if name.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(name))
        return names, archive.read("word/document.xml").decode("utf-8")


def test_character_sheet_as_word(tmp_path):
    path = files.create_file(tmp_path, "Lira – Charakterbogen", SHEET)
    assert path.name == "Lira – Charakterbogen.docx"
    names, document = parts_of(path)
    assert names[0] == "[Content_Types].xml"
    assert {"word/document.xml", "word/styles.xml", "_rels/.rels", "word/_rels/document.xml.rels"} <= set(names)
    assert '<w:pStyle w:val="Title"/>' in document and '<w:pStyle w:val="Heading2"/>' in document
    assert "<w:tbl>" in document and "Wasser &amp; Tiefe &lt;3" in document
    assert "<w:b/>" in document and "<w:i/>" in document
    text = read_document(path)
    assert text.startswith("# Lira – Charakterbogen")
    for expected in ("Rolle: Heldin · Elfe", "## Steckbrief", "Alter", "19", "•\tBogenschiessen",
                     "1.\tMut", "«Ich gehe da nicht rein.»", "Notiz: kein Code"):
        assert expected in text, expected


def test_script_layout(tmp_path):
    path = files.create_file(tmp_path, "Szene 1", SCRIPT, layout="drehbuch")
    _names, document = parts_of(path)
    blocks = files.parse_screenplay(SCRIPT)
    assert [(b.kind, b.text) for b in blocks] == [
        ("title", "Das Ufer"),
        ("scene", "INT. TAVERNE – NACHT"),
        ("action", "Lira sitzt allein am Fenster. Regen."),
        ("character", "TOM"),
        ("parenthetical", "(leise)"),
        ("dialogue", "Du bist nass geworden."),
        ("character", "Lira"),
        ("parenthetical", "(zögert)"),
        ("dialogue", "Ich war nicht im Wasser."),
        ("transition", "SCHNITT ZU:"),
        ("scene", "Szene 2: Am See"),
    ]
    assert document.count('<w:pStyle w:val="ScriptCharacter"/>') == 2
    assert '<w:pStyle w:val="ScriptDialogue"/>' in document and 'w:left="2160"' in document


def test_markdown_and_text_files(tmp_path):
    md = files.create_file(tmp_path, "Notiz", "Nur Text", fmt="md")
    assert md.read_text(encoding="utf-8") == "# Notiz\n\nNur Text\n"
    txt = files.create_file(tmp_path, "Notiz", "Zeile", fmt="txt")
    assert txt.read_bytes() == b"Notiz\r\n\r\nZeile\r\n"


def test_never_overwrites_and_safe_names(tmp_path):
    first = files.create_file(tmp_path, 'Kapitel 1: "Anfang"?', "Text")
    second = files.create_file(tmp_path, 'Kapitel 1: "Anfang"?', "Text")
    assert first.name == "Kapitel 1 Anfang.docx" and second.name == "Kapitel 1 Anfang (2).docx"
    assert files.safe_filename("CON") == "_CON"
    assert files.safe_filename("  ...  ") == "Connor-Dokument"
    assert len(files.safe_filename("x" * 300)) == 100


def test_invalid_characters_are_removed(tmp_path):
    path = files.create_file(tmp_path, "Steuerzeichen", "Hallo\x00 Welt\x07 und\ttab")
    _names, document = parts_of(path)
    assert "\x00" not in document and "<w:tab/>" in document


def test_title_from_text():
    assert files.title_from_text("Klar!\n\n## **Lira – Charakterbogen**\n\nText") == "Lira – Charakterbogen"
    assert files.title_from_text("Hier ist eine sehr lange erste Zeile, die als Dateiname viel zu lang wäre, "
                                 "ehrlich") == "Hier ist eine sehr lange erste Zeile, die als Dateiname"
    assert files.title_from_text("", "Fallback") == "Fallback"


def test_overwrite_on_export(tmp_path):
    target = tmp_path / "Geschichte.docx"
    files.write_file(target, "Version 1", title="G")
    files.write_file(target, "Version 2\n\n* * *\n\nEnde", title="G", overwrite=True)
    assert "Version 2" in read_document(target)


def test_tool_creates_file_and_uses_previous_answer(db, tmp_path):
    settings = Settings(files_dir=str(tmp_path / "Connor"))
    executor = ToolExecutor(db, settings, fallback_content=lambda: "# Tom\n\nSchmied aus Eisenfels, 34 Jahre alt.")
    result = executor.execute("create_file", {"title": "Tom", "content": SHEET, "format": "docx"})
    assert result.startswith("OK: Datei erstellt: ") and result.endswith("Tom.docx")
    assert executor.changed == {"files"}

    result = executor.execute("create_file", {"title": "Tom", "content": "siehe oben"})
    path = result.removeprefix("OK: Datei erstellt: ")
    assert path.endswith("Tom (2).docx") and "Schmied aus Eisenfels" in read_document(files.Path(path))

    empty = ToolExecutor(db, settings, fallback_content=lambda: "")
    assert empty.execute("create_file", {"title": "Leer"}).startswith("FEHLER")
    assert executor.execute("create_file", {"title": "Notiz", "content": SHEET, "format": "pdf"}).endswith(".docx")
