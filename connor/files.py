"""Creates files for the user: Word documents (.docx), Markdown and text.
The Word files are built directly (Office Open XML) – no extra package needed."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

FORMATS = ("docx", "md", "txt")
LAYOUTS = ("dokument", "drehbuch")
FILE_EVENT = "📄 Datei erstellt: "

_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def default_folder() -> Path:
    """«Dokumente\\Connor» – also when the Documents folder lives in OneDrive."""
    try:
        from PySide6.QtCore import QStandardPaths

        location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    except Exception:  # noqa: BLE001 - Qt not available: use the usual place
        location = ""
    return (Path(location) if location else Path.home() / "Documents") / "Connor"


def output_folder(custom: str = "") -> Path:
    folder = Path(custom).expanduser() if custom.strip() else default_folder()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def safe_filename(title: str, limit: int = 100) -> str:
    name = " ".join(_INVALID_NAME.sub(" ", title).split()).strip(" .")
    if not name:
        name = "Connor-Dokument"
    if name.split(".")[0].upper() in _RESERVED:
        name = f"_{name}"
    return name[:limit].rstrip(" .")


def unique_path(folder: Path, name: str, extension: str) -> Path:
    candidate = folder / f"{name}.{extension}"
    number = 2
    while candidate.exists():
        candidate = folder / f"{name} ({number}).{extension}"
        number += 1
    return candidate


def title_from_text(text: str, fallback: str = "") -> str:
    """A file name for a chat answer: its first heading, otherwise its first line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    heading = next((line for line in lines if line.startswith("#")), "")
    candidate = heading or (lines[0] if lines else "")
    candidate = re.sub(r"[#*_`>|]", "", candidate).strip(" :-–")
    if len(candidate) > 60:
        candidate = candidate[:60].rsplit(" ", 1)[0]
    return candidate or fallback or f"Connor {datetime.now():%Y-%m-%d %H-%M}"


def _markdown_file_text(content: str, title: str) -> str:
    text = content.strip()
    if title and not text.startswith("#"):
        text = f"# {title}\n\n{text}"
    return text + "\n"


def write_file(path: Path, content: str, *, title: str = "", layout: str = "dokument",
               overwrite: bool = False) -> Path:
    """Writes .docx, .md or .txt depending on the file extension."""
    extension = path.suffix.lower().lstrip(".")
    mode = "w" if overwrite else "x"
    if extension == "docx":
        write_docx(path, content, title=title, layout=layout, overwrite=overwrite)
    elif extension == "md":
        with path.open(mode, encoding="utf-8", newline="\n") as handle:
            handle.write(_markdown_file_text(content, title))
    else:
        with path.open(mode, encoding="utf-8", newline="\r\n") as handle:
            handle.write((f"{title}\n\n" if title else "") + content.strip() + "\n")
    return path


def create_file(folder: Path, title: str, content: str, fmt: str = "docx", layout: str = "dokument") -> Path:
    """Creates a new file – an existing file is never overwritten («Name (2).docx»)."""
    fmt = fmt if fmt in FORMATS else "docx"
    layout = layout if layout in LAYOUTS else "dokument"
    folder.mkdir(parents=True, exist_ok=True)
    for _attempt in range(5):
        path = unique_path(folder, safe_filename(title), fmt)
        try:
            return write_file(path, content, title=title, layout=layout)
        except FileExistsError:
            continue
    raise FileExistsError(f"Konnte keinen freien Dateinamen für «{title}» finden.")


# ------------------------------------------------------------------ Word package parts

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

CONTENT_TYPES = XML_HEAD + (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    '</Types>'
)
ROOT_RELS = XML_HEAD + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
    'Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" '
    'Target="docProps/app.xml"/>'
    '</Relationships>'
)
DOCUMENT_RELS = XML_HEAD + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/>'
    '</Relationships>'
)
APP_PROPS = XML_HEAD + (
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
    '<Application>Connor</Application></Properties>'
)


