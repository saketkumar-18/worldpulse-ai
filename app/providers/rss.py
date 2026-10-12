"""RSS/Atom adapter for publisher-approved feeds listed in the publisher registry.

Every feed URL is checked by the SSRF guard before being fetched. Bodies are capped in size.
"""
from __future__ import annotations

import feedparser

from ..normalize import ArticleInput, classify_heuristic, detect_language, parse_datetime, validate_article_url
from ..security import UnsafeURLError, validate_public_url
from .base import FetchResult, ProviderAdapter, ProviderError

MAX_FEED_BYTES = 2 * 1024 * 1024


class RSSAdapter(ProviderAdapter):
    name = "rss"
    kind = "rss"

    def __init__(self, feeds: list[dict], **kw) -> None:
        """feeds: [{publisher, country, language, url, category_hint?}]"""
        super().__init__(**kw)
        self.feeds = feeds

    def fetch(self) -> FetchResult:
        result = FetchResult()
        for feed in self.feeds:
            try:
                validate_public_url(feed["url"])
            except UnsafeURLError as exc:
                result.errors.append(f"{feed.get('publisher')}: unsafe feed URL ({exc})")
                continue
            try:
                body = self.download(feed["url"])
                result.requests_made += 1
            except ProviderError as exc:
                result.errors.append(f"{feed.get('publisher')}: {exc}")
                continue
            parsed = feedparser.parse(body)
            if parsed.bozo and not parsed.entries:
                result.errors.append(f"{feed.get('publisher')}: malformed feed")
                continue
            for entry in parsed.entries:
                art = self.normalize_entry(entry, feed)
                if art is None:
                    result.invalid += 1
                else:
                    result.articles.append(art)
        return result

    def download(self, url: str) -> bytes:
        try:
            with self.http.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise ProviderError(f"feed returned HTTP {resp.status_code}")
                chunks, total = [], 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_FEED_BYTES:
                        raise ProviderError("feed exceeds size limit")
                    chunks.append(chunk)
                return b"".join(chunks)
        except ProviderError:
            raise
        except Exception as exc:  # httpx errors; keep message short
            raise ProviderError(f"feed unreachable: {type(exc).__name__}") from exc

    @staticmethod
    def normalize_entry(entry, feed: dict) -> ArticleInput | None:
        title = (entry.get("title") or "").strip()
        url = (entry.get("link") or "").strip()
        published = parse_datetime(entry.get("published_parsed") or entry.get("updated_parsed")
                                   or entry.get("published") or entry.get("updated"))
        if not title or published is None or not validate_article_url(url):
            return None
        description = (entry.get("summary") or "").strip()
        lang = feed.get("language", "en")
        return ArticleInput(
            title=title, url=url, published_at=published, publisher_name=feed["publisher"],
            provider="rss", language=detect_language(title + " " + description, lang),
            country=feed.get("country", ""), description=description,
            category_hint=feed.get("category_hint"), source_domain=feed.get("domain", ""),
        )
