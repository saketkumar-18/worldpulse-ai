"""Normalization helpers: canonical URLs, headline keys, timestamps, language, categories."""
import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .db import utcnow  # noqa: F401  (re-exported for convenience)
from .security import validate_article_url  # noqa: F401  (re-exported)

CATEGORIES: list[tuple[str, str]] = [
    ("world", "World"), ("india", "India"), ("politics", "Politics & Government"),
    ("business", "Business & Economy"), ("technology", "Technology & AI"),
    ("science", "Science & Research"), ("health", "Health"), ("sports", "Sports"),
    ("education", "Education & Careers"), ("environment", "Climate & Environment"),
    ("entertainment", "Entertainment & Culture"), ("regional", "State & Regional"),
]
CATEGORY_SLUGS = {slug for slug, _ in CATEGORIES}

_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|igshid|cmpid)$", re.I)
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_PUBLISHER_SUFFIX = re.compile(r"\s+[-|\u2013\u2014]\s+([^-|\u2013\u2014]{2,30})$")

# Keyword heuristics used when a provider gives no usable category.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "politics": ("election", "parliament", "minister", "government", "lok sabha", "modi",
                 "congress party", "bjp", "policy", "supreme court", "cabinet", "president"),
    "business": ("market", "sensex", "nifty", "stock", "economy", "inflation", "rbi",
                 "earnings", "gdp", "rupee", "tariff", "bank", "startup", "ipo"),
    "technology": ("ai ", " ai", "artificial intelligence", "chip", "software", "smartphone",
                   "openai", "google", "apple", "cyber", "semiconductor", "llm"),
    "science": ("research", "study", "scientists", "nasa", "isro", "space", "physics", "genome"),
    "health": ("health", "hospital", "disease", "vaccine", "doctor", "cancer", "virus", "medical"),
    "sports": ("cricket", "football", "match", "tournament", "olympic", "ipl", "tennis", "league"),
    "education": ("exam", "students", "university", "neet", "jee", "school", "admission", "career"),
    "environment": ("climate", "emissions", "pollution", "flood", "wildfire", "heatwave", "forest"),
    "entertainment": ("film", "movie", "box office", "actor", "album", "celebrity", "series", "bollywood"),
    "regional": ("state government", "chief minister", "district", "municipal", "village"),
}
INDIA_TERMS = ("india", "delhi", "mumbai", "bengaluru", "chennai", "kolkata", "rupee", "lok sabha", "indian")

PROVIDER_CATEGORY_MAP: dict[str, str] = {
    "general": "world", "world": "world", "nation": "india", "business": "business",
    "technology": "technology", "science": "science", "health": "health",
    "sports": "sports", "entertainment": "entertainment",
}


def canonical_url(url: str) -> str:
    """Stable key for de-duplication: lowercase host, no www, no fragment, no tracking params."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/+$", "", parts.path) or "/"
    query = sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k))
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").strip()
    text = _PUBLISHER_SUFFIX.sub("", text)  # drop trailing " - Publisher" credits
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


def title_tokens(title: str) -> set[str]:
    stop = {"the", "and", "for", "with", "from", "that", "this", "after", "over", "amid", "says", "will", "its"}
    return {t for t in normalize_title(title).split() if len(t) > 2 and t not in stop}


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def parse_datetime(value, now: datetime | None = None) -> datetime | None:
    """Parse ISO-8601 strings, datetimes or feedparser struct_time into naive UTC.

    Returns None for missing, unparseable or implausible (pre-2000 / far-future) values.
    """
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, time.struct_time):
            dt = datetime(*value[:6], tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    dt = to_naive_utc(dt)
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if dt.year < 2000 or dt > now + timedelta(days=1):
        return None
    return dt


def detect_language(text: str, default: str = "en") -> str:
    """'hi' when Devanagari makes up a meaningful share of letters, else the default."""
    letters = _LETTER.findall(text or "")
    if not letters:
        return default
    devanagari = len(_DEVANAGARI.findall(text))
    return "hi" if devanagari / max(len(letters), 1) > 0.2 else default


def classify_heuristic(title: str, description: str = "", hint: str | None = None) -> str:
    """Category from a provider hint, else keyword scoring. Never returns an unknown slug."""
    if hint in CATEGORY_SLUGS and hint not in ("world", "india"):
        return hint
    text = f" {(title or '').lower()} {(description or '').lower()} "
    best, best_score = None, 0
    for slug, words in KEYWORDS.items():
        score = sum(text.count(w) for w in words)
        if score > best_score:
            best, best_score = slug, score
    if best:
        return best
    if any(term in text for term in INDIA_TERMS):
        return "india"
    return hint if hint in CATEGORY_SLUGS else "world"


@dataclass
class ArticleInput:
    title: str
    url: str
    published_at: datetime
    publisher_name: str
    provider: str
    language: str = "en"
    country: str = ""
    description: str = ""
    category_hint: str | None = None
    image_url: str | None = None
    source_domain: str = ""
