"""ORM models. All timestamps are naive UTC."""
from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class NewsCategory(Base):
    __tablename__ = "news_categories"
    slug: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Publisher(Base):
    """Publisher directory entry, loaded from app/registry/publishers.json."""
    __tablename__ = "publishers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    country: Mapped[str] = mapped_column(String(8), index=True)
    region: Mapped[str] = mapped_column(String(64), default="")
    language: Mapped[str] = mapped_column(String(8), default="en")
    website: Mapped[str] = mapped_column(String(512))
    domain: Mapped[str] = mapped_column(String(255), index=True)
    rss_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    rss_status: Mapped[str] = mapped_column(String(32), default="unverified")
    rss_last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rss_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    epaper_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # not_verified | verified | unavailable. Only "verified" editions are shown as available.
    epaper_status: Mapped[str] = mapped_column(String(32), default="not_verified")
    reliability: Mapped[float] = mapped_column(Float, default=0.6)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class Provider(Base):
    __tablename__ = "news_providers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)  # gnews | newsapi | rss
    kind: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[str] = mapped_column(String(512), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    request_limit_per_day: Mapped[int] = mapped_column(Integer, default=100)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    languages: Mapped[list] = mapped_column(JSON, default=list)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    articles_imported: Mapped[int] = mapped_column(Integer, default=0)


class NewsEvent(Base):
    """A real-world event grouping articles that report the same story."""
    __tablename__ = "news_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    category: Mapped[str] = mapped_column(String(32), ForeignKey("news_categories.slug"), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    article_count: Mapped[int] = mapped_column(Integer, default=0)
    publisher_count: Mapped[int] = mapped_column(Integer, default=0)


class Article(Base):
    __tablename__ = "news_articles"
    __table_args__ = (
        Index("ix_articles_category_published", "category", "published_at"),
        Index("ix_articles_language_published", "language", "published_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_url: Mapped[str] = mapped_column(String(2048), unique=True)
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(512))
    title_hash: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    publisher_name: Mapped[str] = mapped_column(String(128), index=True)
    publisher_id: Mapped[int | None] = mapped_column(ForeignKey("publishers.id"), nullable=True)
    source_domain: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8), default="en")
    country: Mapped[str] = mapped_column(String(8), default="")
    category: Mapped[str] = mapped_column(String(32), ForeignKey("news_categories.slug"))
    category_source: Mapped[str] = mapped_column(String(16), default="heuristic")  # provider | heuristic | llm
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("news_events.id"), nullable=True, index=True)
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_breaking: Mapped[bool] = mapped_column(Boolean, default=False)


class AISummary(Base):
    __tablename__ = "ai_summaries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id"), unique=True)
    headline_en: Mapped[str] = mapped_column(String(300), default="")
    detail_en: Mapped[str] = mapped_column(Text, default="")
    headline_hi: Mapped[str | None] = mapped_column(String(300), nullable=True)
    detail_hi: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_assigned: Mapped[str | None] = mapped_column(String(32), nullable=True)
    claim_type: Mapped[str] = mapped_column(String(16), default="unknown")
    generator: Mapped[str] = mapped_column(String(32))  # llm | extractive | extractive_fallback
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # running | success | partial | failed | skipped
    requests_made: Mapped[int] = mapped_column(Integer, default=0)
    articles_fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    invalid: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class IntegrationHealthLog(Base):
    __tablename__ = "integration_health_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ok: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str] = mapped_column(Text, default="")


class BriefingRun(Base):
    __tablename__ = "briefing_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    edition: Mapped[str] = mapped_column(String(16))  # morning | evening
    edition_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD in display timezone
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(16))  # success | no_content | failed
    story_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationLog(Base):
    """One row per (edition, date, channel, recipient). The unique key blocks duplicate sends on reruns."""
    __tablename__ = "notification_delivery_logs"
    __table_args__ = (UniqueConstraint("edition", "edition_date", "channel", "recipient",
                                       name="uq_notification_once"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    briefing_id: Mapped[int] = mapped_column(ForeignKey("briefing_runs.id"))
    edition: Mapped[str] = mapped_column(String(16))
    edition_date: Mapped[str] = mapped_column(String(10))
    channel: Mapped[str] = mapped_column(String(16))  # email
    recipient: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))  # sent | failed | skipped
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


# ---------- user accounts and personalization ----------

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)  # stored lowercase
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserSession(Base):
    """Server-side session. Only a SHA-256 hash of the cookie token is stored."""
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class UserPreference(Base):
    __tablename__ = "user_preferences"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ["en"])
    categories: Mapped[list] = mapped_column(JSON, default=list)  # ordered priority; empty = all
    countries: Mapped[list] = mapped_column(JSON, default=list)
    stories_per_section: Mapped[int] = mapped_column(Integer, default=3)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    morning_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    morning_time: Mapped[str] = mapped_column(String(5), default="07:00")  # HH:MM in user's timezone
    evening_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    evening_time: Mapped[str] = mapped_column(String(5), default="19:00")
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class SavedArticle(Base):
    __tablename__ = "saved_articles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ReadingHistory(Base):
    __tablename__ = "reading_history"
    __table_args__ = (Index("ix_history_user_read", "user_id", "read_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id", ondelete="CASCADE"))
    read_at: Mapped[datetime] = mapped_column(DateTime)


class UserFollow(Base):
    """Saved topics and publishers."""
    __tablename__ = "user_follows"
    __table_args__ = (UniqueConstraint("user_id", "kind", "value", name="uq_follow"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # topic | publisher
    value: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime)
