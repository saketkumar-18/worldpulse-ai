"""GNews API adapter. Endpoint and fields follow https://gnews.io/docs/v4 (top-headlines)."""
from __future__ import annotations

from datetime import datetime

from ..normalize import ArticleInput, classify_heuristic, detect_language, parse_datetime, validate_article_url
from .base import FetchResult, ProviderAdapter, ProviderError

GNEWS_TOP_HEADLINES = "https://gnews.io/api/v4/top-headlines"

# GNews topic values per https://gnews.io/docs/v4 (general, world, nation, business, technology,
# entertainment, sports, science, health).
TOPIC_FOR_CATEGORY = {"world": "world", "india": "nation", "business": "business",
                      "technology": "technology", "science": "science", "health": "health",
                      "sports": "sports", "entertainment": "entertainment"}


def _domain_of(url: str) -> str:
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "") if url else ""
    return host[4:] if host.startswith("www.") else host


class GNewsAdapter(ProviderAdapter):
    name = "gnews"
    kind = "api"

    def __init__(self, api_key: str, languages: list[str], countries: list[str], topics: list[str],
                 max_requests: int = 100, **kw) -> None:
        super().__init__(**kw)
        self.api_key = api_key
        self.languages = languages
        self.countries = countries
        self.topics = topics
        self.max_requests = max_requests

    def fetch(self) -> FetchResult:
        result = FetchResult()
        if not self.api_key:
            result.errors.append("GNEWS_API_KEY not configured")
            return result
        combos = [(t, lang, c) for t in self.topics for lang in self.languages for c in self.countries]
        for topic, lang, country in combos[: self.max_requests]:
            params = {"topic": TOPIC_FOR_CATEGORY.get(topic, topic), "lang": lang, "country": country,
                      "max": 10, "apikey": self.api_key}
            try:
                data = self.get_json(GNEWS_TOP_HEADLINES, params)
                result.requests_made += 1
            except ProviderError as exc:
                result.errors.append(f"{topic}/{lang}/{country}: {exc}")
                if "rate limit" in str(exc):
                    break
                continue
            for item in data.get("articles") or []:
                art = self.normalize_item(item, topic, lang, country)
                if art is None:
                    result.invalid += 1
                else:
                    result.articles.append(art)
        return result

    @staticmethod
    def normalize_item(item: dict, topic: str, lang: str, country: str) -> ArticleInput | None:
        """Map one GNews article to ArticleInput. Returns None for unusable items."""
        if not isinstance(item, dict):
            return None
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        published = parse_datetime(item.get("publishedAt"))
        source = item.get("source") or {}
        publisher = (source.get("name") or "").strip()
        if not title or not publisher or published is None or not validate_article_url(url):
            return None
        description = (item.get("description") or "").strip()
        image = (item.get("image") or "").strip() or None
        if image and not validate_article_url(image):
            image = None
        return ArticleInput(
            title=title, url=url, published_at=published, publisher_name=publisher,
            provider="gnews", language=detect_language(title + " " + description, lang),
            country=country.upper(), description=description,
            category_hint=topic,
            image_url=image, source_domain=_domain_of(source.get("url") or ""),
        )
