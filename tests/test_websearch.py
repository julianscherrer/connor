import pytest

from connor import websearch
from connor.websearch import WebError, check_public_url, decode_bytes, html_to_text, parse_ddg_results, resolve_ddg_url

DDG_PAGE = """
<html><body>
<div class="result results_links result--ad">
  <h2 class="result__title"><a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=x">Werbung</a></h2>
  <a class="result__snippet" href="#">Kauf mich</a>
</div>
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fde.wikipedia.org%2Fwiki%2FLangbogen&amp;rut=abc">Langbogen – <b>Wikipedia</b></a>
  </h2>
  <a class="result__snippet" href="#">Der <b>Langbogen</b> ist ein Bogen &amp; mehr.</a>
</div>
<div class="result results_links">
  <h2 class="result__title"><a class="result__a" href="https://example.org/bogen">Bogenbau</a></h2>
  <div class="result__snippet">So baust du einen Bogen.</div>
</div>
</body></html>
"""

LITE_PAGE = """
<table>
<tr><td><a rel="nofollow" href="https://example.com/a" class='result-link'>Erster Treffer</a></td></tr>
<tr><td class='result-snippet'>Beschreibung eins</td></tr>
<tr><td><a rel="nofollow" href="https://example.com/b" class='result-link'>Zweiter</a></td></tr>
</table>
"""


def test_parse_duckduckgo_html():
    results = parse_ddg_results(DDG_PAGE)
    assert [r.url for r in results] == ["https://de.wikipedia.org/wiki/Langbogen", "https://example.org/bogen"]
    assert results[0].title == "Langbogen – Wikipedia"
    assert results[0].snippet == "Der Langbogen ist ein Bogen & mehr."
    assert results[1].snippet == "So baust du einen Bogen."


def test_parse_duckduckgo_lite():
    results = parse_ddg_results(LITE_PAGE)
    assert [(r.title, r.snippet) for r in results] == [("Erster Treffer", "Beschreibung eins"), ("Zweiter", "")]


def test_resolve_ddg_url():
    assert resolve_ddg_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.ch%2Fx%3Fy%3D1") == "https://a.ch/x?y=1"
    assert resolve_ddg_url("https://duckduckgo.com/y.js?ad=1") is None
    assert resolve_ddg_url("javascript:alert(1)") is None
    assert resolve_ddg_url("https://example.com") == "https://example.com"


def test_html_to_text_skips_noise():
    page = """<html><head><title>Mein Titel</title><style>.x{}</style></head>
    <body><nav>Menü Menü</nav><h1>Überschrift</h1><p>Erster Absatz.</p><script>alert(1)</script>
    <p>Zweiter&nbsp;Absatz.</p><footer>Impressum</footer></body></html>"""
    title, text = html_to_text(page)
    assert title == "Mein Titel"
    assert text.splitlines() == ["Überschrift", "Erster Absatz.", "Zweiter Absatz."]
    assert "Menü" not in text and "alert" not in text and "Impressum" not in text


def test_decode_bytes():
    assert decode_bytes("Grüezi".encode("cp1252"), "text/html; charset=windows-1252") == "Grüezi"
    assert decode_bytes('<meta charset="utf-8">Grüezi'.encode()) .endswith("Grüezi")
    assert decode_bytes("Grüezi".encode("cp1252")) == "Grüezi"


@pytest.mark.parametrize("url", ["http://localhost:11434/api/tags", "http://127.0.0.1/", "http://192.168.1.1/",
                                 "file:///C:/Windows/win.ini", "ftp://example.com"])
def test_local_and_strange_urls_are_blocked(url):
    with pytest.raises(WebError):
        check_public_url(url)


def test_search_falls_back_to_next_engine(monkeypatch):
    def broken(_query, _max):
        raise WebError("kaputt")

    monkeypatch.setattr(websearch, "_blocked_until", {})
    monkeypatch.setattr(websearch, "_search_ddg_html", broken)
    monkeypatch.setattr(websearch, "_search_bing", lambda q, m: [])
    monkeypatch.setattr(websearch, "_search_ddg_lite", lambda q, m: [])
    monkeypatch.setattr(websearch, "_search_wikipedia",
                        lambda q, m: [websearch.SearchResult("Wiki", "https://de.wikipedia.org/wiki/X")])
    assert websearch.search("x")[0].title == "Wiki"

    monkeypatch.setattr(websearch, "_search_wikipedia", broken)
    monkeypatch.setattr(websearch, "_search_ddg_lite", broken)
    monkeypatch.setattr(websearch, "_search_bing", broken)
    with pytest.raises(WebError):
        websearch.search("x")
    with pytest.raises(WebError):
        websearch.search("   ")


