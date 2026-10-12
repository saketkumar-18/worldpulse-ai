"""NewsAPI adapter. Endpoint and fields follow https://newsapi.org/docs/endpoints/top-headlines.

NewsAPI's free developer plan is for development only and has restrictions on production use.
Confirm your plan allows the intended usage before enabling this provider in production.
"""
from __future__ import annotations

from ..normalize import ArticleInput, detect_language, parse_datetime, validate_article_url
from .base import FetchResult, ProviderAdapter, ProviderError

NEWSAPI_TOP_HEADLINES = "https://newsapi.org/v2/top-headlines"


class NewsAPIAdapter(ProviderAdapter):
    name = "newsapi"
    kind = "api"

    def __init__(self, api_key: str, countries: list[str], categories: list[str],
                 max_requests: int = 100, **kw) -> None:
        super().__init__(**kw)
        self.api_key = api_key
        self.countries = countries
        self.categories = categories
        self.max_requests = max_requests

    def fetch(self) -> FetchResult:
        result = FetchResult()
        if not self.api_key:
            result.errors.append("NEWSAPI_API_KEY not configured")
            return result
        combos = [(c, cat) for c in self.countries for cat in self.categories]
        for country, cat in combos[: self.max_requests]:
            params = {"country": country, "category": cat, "pageSize": 20}
            headers = {"X-Api-Key": self.api_key}
            try:
                data = self.get_json(NEWSAPI_TOP_HEADLINES, params, headers=headers)
                result.requests_made += 1
            except ProviderError as exc:
                result.errors.append(f"{country}/{cat}: {exc}")
                if "rate limit" in str(exc):
                    break
                continue
            if data.get("status") != "ok":
                result.errors.append(f"{country}/{cat}: status={data.get('status')}")
                continue
            for item in data.get("articles") or []:
                art = self.normalize_item(item, cat, country)
                if art is None:
                    result.invalid += 1
                else:
                    result.articles.append(art)
        return result

    @staticmethod
    def normalize_item(item: dict, category: str, country: str) -> ArticleInput | None:
        if not isinstance(item, dict):
            return None
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        published = parse_datetime(item.get("publishedAt"))
        source = item.get("source") or {}
        publisher = (source.get("name") or "").strip()
        if not title or title == "[Removed]" or not publisher or published is None or not validate_article_url(url):
            return None
        description = (item.get("description") or "").strip()
        image = (item.get("urlToImage") or "").strip() or None
        if image and not validate_article_url(image):
            image = None
        return ArticleInput(
            title=title, url=url, published_at=published, publisher_name=publisher,
            provider="newsapi", language=detect_language(title + " " + description, "en"),
            country=country.upper(), description=description,
            category_hint=category, image_url=image,
        )
