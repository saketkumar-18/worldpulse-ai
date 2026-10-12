from datetime import datetime, timezone

import pytest

from app import normalize as n
from app import security as s


def test_canonical_url_strips_tracking_and_www():
    assert n.canonical_url("https://www.BBC.co.uk/news/x/?utm_source=a&id=3#frag") == "https://bbc.co.uk/news/x?id=3"


def test_canonical_url_drops_trailing_slash_only_on_path():
    assert n.canonical_url("https://example.com/a/") == "https://example.com/a"


def test_normalize_title_strips_publisher_suffix_and_punctuation():
    assert n.normalize_title("Modi visits Japan - The Hindu") == "modi visits japan"


def test_title_hash_is_stable_across_publisher_suffix():
    assert n.title_hash("Rupee falls - Mint") == n.title_hash("Rupee falls!")


def test_detect_language_hindi_vs_english():
    assert n.detect_language("भारत की अर्थव्यवस्था तेज़ी से बढ़ रही है") == "hi"
    assert n.detect_language("India economy grows fast") == "en"
    assert n.detect_language("") == "en"


def test_parse_datetime_rejects_implausible_and_future():
    now = datetime(2026, 10, 12, tzinfo=timezone.utc).replace(tzinfo=None)
    assert n.parse_datetime("1970-01-01T00:00:00Z", now=now) is None
    assert n.parse_datetime("2099-01-01T00:00:00Z", now=now) is None
    assert n.parse_datetime("not a date", now=now) is None
    assert n.parse_datetime("2026-10-11T10:00:00+05:30", now=now) == datetime(2026, 10, 11, 4, 30)


def test_classify_uses_keywords_and_never_returns_unknown_slug():
    assert n.classify_heuristic("Sensex jumps as RBI holds rates") == "business"
    assert n.classify_heuristic("Something entirely unrelated") == "world"
    assert n.classify_heuristic("x", hint="sports") == "sports"
    assert n.classify_heuristic("x", hint="bogus") in n.CATEGORY_SLUGS


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://x.com/a", "", "https://"])
def test_validate_article_url_rejects_bad_schemes(url):
    assert s.validate_article_url(url) is False


def test_validate_public_url_blocks_private_and_internal_hosts():
    fake = lambda host, port, proto=None: [(2, 1, 6, "", ("10.0.0.5", 443))]
    with pytest.raises(s.UnsafeURLError):
        s.validate_public_url("https://intranet.example.com/feed", resolver=fake)
    with pytest.raises(s.UnsafeURLError):
        s.validate_public_url("http://localhost/feed")
    with pytest.raises(s.UnsafeURLError):
        s.validate_public_url("http://192.168.1.1/x")  # literal private IP via resolver
    with pytest.raises(s.UnsafeURLError):
        s.validate_public_url("https://user:pw@example.com/feed")
    with pytest.raises(s.UnsafeURLError):
        s.validate_public_url("https://example.com:8443/feed", resolver=lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


def test_validate_public_url_allows_public_host():
    ok = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
    assert s.validate_public_url("https://example.com/feed", resolver=ok) == "https://example.com/feed"


def test_redact_removes_api_keys():
    assert "SECRET" not in s.redact("https://x/y?apikey=SECRET&q=1")
    assert "SECRET" not in s.redact("https://x/y?api_key=SECRET")