def _style(style_id: str, name: str, ppr: str = "", rpr: str = "", based_on: str = "Normal",
           kind: str = "paragraph", next_style: str = "") -> str:
    parts = [f'<w:style w:type="{kind}" w:styleId="{style_id}"><w:name w:val="{name}"/>']
    if based_on:
        parts.append(f'<w:basedOn w:val="{based_on}"/>')
    if next_style:
        parts.append(f'<w:next w:val="{next_style}"/>')
    parts.append("<w:qFormat/>")
    if ppr:
        parts.append(f"<w:pPr>{ppr}</w:pPr>")
    if rpr:
        parts.append(f"<w:rPr>{rpr}</w:rPr>")
    parts.append("</w:style>")
    return "".join(parts)


_COURIER = '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/>'
_SCRIPT_SIZE = '<w:sz w:val="24"/><w:szCs w:val="24"/>'
_HEADING_SIZES = {1: 32, 2: 28, 3: 24, 4: 22, 5: 22, 6: 22}

STYLES = XML_HEAD + (
    f'<w:styles xmlns:w="{W_NS}">'
    '<w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="de-CH"/>'
    '</w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
    '</w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    + _style("Title", "Title", '<w:spacing w:after="240"/>',
             '<w:b/><w:color w:val="5B21B6"/><w:sz w:val="48"/><w:szCs w:val="48"/>', next_style="Normal")
    + "".join(
        _style(f"Heading{level}", f"heading {level}",
               f'<w:keepNext/><w:spacing w:before="{280 if level == 1 else 200}" w:after="80"/>'
               f'<w:outlineLvl w:val="{level - 1}"/>',
               f'<w:b/><w:color w:val="{"4C1D95" if level <= 2 else "374151"}"/>'
               f'<w:sz w:val="{_HEADING_SIZES[level]}"/><w:szCs w:val="{_HEADING_SIZES[level]}"/>',
               next_style="Normal")
        for level in range(1, 7)
    )
    + _style("Quote", "Quote", '<w:ind w:left="567" w:right="567"/>', '<w:i/><w:color w:val="4B5563"/>')
    + _style("Code", "Code", '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/>',
             '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="20"/><w:szCs w:val="20"/>')
    + _style("ScriptTitle", "Drehbuch Titel", '<w:spacing w:before="1440" w:after="480"/><w:jc w:val="center"/>',
             f"{_COURIER}<w:b/><w:caps/>{_SCRIPT_SIZE}")
    + _style("ScriptScene", "Drehbuch Szene", '<w:keepNext/><w:spacing w:before="360" w:after="0"/>',
             f"{_COURIER}<w:b/><w:caps/>{_SCRIPT_SIZE}")
    + _style("ScriptAction", "Drehbuch Handlung", '<w:spacing w:before="240" w:after="0"/>',
             f"{_COURIER}{_SCRIPT_SIZE}")
    + _style("ScriptCharacter", "Drehbuch Figur",
             '<w:keepNext/><w:spacing w:before="240" w:after="0"/><w:ind w:left="3168"/>',
             f"{_COURIER}<w:caps/>{_SCRIPT_SIZE}")
    + _style("ScriptParenthetical", "Drehbuch Regieanweisung",
             '<w:keepNext/><w:spacing w:before="0" w:after="0"/><w:ind w:left="2304" w:right="2880"/>',
             f"{_COURIER}{_SCRIPT_SIZE}")
    + _style("ScriptDialogue", "Drehbuch Dialog",
             '<w:spacing w:before="0" w:after="0"/><w:ind w:left="1440" w:right="2160"/>',
             f"{_COURIER}{_SCRIPT_SIZE}")
    + _style("ScriptTransition", "Drehbuch Übergang", '<w:spacing w:before="240" w:after="0"/><w:jc w:val="right"/>',
             f"{_COURIER}<w:caps/>{_SCRIPT_SIZE}")
    + '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
    + "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
              for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    + '</w:tblBorders><w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
    '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
    '</w:styles>'
)


