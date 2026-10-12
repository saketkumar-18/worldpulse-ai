"""Provider adapter interface and shared HTTP helpers with retry/backoff."""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from ..normalize import ArticleInput
from ..security import redact

log = logging.getLogger("worldpulse.providers")


class ProviderError(Exception):
    """Non-retryable provider failure (bad response shape, auth failure, quota)."""


class RateLimited(ProviderError):
    pass


@dataclass
class FetchResult:
    articles: list[ArticleInput] = field(default_factory=list)
    requests_made: int = 0
    invalid: int = 0
    errors: list[str] = field(default_factory=list)


class ProviderAdapter(ABC):
    name: str = "base"
    kind: str = "base"

    def __init__(self, http: httpx.Client | None = None, max_attempts: int = 3,
                 backoff_base: float = 1.0, sleep=time.sleep) -> None:
        self.http = http or httpx.Client(timeout=15.0, follow_redirects=True,
                                         headers={"User-Agent": "WorldPulseAI/0.1 (+news reader)"})
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self._sleep = sleep

    @abstractmethod
    def fetch(self) -> FetchResult:
        """Fetch and normalize articles. Must not raise for a single bad item."""

    def get_json(self, url: str, params: dict, headers: dict | None = None) -> dict:
        """GET with exponential backoff on 429/5xx/network errors. Never logs the API key."""
        last_exc: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                resp = self.http.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_exc = ProviderError(f"network error: {redact(str(exc))}")
            else:
                if resp.status_code == 429:
                    raise RateLimited("provider rate limit reached")
                if resp.status_code in (401, 403):
                    raise ProviderError(f"authentication/authorization rejected (HTTP {resp.status_code})")
                if resp.status_code >= 500:
                    last_exc = ProviderError(f"provider server error (HTTP {resp.status_code})")
                elif resp.status_code >= 400:
                    raise ProviderError(f"request rejected (HTTP {resp.status_code})")
                else:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise ProviderError("provider returned non-JSON response") from exc
            if attempt < self.max_attempts - 1:
                self._sleep(self.backoff_base * (2 ** attempt))
        raise last_exc or ProviderError("unknown provider failure")
