"""Ranking, clustering view, and daily briefing generation."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AISummary, Article, BriefingRun, NewsEvent, Publisher
from .normalize import CATEGORIES, utcnow

SECTION_ORDER = ["world", "india", "politics", "business", "technology", "science",
                 "health", "sports", "education", "environment", "entertainment", "regional"]
HALF_LIFE_HOURS = 12.0


def importance(published_at: datetime, reliability: float, event_publishers: int,
               is_breaking: bool, now: datetime | None = None) -> float:
    """Score = recency decay + source quality + independent-publisher coverage.

    Popularity is NOT truth: the score only orders what the reader sees. It does not
    mark any story as verified or true.
    """
    now = now or utcnow()
    age_h = max((now - published_at).total_seconds() / 3600.0, 0.0)
    recency = math.pow(0.5, age_h / HALF_LIFE_HOURS)
    coverage = min(math.log2(1 + event_publishers) / 3.0, 1.0)  # distinct publishers, capped
    return round(0.5 * recency + 0.3 * reliability + 0.1 * coverage + (0.1 if is_breaking else 0.0), 4)


def recompute_scores(session: Session) -> int:
    """Refresh importance for articles from the last 3 days."""
    cutoff = utcnow() - timedelta(days=3)
    rows = session.execute(
        select(Article, Publisher.reliability, NewsEvent.publisher_count)
        .outerjoin(Publisher, Publisher.id == Article.publisher_id)
        .outerjoin(NewsEvent, NewsEvent.id == Article.event_id)
        .where(Article.published_at >= cutoff)
    ).all()
    for article, rel, pubs in rows:
        article.importance_score = importance(article.published_at, rel or 0.6, pubs or 1, article.is_breaking)
    session.commit()
    return len(rows)


def top_stories(session: Session, category: str | None = None, language: str | None = None,
                limit: int = 10, since_hours: int = 72) -> list[dict]:
    q = select(Article).where(Article.published_at >= utcnow() - timedelta(hours=since_hours))
    if category:
        q = q.where(Article.category == category)
    if language:
        q = q.where(Article.language == language)
    q = q.order_by(Article.importance_score.desc(), Article.published_at.desc()).limit(limit)
    return [serialize(session, a) for a in session.scalars(q).all()]


def serialize(session: Session, a: Article) -> dict:
    summary = session.scalar(select(AISummary).where(AISummary.article_id == a.id))
    pubs = 1
    if a.event_id:
        ev = session.get(NewsEvent, a.event_id)
        pubs = ev.publisher_count if ev else 1
    return {
        "id": a.id, "title": a.title, "url": a.url, "publisher": a.publisher_name,
        "language": a.language, "country": a.country, "category": a.category,
        "published_at": a.published_at.isoformat() + "Z", "is_breaking": a.is_breaking,
        "image_url": a.image_url, "importance": a.importance_score,
        "event_id": a.event_id, "publisher_count": pubs, "provider": a.provider,
        "summary": None if summary is None else {
            "headline_en": summary.headline_en, "detail_en": summary.detail_en,
            "headline_hi": summary.headline_hi, "detail_hi": summary.detail_hi,
            "generator": summary.generator, "model": summary.model,
            "needs_review": summary.needs_review, "claim_type": summary.claim_type,
        },
    }


def build_briefing(session: Session, edition: str, per_section: int = 3) -> dict:
    """Structured briefing: top stories per section, plus distinct publisher counts and source diversity."""
    sections = []
    all_publishers: set[str] = set()
    for slug in SECTION_ORDER:
        stories = top_stories(session, category=slug, limit=per_section)
        if not stories:
            continue
        label = dict(CATEGORIES)[slug]
        sections.append({"slug": slug, "label": label, "stories": stories})
        all_publishers.update(s["publisher"] for s in stories)
    epapers = [
        {"name": p.name, "language": p.language, "url": p.epaper_url}
        for p in session.scalars(select(Publisher).where(Publisher.epaper_status == "verified")).all()
    ]
    return {"edition": edition, "generated_at": utcnow().isoformat() + "Z",
            "sections": sections, "publisher_count": len(all_publishers), "epapers": epapers}


def edition_date(now: datetime | None = None, tz_name: str = "Asia/Kolkata") -> str:
    """Calendar date of the edition in the display timezone (default Asia/Kolkata)."""
    from zoneinfo import ZoneInfo
    now = now or utcnow()
    return now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name)).date().isoformat()


def record_briefing(session: Session, edition: str, payload: dict) -> BriefingRun:
    count = sum(len(s["stories"]) for s in payload["sections"])
    run = BriefingRun(edition=edition, edition_date=edition_date(), created_at=utcnow(),
                      status="success" if count else "no_content",
                      story_count=count, payload=payload)
    session.add(run)
    session.commit()
    return run
