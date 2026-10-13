"""Runtime configuration loaded from environment variables (.env supported).

Secrets are read only on the server. Nothing in this module is exposed to the browser.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: a blank variable (e.g. an unset GitHub secret) falls back to the default.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./worldpulse.db"
    display_timezone: str = "Asia/Kolkata"
    retention_days: int = 30
    rate_limit_per_minute: int = 120
    http_timeout_seconds: float = 15.0

    # Admin: if empty, all /api/admin routes are disabled.
    admin_token: str = ""

    # News providers (leave empty to disable a provider).
    gnews_api_key: str = ""
    gnews_languages: str = "en"
    gnews_countries: str = "in"
    gnews_categories: str = "general,business,technology,science,health,sports,entertainment,world"
    gnews_daily_request_limit: int = 100

    newsapi_api_key: str = ""
    newsapi_countries: str = "in"
    newsapi_categories: str = "general,business,technology,science,health,sports,entertainment"
    newsapi_daily_request_limit: int = 100

    # LLM: any OpenAI-compatible chat-completions endpoint.
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_max_articles_per_run: int = 25

    # Email delivery via SMTP.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    notify_recipients: str = ""

    @staticmethod
    def _csv(value: str) -> list[str]:
        return [v.strip() for v in value.split(",") if v.strip()]

    @property
    def gnews_language_list(self) -> list[str]:
        return self._csv(self.gnews_languages)

    @property
    def gnews_country_list(self) -> list[str]:
        return self._csv(self.gnews_countries)

    @property
    def gnews_category_list(self) -> list[str]:
        return self._csv(self.gnews_categories)

    @property
    def newsapi_country_list(self) -> list[str]:
        return self._csv(self.newsapi_countries)

    @property
    def newsapi_category_list(self) -> list[str]:
        return self._csv(self.newsapi_categories)

    @property
    def recipients(self) -> list[str]:
        return self._csv(self.notify_recipients)


@lru_cache
def get_settings() -> Settings:
    return Settings()
