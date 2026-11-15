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

    monkeypatch.setattr(websearch, "_search_ddg_html", broken)
    monkeypatch.setattr(websearch, "_search_ddg_lite", lambda q, m: [])
    monkeypatch.setattr(websearch, "_search_wikipedia",
                        lambda q, m: [websearch.SearchResult("Wiki", "https://de.wikipedia.org/wiki/X")])
    assert websearch.search("x")[0].title == "Wiki"

    monkeypatch.setattr(websearch, "_search_wikipedia", broken)
    monkeypatch.setattr(websearch, "_search_ddg_lite", broken)
    with pytest.raises(WebError):
        websearch.search("x")
    with pytest.raises(WebError):
        websearch.search("   ")
