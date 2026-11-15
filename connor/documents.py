"""Reads your files – Markdown, text, Word, PDF, EPUB, audio … – so Connor can use them."""
from __future__ import annotations

import logging
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import unquote

from .database import Database
from .websearch import decode_bytes, html_to_text

log = logging.getLogger(__name__)

KINDS = {
    "text": {".md", ".markdown", ".txt", ".text", ".rst", ".org", ".csv", ".tsv", ".json", ".yaml", ".yml",
             ".log", ".tex", ".ini", ".fountain"},
    "html": {".html", ".htm", ".xhtml"},
    "docx": {".docx", ".docm", ".dotx"},
    "odt": {".odt", ".ott"},
    "pdf": {".pdf"},
    "epub": {".epub"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac", ".wma", ".webm", ".mp4",
              ".m4v", ".mov", ".mkv"},
}
KIND_LABELS = {
    "text": "📝 Text", "html": "🌐 Webseite", "docx": "📘 Word", "odt": "📘 OpenDocument", "pdf": "📕 PDF",
    "epub": "📚 E-Book", "audio": "🎧 Audio",
}
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
MAX_FILES = 500
MAX_TEXT_BYTES = 30_000_000
MAX_PDF_BYTES = 300_000_000
MAX_XML_BYTES = 150_000_000

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ODF_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_HEADING_STYLE = re.compile(r"(?i)(heading|berschrift|titel|title)\s*(\d?)")


class DocumentError(Exception):
    """A file could not be read. The message is meant for the user."""


Transcribe = Callable[[Path, Callable[[str], None]], str]


def kind_of(path: Path) -> str | None:
    suffix = path.suffix.lower()
    for kind, suffixes in KINDS.items():
        if suffix in suffixes:
            return kind
    return None


def supported_extensions() -> list[str]:
    return sorted(s for suffixes in KINDS.values() for s in suffixes)


def collect_files(paths: Iterable[Path | str], max_files: int = MAX_FILES) -> tuple[list[tuple[Path, str]], list[str]]:
    """Expands folders. Returns ([(file, display name)], [skipped names]).
    The display name keeps the folder structure, e.g. «Notizen/Welt/Orte.md»."""
    files: list[tuple[Path, str]] = []
    skipped: list[str] = []
    seen: set[Path] = set()

    def add(path: Path, display: str) -> None:
        if path in seen:
            return
        seen.add(path)
        if kind_of(path) is None:
            skipped.append(display)
        elif len(files) >= max_files:
            skipped.append(f"{display} (mehr als {max_files} Dateien)")
        else:
            files.append((path, display))

    for raw in paths:
        root = Path(raw)
        if root.is_dir():
            for path in sorted(root.rglob("*"), key=lambda p: str(p).casefold()):
                relative = path.relative_to(root)
                if any(part in SKIP_DIRS or part.startswith("~$") for part in relative.parts):
                    continue
                if path.is_file():
                    add(path, f"{root.name}/{relative.as_posix()}")
        elif root.is_file() and not root.name.startswith("~$"):
            add(root, root.name)
    return files, skipped


def read_document(path: Path, *, transcribe: Transcribe | None = None,
                  progress: Callable[[str], None] | None = None) -> str:
    return normalize(_read_raw(path, transcribe, progress))


def _read_raw(path: Path, transcribe: Transcribe | None, progress: Callable[[str], None] | None) -> str:
    kind = kind_of(path)
    if kind is None:
        raise DocumentError("Dieses Dateiformat kann ich nicht lesen.")
    size = path.stat().st_size
    if kind in ("text", "html") and size > MAX_TEXT_BYTES:
        raise DocumentError("Die Datei ist zu gross (über 30 MB Text).")
    if kind == "pdf" and size > MAX_PDF_BYTES:
        raise DocumentError("Das PDF ist zu gross (über 300 MB).")
    if kind == "text":
        return decode_bytes(path.read_bytes())
    if kind == "html":
        title, text = html_to_text(decode_bytes(path.read_bytes()))
        return f"# {title}\n\n{_paragraphs(text)}" if title else _paragraphs(text)
    if kind == "docx":
        return _docx_text(path)
    if kind == "odt":
        return _odt_text(path)
    if kind == "pdf":
        return _pdf_text(path, progress)
    if kind == "epub":
        return _epub_text(path)
    if transcribe is None:
        raise DocumentError("Für Audio braucht es die Spracherkennung (siehe Einstellungen → Spracherkennung).")
    try:
        return transcribe(path, progress or (lambda _text: None))
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001 - e.g. VoiceError: show it like any other file problem
        raise DocumentError(str(exc) or exc.__class__.__name__) from exc


def _paragraphs(text: str) -> str:
    return "\n\n".join(line for line in text.splitlines() if line.strip())


def _read_zip_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise DocumentError("Die Datei ist beschädigt oder kein gültiges Dokument.") from exc
    if info.file_size > MAX_XML_BYTES:
        raise DocumentError("Das Dokument ist zu gross.")
    return ET.fromstring(archive.read(info))


