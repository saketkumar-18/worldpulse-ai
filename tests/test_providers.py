import httpx
import pytest

from app.providers.base import ProviderAdapter, ProviderError, RateLimited
from app.providers.gnews import GNewsAdapter
from app.providers.newsapi import NewsAPIAdapter
from app.providers.rss import RSSAdapter


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


GNEWS_OK = {"articles": [
    {"title": "RBI holds repo rate steady", "url": "https://www.thehindu.com/business/rbi/a1.html?utm_source=x",
     "publishedAt": "2026-10-11T08:00:00Z", "description": "The central bank kept rates unchanged.",
     "source": {"name": "The Hindu", "url": "https://www.thehindu.com"}, "image": None},
    {"title": "", "url": "https://x.com/bad", "publishedAt": "2026-10-11T08:00:00Z", "source": {"name": "X"}},
    {"title": "Broken URL", "url": "javascript:alert(1)", "publishedAt": "2026-10-11T08:00:00Z", "source": {"name": "X"}},
]}


def test_gnews_normalizes_and_counts_invalid_items():
    seen = {}

    def handler(req):
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json=GNEWS_OK)

    a = GNewsAdapter("KEY", ["en"], ["in"], ["business"], 10, http=client(handler), sleep=lambda s: None)
    res = a.fetch()
    assert len(res.articles) == 1
    assert res.invalid == 2
    assert res.articles[0].publisher_name == "The Hindu"
    assert res.articles[0].category_hint == "business"
    assert seen["params"]["apikey"] == "KEY"


def test_gnews_without_key_is_a_clean_error_not_a_crash():
    res = GNewsAdapter("", ["en"], ["in"], ["business"], sleep=lambda s: None).fetch()
    assert res.articles == [] and res.errors


def test_gnews_retries_server_errors_with_backoff_then_records_error():
    delays = []
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503)

    a = GNewsAdapter("KEY", ["en"], ["in"], ["business"], http=client(handler),
                     max_attempts=3, backoff_base=1.0, sleep=delays.append)
    res = a.fetch()
    assert calls["n"] == 3
    assert delays == [1.0, 2.0]          # exponential backoff
    assert res.errors and "KEY" not in res.errors[0]


def test_rate_limit_stops_provider_without_retry_storm():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429)

    a = GNewsAdapter("KEY", ["en"], ["in"], ["business", "sports"], http=client(handler), sleep=lambda s: None)
    res = a.fetch()
    assert calls["n"] == 1               # stopped after the first rate-limited request
    assert any("rate limit" in e for e in res.errors)


def test_newsapi_sends_key_in_header_not_url_and_parses_ok_status():
    seen = {}

    def handler(req):
        seen["key_header"] = req.headers.get("x-api-key")
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"status": "ok", "articles": [
            {"title": "Chip maker expands fab", "url": "https://www.reuters.com/tech/a", "publishedAt": "2026-10-11T09:00:00Z",
             "source": {"name": "Reuters"}, "description": "Expansion announced.", "urlToImage": None}]})

    a = NewsAPIAdapter("NKEY", ["in"], ["technology"], http=client(handler), sleep=lambda s: None)
    res = a.fetch()
    assert seen["key_header"] == "NKEY"
    assert "NKEY" not in seen["url"]
    assert len(res.articles) == 1 and res.articles[0].publisher_name == "Reuters"


def test_newsapi_removed_placeholder_is_dropped():
    item = {"title": "[Removed]", "url": "https://x.com/a", "publishedAt": "2026-10-11T09:00:00Z", "source": {"name": "X"}}
    assert NewsAPIAdapter.normalize_item(item, "general", "in") is None


RSS_XML = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Monsoon reaches Kerala</title><link>https://feeds.example.com/a1</link>
<pubDate>Sat, 11 Oct 2026 06:00:00 GMT</pubDate><description>Early arrival.</description></item>
<item><title></title><link>https://feeds.example.com/a2</link><pubDate>Sat, 11 Oct 2026 06:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_rss_parses_valid_items_and_skips_empty_titles(monkeypatch):
    import app.providers.rss as rss_mod
    monkeypatch.setattr(rss_mod, "validate_public_url", lambda url, **k: url)

    def handler(req):
        return httpx.Response(200, content=RSS_XML)

    feeds = [{"publisher": "Example Feed", "country": "IN", "language": "en", "url": "https://feeds.example.com/rss"}]
    res = RSSAdapter(feeds, http=client(handler), sleep=lambda s: None).fetch()
    assert [a.title for a in res.articles] == ["Monsoon reaches Kerala"]
    assert res.invalid == 1


def test_rss_blocks_unsafe_feed_url_without_fetching():
    called = {"n": 0}

    def handler(req):
        called["n"] += 1
        return httpx.Response(200, content=RSS_XML)

    feeds = [{"publisher": "Internal", "country": "IN", "language": "en", "url": "http://127.0.0.1/rss"}]
    res = RSSAdapter(feeds, http=client(handler), sleep=lambda s: None).fetch()
    assert called["n"] == 0
    assert res.errors and "unsafe" in res.errors[0]


def test_one_failing_feed_does_not_stop_others(monkeypatch):
    import app.providers.rss as rss_mod
    monkeypatch.setattr(rss_mod, "validate_public_url", lambda url, **k: url)

    def handler(req):
        if "bad" in str(req.url):
            return httpx.Response(500)
        return httpx.Response(200, content=RSS_XML)

    feeds = [{"publisher": "Bad", "country": "IN", "language": "en", "url": "https://bad.example.com/rss"},
             {"publisher": "Good", "country": "IN", "language": "en", "url": "https://good.example.com/rss"}]
    res = RSSAdapter(feeds, http=client(handler), sleep=lambda s: None).fetch()
    assert len(res.articles) == 1
    assert any("Bad" in e for e in res.errors)
