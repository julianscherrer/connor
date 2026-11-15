"""Web search and page reading – the only part of Connor that talks to the internet."""
from __future__ import annotations

import base64
import html
import ipaddress
import logging
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
BLOCK_PAUSE = 600  # seconds to skip an engine after it blocked us
SKIP_READING = ("youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com")

_blocked_until: dict[str, float] = {}
_blocked_lock = threading.Lock()


class WebError(Exception):
    """A problem with web search or page loading that can be shown to the user."""


class EngineBlocked(WebError):
    """The search engine refuses automated requests for now."""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


def use_system_certificates() -> bool:
    """Trust the certificates Windows trusts. Needed behind company proxies and antivirus
    programs that check HTTPS traffic – otherwise every search fails with an SSL error."""
    try:
        import truststore
    except ImportError:
        return False
    try:
        truststore.inject_into_ssl()
    except Exception as exc:  # noqa: BLE001 - fall back to the default certificates
        log.warning("System-Zertifikate konnten nicht aktiviert werden: %s", exc)
        return False
    return True


def _is_blocked(name: str) -> bool:
    with _blocked_lock:
        return _blocked_until.get(name, 0) > time.monotonic()


def _mark_blocked(name: str) -> None:
    with _blocked_lock:
        _blocked_until[name] = time.monotonic() + BLOCK_PAUSE


def search(query: str, provider: str = "auto", searxng_url: str = "",
           max_results: int = 6) -> list[SearchResult]:
    query = " ".join(query.split())
    if not query:
        raise WebError("Die Suchanfrage ist leer.")
    if provider == "searxng":
        return _search_searxng(query, searxng_url, max_results)

    chains = {
        "ddg": (_search_ddg_html, _search_ddg_lite),
        "bing": (_search_bing,),
    }
    engines = chains.get(provider, (_search_ddg_html, _search_bing, _search_ddg_lite, _search_wikipedia))
    errors: list[str] = []
    tried = 0
    for engine in engines:
        name = engine.__name__
        if _is_blocked(name):
            continue
        tried += 1
        try:
            results = engine(query, max_results)
        except EngineBlocked as exc:
            log.info("%s blockiert gerade automatische Anfragen – pausiere es: %s", name, exc)
            _mark_blocked(name)
            errors.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - try the next engine
            log.info("Suche über %s fehlgeschlagen: %s", name, exc)
            errors.append(str(exc))
            continue
        if results:
            return results[:max_results]
    if errors and len(errors) == tried:
        raise WebError(f"Die Websuche hat nicht geklappt ({errors[-1]}). Ist der PC online?")
    if not tried:
        raise WebError("Alle Suchmaschinen blockieren gerade automatische Anfragen. Versuch es in ein paar Minuten nochmal.")
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
    if resp.status_code in (202, 403, 429):
        raise EngineBlocked(f"DuckDuckGo blockiert automatische Anfragen gerade (Status {resp.status_code})")
    if resp.status_code != 200:
        raise WebError(f"DuckDuckGo antwortet mit Status {resp.status_code}")
    return resp.text


def _search_ddg_html(query: str, max_results: int) -> list[SearchResult]:
    page = _ddg_request("https://html.duckduckgo.com/html/", query)
    results = parse_ddg_results(page)
    if not results and "anomaly" in page.lower():
        raise EngineBlocked("DuckDuckGo blockiert die Anfrage gerade")
    return results[:max_results]


def _search_ddg_lite(query: str, max_results: int) -> list[SearchResult]:
    return parse_ddg_results(_ddg_request("https://lite.duckduckgo.com/lite/", query))[:max_results]


def resolve_bing_url(href: str) -> str | None:
    """Bing wraps result links (…/ck/a?…&u=a1<base64>). Returns the real target."""
    if not href:
        return None
    parsed = urlparse(href)
    if parsed.netloc.endswith("bing.com"):
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if not encoded.startswith("a1"):
            return None
        data = encoded[2:]
        try:
            target = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        return target if target.startswith(("http://", "https://")) else None
    return href if parsed.scheme in ("http", "https") else None


class _BingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._li_depth = 0
        self._in_h2 = False
        self._in_title = False
        self._in_snippet = False
        self._href = ""
        self._title: list[str] = []
        self._snippet: list[str] = []
        self._have_snippet = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "li":
            if self._li_depth:
                self._li_depth += 1
            elif "b_algo" in (attributes.get("class") or "").split():
                self._li_depth = 1
                self._href, self._title, self._snippet, self._have_snippet = "", [], [], False
            return
        if not self._li_depth:
            return
        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and not self._href:
            self._href = attributes.get("href") or ""
            self._in_title = True
        elif tag == "p" and not self._have_snippet and self._href:
            self._in_snippet = True

    def handle_endtag(self, tag):
        if not self._li_depth:
            return
        if tag == "a" and self._in_title:
            self._in_title = False
        elif tag == "h2":
            self._in_h2 = False
        elif tag == "p" and self._in_snippet:
            self._in_snippet = False
            self._have_snippet = bool("".join(self._snippet).strip())
        elif tag == "li":
            self._li_depth -= 1
            if not self._li_depth:
                url = resolve_bing_url(self._href)
                title = " ".join("".join(self._title).split())
                if url and title:
                    self.results.append(SearchResult(title, url, " ".join("".join(self._snippet).split())))

    def handle_data(self, data):
        if self._in_title:
            self._title.append(data)
        elif self._in_snippet:
            self._snippet.append(data)