def _core_props(title: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return XML_HEAD + (
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{_xml(title)}</dc:title><dc:creator>Connor</dc:creator>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified>'
        '</cp:coreProperties>'
    )


def _xml(text: str) -> str:
    return escape(_INVALID_XML.sub("", text))


# ------------------------------------------------------------------ reading Markdown and scripts

@dataclass
class Block:
    kind: str
    text: str = ""
    level: int = 0
    marker: str = ""
    rows: list[list[str]] = field(default_factory=list)


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+•]\s+(.*)$")
_NUMBER = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


def _indent(prefix: str) -> int:
    return len(prefix.replace("\t", "    ")) // 2


def parse_markdown(text: str) -> list[Block]:
    blocks: list[Block] = []
    paragraph: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    def flush() -> None:
        if paragraph:
            blocks.append(Block("para", "\n".join(paragraph)))
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append(Block("code", "\n".join(code)))
            i += 1
            continue
        if not stripped:
            flush()
            i += 1
            continue
        heading = _HEADING.match(stripped)
        if heading:
            flush()
            blocks.append(Block("heading", heading.group(2), level=len(heading.group(1))))
        elif _RULE.match(stripped):
            flush()
            blocks.append(Block("rule"))
        elif stripped.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip()
                if not _TABLE_SEPARATOR.match(row):
                    rows.append([cell.strip() for cell in row.strip("|").split("|")])
                i += 1
            if rows:
                blocks.append(Block("table", rows=rows))
            continue
        elif stripped.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(Block("quote", "\n".join(quote)))
            continue
        elif (bullet := _BULLET.match(line)) is not None:
            flush()
            blocks.append(Block("bullet", bullet.group(2), level=_indent(bullet.group(1)), marker="•"))
        elif (number := _NUMBER.match(line)) is not None:
            flush()
            blocks.append(Block("bullet", number.group(3), level=_indent(number.group(1)), marker=f"{number.group(2)}."))
        else:
            paragraph.append(stripped)
        i += 1
    flush()
    return blocks


_SCENE = re.compile(r"^(?:INT|EXT|EST|INT\.?/EXT|I/E|INNEN|AUSSEN|AUẞEN|INNEN/AUSSEN)\b[.\s/–-]", re.I)
_TRANSITION = re.compile(r"^(?:[A-ZÄÖÜ ]+(?:TO|ZU|AUF):|(?:SCHNITT|ÜBERBLENDE|ÜBERBLENDUNG|ABBLENDE|AUFBLENDE|CUT|FADE)\b.*)$")
_CHARACTER = re.compile(r"^@?[A-ZÄÖÜ0-9][A-ZÄÖÜẞ0-9 .'\-]{0,30}(?:\s*\([^)]*\))?$")
_COLON_LINE = re.compile(r"^\*{0,2}([A-ZÄÖÜ][\wÄÖÜäöüß.'\- ]{0,30}?)\*{0,2}\s*:\s*\*{0,2}\s*(.+)$")
_PARENTHETICAL = re.compile(r"^(\([^)]*\))\s*(.*)$")


def _strip_emphasis(text: str) -> str:
    return re.sub(r"^[*_]{1,3}|[*_]{1,3}$", "", text.strip()).strip()


