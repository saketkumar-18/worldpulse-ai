"""Ingestion pipeline: fetch -> normalize -> dedupe -> categorize -> store -> cluster.

A failing provider is recorded in ingestion_jobs and never stops other providers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Article, IngestionJob, NewsEvent, Provider, Publisher, IntegrationHealthLog
from .normalize import (ArticleInput, canonical_url, classify_heuristic, title_hash, title_tokens, utcnow)
from .security import redact

log = logging.getLogger("worldpulse.ingest")
CLUSTER_WINDOW = timedelta(hours=36)
CLUSTER_MIN_OVERLAP = 0.5


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_event(session: Session, title: str, category: str, published: datetime) -> NewsEvent | None:
    """Best matching open event by title-token overlap within the time window."""
    tokens = title_tokens(title)
    if len(tokens) < 3:
        return None
    candidates = session.scalars(
        select(NewsEvent).where(NewsEvent.category == category,
                                NewsEvent.last_seen_at >= published - CLUSTER_WINDOW)
    ).all()
    best, best_score = None, 0.0
    for ev in candidates:
        score = jaccard(tokens, title_tokens(ev.title))
        if score > best_score:
            best, best_score = ev, score
    return best if best_score >= CLUSTER_MIN_OVERLAP else None


def store_article(session: Session, art: ArticleInput, publisher_map: dict[str, Publisher],
                  stats: dict) -> bool:
    """Insert one article. Returns False if it is a duplicate (by canonical URL or same publisher+title)."""
    canon = canonical_url(art.url)
    t_hash = title_hash(art.title)
    if session.scalar(select(Article.id).where(Article.canonical_url == canon)):
        stats["duplicates"] += 1
        return False
    if session.scalar(select(Article.id).where(Article.title_hash == t_hash,
                                              Article.publisher_name == art.publisher_name)):
        stats["duplicates"] += 1
        return False

    category = classify_heuristic(art.title, art.description, art.category_hint)
    category_source = "provider" if art.category_hint and category == art.category_hint else "heuristic"
    now = utcnow()
    pub = publisher_map.get(art.publisher_name.lower())
    event = find_event(session, art.title, category, art.published_at)
    if event is None:
        event = NewsEvent(title=art.title[:512], category=category, first_seen_at=art.published_at,
                          last_seen_at=art.published_at, article_count=0, publisher_count=0)
        session.add(event)
        session.flush()
    event.article_count += 1
    event.last_seen_at = max(event.last_seen_at, art.published_at)

    row = Article(
        canonical_url=canon[:2048], url=art.url[:2048], title=art.title[:512], title_hash=t_hash,
        description=art.description[:2000], publisher_name=art.publisher_name[:128],
        publisher_id=pub.id if pub else None, source_domain=(art.source_domain or "")[:255],
        provider=art.provider, language=art.language, country=art.country[:8],
        category=category, category_source=category_source,
        image_url=(art.image_url or None) and art.image_url[:2048],
        published_at=art.published_at, fetched_at=now, updated_at=now, event_id=event.id,
        is_breaking=(now - art.published_at) < timedelta(hours=3),
    )
    session.add(row)
    session.flush()
    # Publisher count per event = distinct publishers among its articles.
    event.publisher_count = len(set(session.scalars(
        select(Article.publisher_name).where(Article.event_id == event.id)).all()))
    stats["inserted"] += 1
    return True


def run_provider(session: Session, adapter, provider_row: Provider, publisher_map: dict) -> IngestionJob:
    job = IngestionJob(provider=adapter.name, started_at=utcnow(), status="running")
    session.add(job)
    session.flush()
    stats = {"inserted": 0, "duplicates": 0}
    try:
        result = adapter.fetch()
        for art in result.articles:
            try:
                with session.begin_nested():  # SAVEPOINT: a bad item rolls back alone
                    store_article(session, art, publisher_map, stats)
            except Exception as exc:  # one bad item must not abort the run
                log.warning("skipping article: %s", redact(str(exc)))
                result.invalid += 1
        job.requests_made = result.requests_made
        job.articles_fetched = len(result.articles)
        job.invalid = result.invalid
        job.inserted = stats["inserted"]
        job.duplicates = stats["duplicates"]
        if result.errors and stats["inserted"] == 0 and not result.articles:
            job.status = "failed"
        else:
            job.status = "partial" if result.errors else "success"
        job.error = redact("; ".join(result.errors))[:4000] or None
        if job.status in ("success", "partial"):
            provider_row.last_success_at = utcnow()
        if result.errors:
            provider_row.last_error = job.error
            provider_row.last_error_at = utcnow()
        provider_row.articles_imported = (provider_row.articles_imported or 0) + stats["inserted"]
    except Exception as exc:  # adapter bug or unexpected failure: record, do not crash the app
        job.status = "failed"
        job.error = redact(f"{type(exc).__name__}: {exc}")[:4000]
        provider_row.last_error = job.error
        provider_row.last_error_at = utcnow()
    job.finished_at = utcnow()
    session.add(IntegrationHealthLog(provider=adapter.name, checked_at=utcnow(),
                                     ok=job.status in ("success", "partial"), detail=(job.error or "ok")[:1000]))
    session.commit()
    return job
