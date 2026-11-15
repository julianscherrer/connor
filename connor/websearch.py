"""Web search and page reading – the only part of Connor that talks to the internet."""
from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
CONNOR_UA = "Connor/1.0 (private local assistant)"
ACCEPT_LANGUAGE = "de-CH,de;q=0.9,en;q=0.7"
TIMEOUT = 15
MAX_DOWNLOAD = 3_000_000


class WebError(Exception):
    """A problem with web search or page loading that can be shown to the user."""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


def search(query: str, provider: str = "duckduckgo", searxng_url: str = "",
           max_results: int = 6) -> list[SearchResult]:
    query = " ".join(query.split())
    if not query:
        raise WebError("Die Suchanfrage ist leer.")
    if provider == "searxng":
        return _search_searxng(query, searxng_url, max_results)

    errors: list[str] = []
    engines = (_search_ddg_html, _search_ddg_lite, _search_wikipedia)
    for engine in engines:
        try:
            results = engine(query, max_results)
        except Exception as exc:  # noqa: BLE001 - try the next engine
            log.info("Suche über %s fehlgeschlagen: %s", engine.__name__, exc)
            errors.append(str(exc))
            continue
        if results:
            return results[:max_results]
    if len(errors) == len(engines):
        raise WebError(f"Die Websuche hat nicht geklappt ({errors[0]}). Ist der PC online?")
    return []


def _headers(user_agent: str = BROWSER_UA) -> dict[str, str]:
    return {"User-Agent": user_agent, "Accept-Language": ACCEPT_LANGUAGE}


def resolve_ddg_url(href: str) -> str | None:
    """DuckDuckGo wraps result links. Returns the real target or None for ads/internal links."""
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://duckduckgo.com" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com"):
        if parsed.path.startswith("/l/"):
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            return target if target.startswith(("http://", "https://")) else None
        return None
    return href if parsed.scheme in ("http", "https") else None