def parse_screenplay(text: str) -> list[Block]:
    """Understands screenplay text (Fountain-like) and «NAME: Dialog» lines as written by language models."""
    blocks: list[Block] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        line = _strip_emphasis(raw)
        i += 1
        if not line:
            continue
        heading = _HEADING.match(raw)
        if heading:
            title = _strip_emphasis(heading.group(2))
            blocks.append(Block("title" if len(heading.group(1)) == 1 and not blocks else "scene", title))
            continue
        if line.startswith(".") and not line.startswith("..") or _SCENE.match(line):
            blocks.append(Block("scene", line.lstrip(".").strip()))
            continue
        if line.startswith(">") or (_TRANSITION.match(line) and line.upper() == line):
            blocks.append(Block("transition", line.lstrip("> ").strip()))
            continue
        colon = _COLON_LINE.match(raw)
        if colon and colon.group(1).split()[0].lower() in {"szene", "akt", "kapitel", "teil", "scene", "act"}:
            blocks.append(Block("scene", line))
            continue
        if colon and len(colon.group(1).split()) <= 3:
            blocks.append(Block("character", colon.group(1).strip()))
            dialogue = _strip_emphasis(colon.group(2))
            parenthetical = _PARENTHETICAL.match(dialogue)
            if parenthetical:
                blocks.append(Block("parenthetical", parenthetical.group(1)))
                dialogue = parenthetical.group(2).strip()
            if dialogue:
                blocks.append(Block("dialogue", dialogue))
            continue
        following = lines[i].strip() if i < len(lines) else ""
        if following and _CHARACTER.match(line) and line.upper() == line and any(c.isalpha() for c in line):
            blocks.append(Block("character", line.lstrip("@")))
            while i < len(lines) and lines[i].strip():
                speech = _strip_emphasis(lines[i].strip())
                kind = "parenthetical" if speech.startswith("(") and speech.endswith(")") else "dialogue"
                blocks.append(Block(kind, speech))
                i += 1
            continue
        blocks.append(Block("action", line))
    return blocks


# ------------------------------------------------------------------ writing Word XML

_INLINE = re.compile(
    r"(\*\*\*[^*\n]+?\*\*\*|\*\*[^\n]+?\*\*|__[^_\n]+?__|(?<![\w*])\*(?![\s*])[^*\n]+?(?<!\s)\*(?![\w*])"
    r"|(?<![\w_])_(?![\s_])[^_\n]+?(?<!\s)_(?![\w_])|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\))"
)


def _run(text: str, *, bold: bool = False, italic: bool = False, code: bool = False) -> str:
    if not text:
        return ""
    props = ('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>' if code else "")
    props += ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "")
    parts = []
    for line_number, line in enumerate(text.split("\n")):
        if line_number:
            parts.append("<w:br/>")
        for chunk_number, chunk in enumerate(line.split("\t")):
            if chunk_number:
                parts.append("<w:tab/>")
            if chunk:
                parts.append(f'<w:t xml:space="preserve">{_xml(chunk)}</w:t>')
    return f"<w:r>{f'<w:rPr>{props}</w:rPr>' if props else ''}{''.join(parts)}</w:r>"


def _runs(text: str, *, bold: bool = False) -> str:
    """Markdown emphasis (**fett**, *kursiv*, `Code`, [Link](url)) as Word runs."""
    out = []
    position = 0
    for match in _INLINE.finditer(text):
        out.append(_run(text[position:match.start()], bold=bold))
        token = match.group(0)
        if token.startswith("***"):
            out.append(_run(token[3:-3], bold=True, italic=True))
        elif token.startswith(("**", "__")):
            out.append(_run(token[2:-2], bold=True))
        elif token.startswith("`"):
            out.append(_run(token[1:-1], code=True, bold=bold))
        elif token.startswith("["):
            label, url = token[1:].split("](", 1)
            out.append(_run(f"{label} ({url[:-1]})" if url.startswith("http") else label, bold=bold))
        else:
            out.append(_run(token[1:-1], italic=True, bold=bold))
        position = match.end()
    out.append(_run(text[position:], bold=bold))
    return "".join(out)


def _paragraph(content: str, style: str = "", *, indent: int = 0, hanging: int = 0, border: bool = False,
               keep_next: bool = False) -> str:
    props = f'<w:pStyle w:val="{style}"/>' if style else ""
    props += "<w:keepNext/>" if keep_next else ""
    props += ('<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="BFBFBF"/></w:pBdr>' if border else "")
    if indent or hanging:
        props += f'<w:ind w:left="{indent}" w:hanging="{hanging}"/>'
    return f"<w:p>{f'<w:pPr>{props}</w:pPr>' if props else ''}{content}</w:p>"