def test_blocked_engine_is_paused(monkeypatch):
    calls = []

    def blocked(_query, _max):
        calls.append("ddg")
        raise websearch.EngineBlocked("Status 202")

    def bing(_query, _max):
        calls.append("bing")
        return [websearch.SearchResult("Treffer", "https://example.org")]

    monkeypatch.setattr(websearch, "_blocked_until", {})
    monkeypatch.setattr(websearch, "_search_ddg_html", blocked)
    monkeypatch.setattr(websearch, "_search_bing", bing)
    assert websearch.search("x")[0].title == "Treffer"
    assert websearch.search("y")[0].title == "Treffer"
    assert calls == ["ddg", "bing", "bing"]
    assert websearch.search("z", provider="bing")[0].url == "https://example.org"


BING_PAGE = """
<ol id="b_results">
<li class="b_algo" data-id iid=SERP.1><link rel="stylesheet" href="x.css"/>
  <div class="b_tpcn"><a class="tilk" href="https://www.bing.com/ck/a?!&amp;&amp;p=1&amp;u=a1aHR0cHM6Ly9kZS53aWtpcGVkaWEub3JnL3dpa2kvRnUlQzMlOUZiYWxs&amp;ntb=1">
  <div class="tptt">wikipedia.org</div><cite>https://de.wikipedia.org › wiki</cite></a></div>
  <h2><a target="_blank" href="https://www.bing.com/ck/a?!&amp;&amp;p=1&amp;u=a1aHR0cHM6Ly9kZS53aWtpcGVkaWEub3JnL3dpa2kvRnUlQzMlOUZiYWxs&amp;ntb=1">Fu&#223;ball – Wikipedia</a></h2>
  <div class="b_caption"><p class="b_lineclamp2">Fu&#223;ball ist eine <strong>Ballsportart</strong> …</p></div>
</li>
<li class="b_ad"><h2><a href="https://ads.example">Werbung</a></h2></li>
<li class="b_algo"><h2><a href="https://example.org/direkt">Direkter Link</a></h2>
  <div class="b_caption"><p>Ohne Umweg.</p><p>Zweiter Absatz</p></div></li>
</ol>
"""


def test_parse_bing():
    results = websearch.parse_bing_results(BING_PAGE)
    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("Fußball – Wikipedia", "https://de.wikipedia.org/wiki/Fu%C3%9Fball", "Fußball ist eine Ballsportart …"),
        ("Direkter Link", "https://example.org/direkt", "Ohne Umweg."),
    ]
    assert websearch.resolve_bing_url("https://www.bing.com/ck/a?u=zzz") is None
    assert websearch.resolve_bing_url("https://www.bing.com/ck/a?u=a1!!!") is None


def test_best_excerpt_prefers_matching_lines():
    page = "\n".join([
        "Cookies akzeptieren und weiter zum Inhalt der Seite bitte",
        "Navigation: Startseite, Sport, Wetter, Kultur und noch viel mehr",
        "Im Finale der Fussball-WM 2026 gewann Spanien gegen Brasilien mit 2:1.",
        "Impressum und Datenschutz und Kontakt und so weiter und so fort",
    ])
    excerpt = websearch.best_excerpt(page, "Wer hat das Finale der WM 2026 gewonnen?", max_chars=120)
    assert excerpt.startswith("Im Finale der Fussball-WM 2026 gewann Spanien")
    assert "Cookies" not in excerpt
    assert websearch.best_excerpt("kurz", "egal") == "kurz"


def test_format_results():
    results = [websearch.SearchResult("A", "https://a.ch", "Snippet A")]
    text = websearch.format_results("frage", results, [(results[0], "Auszug A")])
    assert "1. A\n   https://a.ch\n   Snippet A" in text and "--- A (https://a.ch) ---\nAuszug A" in text
    assert websearch.format_results("frage", [], []) == "Keine Treffer für «frage»."


def test_read_top_pages_skips_broken_and_video_sites(monkeypatch):
    def fetch(url, max_chars=0):
        if "kaputt" in url:
            raise WebError("404")
        return url, "Titel", "Eine ausführliche Zeile über das Thema Langbogen und seine Geschichte."

    monkeypatch.setattr(websearch, "fetch_page", fetch)
    results = [websearch.SearchResult("Video", "https://www.youtube.com/watch?v=1"),
               websearch.SearchResult("Kaputt", "https://kaputt.example"),
               websearch.SearchResult("Gut", "https://gut.example"),
               websearch.SearchResult("Auch gut", "https://auch.example")]
    pages = websearch.read_top_pages(results, "Langbogen", count=2)
    assert [result.title for result, _excerpt in pages] == ["Gut", "Auch gut"]