def parse_bing_results(page: str) -> list[SearchResult]:
    parser = _BingParser()
    parser.feed(page)
    parser.close()
    seen: set[str] = set()
    return [r for r in parser.results if not (r.url in seen or seen.add(r.url))]


def _search_bing(query: str, max_results: int) -> list[SearchResult]:
    resp = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "setlang": "de", "cc": "CH"},
        headers={**_headers(), "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        timeout=TIMEOUT,
    )
    if resp.status_code in (403, 429):
        raise EngineBlocked(f"Bing blockiert automatische Anfragen gerade (Status {resp.status_code})")
    if resp.status_code != 200:
        raise WebError(f"Bing antwortet mit Status {resp.status_code}")
    return parse_bing_results(resp.text)[:max_results]


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


# ------------------------------------------------------------------ research

_WORD = re.compile(r"\w+")
_EXCERPT_STOPWORDS = frozenset(
    "aber als auch auf aus bei bis das dass dem den der des die dies ein eine einem einen einer eines für hat hatte "
    "ich ihr ist mit nach nicht noch oder sich sie sind und von vor war was wer wie wird wo zu zum zur über "
    "the and for are was who what with this that from have how".split()
)


def best_excerpt(text: str, query: str, max_chars: int = 1500) -> str:
    """Picks the lines of a page that fit the question best (instead of menus and cookie banners)."""
    words = {w for w in _WORD.findall(query.lower()) if len(w) > 2 and w not in _EXCERPT_STOPWORDS}
    lines = list(dict.fromkeys(line.strip() for line in text.splitlines() if len(line.strip()) > 30))
    if not lines:
        return text[:max_chars].strip()
    if not words:
        scored = list(enumerate(lines))
    else:
        scored = []
        for index, line in enumerate(lines):
            low = line.lower()
            hits = sum(1 for w in words if w in low)
            if hits:
                scored.append((index, line, hits + min(len(line), 400) / 400))
        scored.sort(key=lambda item: -item[2])
        scored = [(index, line) for index, line, _score in scored]
        if not scored:
            scored = list(enumerate(lines))
    chosen: list[tuple[int, str]] = []
    used = 0
    for index, line in scored:
        if used >= max_chars:
            break
        if len(line) > max_chars - used:
            line = line[: max(0, max_chars - used)].rsplit(" ", 1)[0] + " …"
        chosen.append((index, line))
        used += len(line) + 3
    chosen.sort()
    return "\n".join(line for _index, line in chosen).strip()


def read_top_pages(results: list[SearchResult], query: str, count: int = 2,
                   max_chars: int = 1500) -> list[tuple[SearchResult, str]]:
    """Downloads the best results in parallel and returns the passages that fit the question."""
    candidates = [r for r in results if not any(host in urlparse(r.url).netloc for host in SKIP_READING)]
    candidates = candidates[: count + 1]

    def load(result: SearchResult) -> tuple[SearchResult, str]:
        try:
            _url, _title, text = fetch_page(result.url, max_chars=40_000)
        except Exception as exc:  # noqa: BLE001 - a page that cannot be read is simply skipped
            log.info("Seite %s nicht lesbar: %s", result.url, exc)
            return result, ""
        return result, best_excerpt(text, query, max_chars)

    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        pages = list(pool.map(load, candidates))
    return [(result, excerpt) for result, excerpt in pages if excerpt][:count]


def format_results(query: str, results: list[SearchResult], pages: list[tuple[SearchResult, str]]) -> str:
    if not results:
        return f"Keine Treffer für «{query}»."
    lines = [f"Suchergebnisse für «{query}»:"]
    for number, result in enumerate(results, 1):
        lines.append(f"{number}. {result.title}\n   {result.url}\n   {result.snippet[:300]}")
    if pages:
        lines.append("\nAuszüge aus den besten Treffern:")
        for result, excerpt in pages:
            lines.append(f"--- {result.title} ({result.url}) ---\n{excerpt}")
    lines.append("\nBeantworte die Frage mit diesen Infos. Nenne die Quellen als Markdown-Links [Titel](URL). "
                 "Steht die Antwort nicht drin, sag das ehrlich.")
    return "\n".join(lines)