def _open_zip(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise DocumentError("Die Datei ist beschädigt oder ein altes Format. Speichere sie als .docx/.odt neu.") from exc


def _docx_text(path: Path) -> str:
    with _open_zip(path) as archive:
        root = _read_zip_xml(archive, "word/document.xml")
    paragraphs = []
    for para in root.iter(f"{W}p"):
        parts = []
        for node in para.iter():
            if node.tag == f"{W}t":
                parts.append(node.text or "")
            elif node.tag == f"{W}tab":
                parts.append("\t")
            elif node.tag in (f"{W}br", f"{W}cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if not text:
            continue
        style = para.find(f"{W}pPr/{W}pStyle")
        match = _HEADING_STYLE.search(style.get(f"{W}val", "")) if style is not None else None
        if match:
            text = "#" * int(match.group(2) or 1) + " " + text
        paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _odf_text(element: ET.Element) -> str:
    out = [element.text or ""]
    for child in element:
        if child.tag == f"{ODF_TEXT}s":
            out.append(" " * int(child.get(f"{ODF_TEXT}c", "1")))
        elif child.tag == f"{ODF_TEXT}tab":
            out.append("\t")
        elif child.tag == f"{ODF_TEXT}line-break":
            out.append("\n")
        elif child.tag != f"{ODF_TEXT}note":
            out.append(_odf_text(child))
        out.append(child.tail or "")
    return "".join(out)


def _odt_text(path: Path) -> str:
    with _open_zip(path) as archive:
        root = _read_zip_xml(archive, "content.xml")
    paragraphs = []
    for element in root.iter():
        if element.tag == f"{ODF_TEXT}h":
            text = _odf_text(element).strip()
            if text:
                paragraphs.append("#" * int(element.get(f"{ODF_TEXT}outline-level", "1")) + " " + text)
        elif element.tag == f"{ODF_TEXT}p":
            text = _odf_text(element).strip()
            if text:
                paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _pdf_text(path: Path, progress: Callable[[str], None] | None) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentError("Für PDFs fehlt das Paket «pypdf». Starte update.bat, dann geht's.") from exc
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError("Das PDF ist mit einem Passwort geschützt.")
        total = len(reader.pages)
        pages = []
        for number, page in enumerate(reader.pages, 1):
            if progress and (number == 1 or number % 10 == 0):
                progress(f"📕 Lese {path.name} – Seite {number}/{total} …")
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - one broken page should not stop the rest
                text = ""
            if text.strip():
                pages.append(re.sub(r"(\w)-\n(\w)", r"\1\2", text.strip()))
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"Das PDF konnte nicht gelesen werden ({exc.__class__.__name__}).") from exc
    if not pages:
        raise DocumentError("Kein Text gefunden – vermutlich ein eingescanntes PDF (nur Bilder).")
    return "\n\n".join(pages)


def _epub_text(path: Path) -> str:
    with _open_zip(path) as archive:
        container = _read_zip_xml(archive, "META-INF/container.xml")
        rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
        if rootfile is None:
            raise DocumentError("Das E-Book ist beschädigt.")
        opf_name = rootfile.get("full-path", "")
        opf = _read_zip_xml(archive, opf_name)
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        manifest = {item.get("id"): item.get("href") for item in opf.iterfind(".//opf:manifest/opf:item", ns)}
        base = posixpath.dirname(opf_name)
        parts = []
        for ref in opf.iterfind(".//opf:spine/opf:itemref", ns):
            href = manifest.get(ref.get("idref"))
            if not href:
                continue
            name = posixpath.normpath(posixpath.join(base, unquote(href)))
            try:
                page = decode_bytes(archive.read(name))
            except KeyError:
                continue
            _title, text = html_to_text(page)
            if text.strip():
                parts.append(_paragraphs(text))
    if not parts:
        raise DocumentError("Im E-Book habe ich keinen Text gefunden.")
    return "\n\n".join(parts)


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@dataclass
class ImportResult:
    imported: list[tuple[int, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.imported:
            names = ", ".join(name for _id, name in self.imported[:5])
            more = f" und {len(self.imported) - 5} weitere" if len(self.imported) > 5 else ""
            parts.append(f"📥 {len(self.imported)} Datei(en) gelesen: {names}{more}")
        for name, reason in self.failed[:5]:
            parts.append(f"⚠️ {name}: {reason}")
        if len(self.failed) > 5:
            parts.append(f"⚠️ … und {len(self.failed) - 5} weitere Fehler")
        if self.skipped:
            sample = ", ".join(self.skipped[:3]) + (" …" if len(self.skipped) > 3 else "")
            parts.append(f"⏭️ {len(self.skipped)} Datei(en) übersprungen (Format nicht unterstützt): {sample}")
        return "\n".join(parts) or "Keine Dateien gefunden."


def import_paths(db: Database, paths: Iterable[Path | str], *, project: str = "",
                 transcribe: Transcribe | None = None, progress: Callable[[str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> ImportResult:
    files, skipped = collect_files(paths)
    result = ImportResult(skipped=skipped)
    for index, (path, display) in enumerate(files, 1):
        if should_stop is not None and should_stop():
            break
        if progress:
            progress(f"📥 Lese {display} ({index}/{len(files)}) …")
        try:
            text = read_document(path, transcribe=transcribe, progress=progress)
        except DocumentError as exc:
            result.failed.append((display, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the import
            log.exception("Datei %s konnte nicht gelesen werden", path)
            result.failed.append((display, f"{exc.__class__.__name__}: {exc}"))
            continue
        if not text:
            result.failed.append((display, "Darin habe ich keinen Text gefunden."))
            continue
        doc_id, _created = db.add_document(display, text, path=str(path.resolve()), kind=kind_of(path) or "",
                                           project=project)
        result.imported.append((doc_id, display))
    return result