class _ResultParser(HTMLParser):
    TITLE_CLASSES = {"result__a", "result-link"}
    SNIPPET_CLASSES = {"result__snippet", "result-snippet"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._mode: str | None = None
        self._tag = ""
        self._depth = 0
        self._href = ""
        self._buf: list[str] = []
        self._snippet_allowed = False

    def handle_starttag(self, tag, attrs):
        if self._mode:
            if tag == self._tag:
                self._depth += 1
            return
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "a" and classes & self.TITLE_CLASSES:
            self._begin("title", tag, attributes.get("href") or "")
        elif classes & self.SNIPPET_CLASSES:
            self._begin("snippet", tag, "")

    def _begin(self, mode: str, tag: str, href: str) -> None:
        self._mode, self._tag, self._depth, self._href, self._buf = mode, tag, 0, href, []

    def handle_endtag(self, tag):
        if not self._mode or tag != self._tag:
            return
        if self._depth:
            self._depth -= 1
            return
        text = " ".join("".join(self._buf).split())
        if self._mode == "title":
            url = resolve_ddg_url(self._href)
            self._snippet_allowed = bool(url and text)
            if self._snippet_allowed:
                self.results.append(SearchResult(text, url))
        elif self._snippet_allowed and text and self.results and not self.results[-1].snippet:
            self.results[-1].snippet = text
        self._mode = None

    def handle_data(self, data):
        if self._mode:
            self._buf.append(data)


def parse_ddg_results(page: str) -> list[SearchResult]:
    parser = _ResultParser()
    parser.feed(page)
    parser.close()
    seen: set[str] = set()
    unique = []
    for result in parser.results:
        if result.url not in seen:
            seen.add(result.url)
            unique.append(result)
    return unique


def _ddg_request(url: str, query: str) -> str:
    resp = requests.post(url, data={"q": query, "kl": "ch-de"}, headers=_headers(), timeout=TIMEOUT)
    if resp.status_code != 200:
        raise WebError(f"DuckDuckGo antwortet mit Status {resp.status_code}")
    return resp.text


def _search_ddg_html(query: str, max_results: int) -> list[SearchResult]:
    page = _ddg_request("https://html.duckduckgo.com/html/", query)
    results = parse_ddg_results(page)
    if not results and "anomaly" in page.lower():
        raise WebError("DuckDuckGo blockiert die Anfrage gerade")
    return results[:max_results]


def _search_ddg_lite(query: str, max_results: int) -> list[SearchResult]:
    return parse_ddg_results(_ddg_request("https://lite.duckduckgo.com/lite/", query))[:max_results]


def _strip_tags(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", text)).split())


def _search_wikipedia(query: str, max_results: int) -> list[SearchResult]:
    resp = requests.get(
        "https://de.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query, "format": "json",
                "srlimit": max_results, "utf8": 1},
        headers=_headers(CONNOR_UA),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    items = resp.json().get("query", {}).get("search", [])
    return [
        SearchResult(
            title=f"{item['title']} – Wikipedia",
            url="https://de.wikipedia.org/wiki/" + quote(item["title"].replace(" ", "_")),
            snippet=_strip_tags(item.get("snippet", "")),
        )
        for item in items
    ]


def _search_searxng(query: str, base_url: str, max_results: int) -> list[SearchResult]:
    if not base_url.strip():
        raise WebError("Es ist keine SearXNG-Adresse eingestellt.")
    try:
        resp = requests.get(
            base_url.rstrip("/") + "/search",
            params={"q": query, "format": "json", "language": "de-CH"},
            headers=_headers(CONNOR_UA),
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise WebError(f"SearXNG unter {base_url} ist nicht erreichbar.") from exc
    if resp.status_code == 403:
        raise WebError("SearXNG erlaubt kein JSON. Trag in der settings.yml unter search → formats «json» ein.")
    if resp.status_code != 200:
        raise WebError(f"SearXNG antwortet mit Status {resp.status_code}.")
    results = []
    for item in resp.json().get("results", []):
        if item.get("url"):
            results.append(SearchResult(item.get("title") or item["url"], item["url"], item.get("content") or ""))
    return results[:max_results]


# ------------------------------------------------------------------ page reading

def check_public_url(url: str) -> None:
    """Only public web pages – never the local network or this PC."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebError("Ich kann nur normale Webadressen (http/https) öffnen.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebError(f"Die Adresse {parsed.hostname} wurde nicht gefunden.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise WebError("Lokale oder interne Adressen öffne ich aus Sicherheitsgründen nicht.")


def fetch_page(url: str, max_chars: int = 6000) -> tuple[str, str, str]:
    """Downloads a web page and returns (final_url, title, readable_text)."""
    current = url.strip()
    headers = {
        **_headers(),
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
    }
    session = requests.Session()
    resp = None
    for _ in range(6):
        check_public_url(current)
        try:
            resp = session.get(current, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=False)
        except requests.RequestException as exc:
            raise WebError(f"Die Seite konnte nicht geladen werden: {exc.__class__.__name__}") from exc
        if resp.is_redirect:
            location = resp.headers.get("Location", "")
            resp.close()
            if not location:
                raise WebError("Die Seite leitet ungültig weiter.")
            current = urljoin(current, location)
            resp = None
            continue
        break
    if resp is None:
        raise WebError("Zu viele Weiterleitungen.")

    with resp:
        if resp.status_code >= 400:
            raise WebError(f"Die Seite antwortet mit Fehler {resp.status_code}.")
        content_type = resp.headers.get("Content-Type", "").lower()
        if content_type and not any(t in content_type for t in ("text/", "html", "xml", "json")):
            raise WebError(f"Diesen Dateityp ({content_type.split(';')[0]}) kann ich nicht lesen.")
        raw = bytearray()
        for chunk in resp.iter_content(65536):
            raw.extend(chunk)
            if len(raw) >= MAX_DOWNLOAD:
                break

    page = decode_bytes(bytes(raw), content_type)
    if "html" in content_type or "<html" in page[:3000].lower():
        title, text = html_to_text(page)
    else:
        title, text = "", page
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + " …"
    return current, title, text


def decode_bytes(raw: bytes, content_type: str = "") -> str:
    charset = None
    match = re.search(r"charset=[\"']?([\w-]+)", content_type)
    if match:
        charset = match.group(1)
    else:
        meta = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4096], re.I)
        if meta:
            charset = meta.group(1).decode("ascii", "ignore")
    for encoding in (charset, "utf-8", "cp1252"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "aside", "form", "iframe",
            "template", "button", "select", "head"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section",
             "article", "header", "main", "blockquote", "pre", "table", "dd", "dt", "figcaption"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag == "body":
            self._skip = 0
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip:
            self.parts.append(data)


def html_to_text(page: str) -> tuple[str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(page)
        parser.close()
    except Exception:  # noqa: BLE001 - broken HTML: use what we have
        pass
    lines: list[str] = []
    for line in "".join(parser.parts).splitlines():
        line = " ".join(line.split())
        if len(line) < 2 or (lines and lines[-1] == line):
            continue
        lines.append(line)
    return " ".join("".join(parser.title_parts).split()), "\n".join(lines)