def _table(rows: list[list[str]], width: int) -> str:
    columns = max(len(row) for row in rows)
    column_width = width // columns
    grid = "".join(f'<w:gridCol w:w="{column_width}"/>' for _ in range(columns))
    xml_rows = []
    for row_number, row in enumerate(rows):
        cells = []
        for cell in row + [""] * (columns - len(row)):
            shading = '<w:shd w:val="clear" w:color="auto" w:fill="EDE9FE"/>' if row_number == 0 else ""
            cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{column_width}" w:type="dxa"/>{shading}</w:tcPr>'
                         f"{_paragraph(_runs(cell, bold=row_number == 0))}</w:tc>")
        header = "<w:trPr><w:tblHeader/></w:trPr>" if row_number == 0 else ""
        xml_rows.append(f"<w:tr>{header}{''.join(cells)}</w:tr>")
    return ('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="5000" w:type="pct"/>'
            '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" '
            f'w:noHBand="0" w:noVBand="1"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{"".join(xml_rows)}</w:tbl>')


def _document_body(blocks: list[Block], title: str, text_width: int) -> str:
    body = []
    if blocks and blocks[0].kind == "heading" and blocks[0].level == 1:
        body.append(_paragraph(_runs(blocks[0].text), "Title"))
        blocks = blocks[1:]
    elif title:
        body.append(_paragraph(_run(title), "Title"))
    for block in blocks:
        if block.kind == "heading":
            body.append(_paragraph(_runs(block.text), f"Heading{block.level}"))
        elif block.kind == "bullet":
            indent = 360 + 360 * (block.level + 1)
            body.append(_paragraph(_run(f"{block.marker}\t") + _runs(block.text), indent=indent, hanging=360))
        elif block.kind == "quote":
            body.append(_paragraph(_runs(block.text), "Quote"))
        elif block.kind == "code":
            body += [_paragraph(_run(line), "Code") for line in (block.text.split("\n") or [""])]
        elif block.kind == "rule":
            body.append(_paragraph("", border=True))
        elif block.kind == "table":
            body.append(_table(block.rows, text_width))
            body.append(_paragraph(""))
        else:
            body.append(_paragraph(_runs(block.text)))
    return "".join(body)


_SCRIPT_STYLES = {
    "title": "ScriptTitle", "scene": "ScriptScene", "action": "ScriptAction", "character": "ScriptCharacter",
    "parenthetical": "ScriptParenthetical", "dialogue": "ScriptDialogue", "transition": "ScriptTransition",
}


def _screenplay_body(blocks: list[Block], title: str) -> str:
    body = []
    if title and not (blocks and blocks[0].kind == "title"):
        body.append(_paragraph(_run(title), "ScriptTitle"))
    for block in blocks:
        text = _run(block.text) if block.kind in ("title", "scene", "character", "transition") else _runs(block.text)
        body.append(_paragraph(text, _SCRIPT_STYLES[block.kind]))
    return "".join(body)


A4 = '<w:pgSz w:w="11906" w:h="16838"/>'


def write_docx(path: Path, content: str, *, title: str = "", layout: str = "dokument", overwrite: bool = False) -> None:
    if layout == "drehbuch":
        body = _screenplay_body(parse_screenplay(content), title)
        margins = '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="2160" w:header="708" w:footer="708" w:gutter="0"/>'
    else:
        body = _document_body(parse_markdown(content), title, text_width=11906 - 2 * 1417)
        margins = '<w:pgMar w:top="1417" w:right="1417" w:bottom="1134" w:left="1417" w:header="708" w:footer="708" w:gutter="0"/>'
    document = (XML_HEAD + f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:body>{body or _paragraph("")}'
                f"<w:sectPr>{A4}{margins}</w:sectPr></w:body></w:document>")
    with zipfile.ZipFile(path, "w" if overwrite else "x", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", STYLES)
        archive.writestr("docProps/core.xml", _core_props(title))
        archive.writestr("docProps/app.xml", APP_PROPS)
