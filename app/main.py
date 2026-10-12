"""WorldPulse AI API. Serves the JSON API and the static frontend from /static."""
from __future__ import annotations

import hmac
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import db
from .briefing import SECTION_ORDER, build_briefing, serialize, top_stories
from .config import get_settings
from .models import (Article, BriefingRun, IngestionJob, IntegrationHealthLog, NewsCategory,
                     NewsEvent, Provider, Publisher)
from .normalize import CATEGORIES, utcnow
from .pipeline import run_cycle, purge_old
from .user_routes import auth_router, router as me_router

log = logging.getLogger("worldpulse.api")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class RateLimiter:
    """In-process sliding-window limiter per client IP. Use Redis or the edge in multi-instance deployments."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


def create_app(database_url: str | None = None, create_schema: bool = True) -> FastAPI:
    settings = get_settings()
    db.configure(database_url or settings.database_url, create_schema=create_schema)
    limiter = RateLimiter(settings.rate_limit_per_minute)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        session = db.session_factory()()
        try:
            from .registry import load_registry, seed_categories, sync_publishers
            seed_categories(session)
            sync_publishers(session, load_registry())
        finally:
            session.close()
        yield

    app = FastAPI(title="WorldPulse AI", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=[settings.app_base_url], allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "X-Admin-Token"],
                       allow_credentials=True)

    @app.middleware("http")
    async def security_headers_and_rate_limit(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            ip = request.client.host if request.client else "unknown"
            if not limiter.check(ip):
                return JSONResponse({"detail": "rate limit exceeded"}, status_code=429,
                                    headers={"Retry-After": "60"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        return response

    def get_session():
        yield from db.get_db()

    def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
        if not settings.admin_token:
            raise HTTPException(status_code=503, detail="admin disabled: ADMIN_TOKEN not set")
        if not x_admin_token or not hmac.compare_digest(x_admin_token, settings.admin_token):
            raise HTTPException(status_code=401, detail="unauthorized")

    def iso(dt) -> str | None:
        return dt.isoformat() + "Z" if dt else None

    # ---------- public API ----------
    @app.get("/api/health")
    def health(session: Session = Depends(get_session)):
        last = session.scalar(select(func.max(IngestionJob.finished_at)).where(
            IngestionJob.status.in_(["success", "partial"])))
        return {"status": "ok", "time": iso(utcnow()), "last_successful_collection": iso(last)}

    @app.get("/api/categories")
    def categories():
        return [{"slug": s, "label": l} for s, l in CATEGORIES]

    @app.get("/api/news")
    def news(
        category: str | None = Query(None, max_length=32),
        language: Literal["en", "hi"] | None = None,
        country: str | None = Query(None, min_length=2, max_length=8),
        publisher: str | None = Query(None, max_length=128),
        q: str | None = Query(None, max_length=200),
        breaking: bool | None = None,
        since_hours: int = Query(72, ge=1, le=24 * 30),
        sort: Literal["newest", "relevance"] = "newest",
        page: int = Query(1, ge=1, le=200),
        page_size: int = Query(20, ge=1, le=50),
        session: Session = Depends(get_session),
    ):
        valid = {s for s, _ in CATEGORIES}
        if category and category not in valid:
            raise HTTPException(400, "unknown category")
        query = select(Article).where(Article.published_at >= utcnow() - timedelta(hours=since_hours))
        if category:
            query = query.where(Article.category == category)
        if language:
            query = query.where(Article.language == language)
        if country:
            query = query.where(Article.country == country.upper())
        if publisher:
            query = query.where(Article.publisher_name == publisher)
        if breaking is not None:
            query = query.where(Article.is_breaking.is_(breaking))
        if q:
            term = f"%{q.strip()}%"  # bound parameter; no string interpolation into SQL
            query = query.where(or_(Article.title.ilike(term), Article.description.ilike(term)))
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        order = ((Article.importance_score.desc(), Article.published_at.desc()) if sort == "relevance"
                 else (Article.published_at.desc(),))
        rows = session.scalars(query.order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
        return {"page": page, "page_size": page_size, "total": total,
                "items": [serialize(session, a) for a in rows]}

    @app.get("/api/news/{article_id}")
    def article(article_id: int, session: Session = Depends(get_session)):
        a = session.get(Article, article_id)
        if not a:
            raise HTTPException(404, "article not found")
        return serialize(session, a)

    @app.get("/api/top")
    def top(category: str | None = Query(None, max_length=32), language: Literal["en", "hi"] | None = None,
            limit: int = Query(10, ge=1, le=30), session: Session = Depends(get_session)):
        return top_stories(session, category=category, language=language, limit=limit)

    @app.get("/api/breaking")
    def breaking(limit: int = Query(10, ge=1, le=30), session: Session = Depends(get_session)):
        rows = session.scalars(select(Article).where(Article.is_breaking.is_(True),
                                                     Article.published_at >= utcnow() - timedelta(hours=6))
                               .order_by(Article.published_at.desc()).limit(limit)).all()
        return [serialize(session, a) for a in rows]

    @app.get("/api/trending")
    def trending(hours: int = Query(24, ge=1, le=168), limit: int = Query(10, ge=1, le=30),
                 session: Session = Depends(get_session)):
        """Events ranked by how many distinct publishers covered them. Coverage count, not truth."""
        rows = session.scalars(select(NewsEvent).where(NewsEvent.last_seen_at >= utcnow() - timedelta(hours=hours))
                               .order_by(NewsEvent.publisher_count.desc(), NewsEvent.last_seen_at.desc())
                               .limit(limit)).all()
        out = []
        for ev in rows:
            if ev.publisher_count < 2:
                continue
            out.append({"event_id": ev.id, "title": ev.title, "category": ev.category,
                        "publisher_count": ev.publisher_count, "article_count": ev.article_count,
                        "last_seen_at": iso(ev.last_seen_at)})
        return out

    @app.get("/api/events/{event_id}/coverage")
    def event_coverage(event_id: int, session: Session = Depends(get_session)):
        """Compare how different publishers covered one event."""
        ev = session.get(NewsEvent, event_id)
        if not ev:
            raise HTTPException(404, "event not found")
        rows = session.scalars(select(Article).where(Article.event_id == event_id)
                               .order_by(Article.published_at.asc())).all()
        return {"event_id": event_id, "title": ev.title, "publisher_count": ev.publisher_count,
                "coverage": [serialize(session, a) for a in rows],
                "note": "Multiple publishers repeating one wire report are not independent confirmation."}

    @app.get("/api/briefing/{edition}")
    def briefing(edition: Literal["morning", "evening"], per_section: int = Query(3, ge=1, le=10),
                 session: Session = Depends(get_session)):
        return build_briefing(session, edition, per_section=per_section)

    @app.get("/api/publishers")
    def publishers(country: str | None = Query(None, max_length=8), language: Literal["en", "hi"] | None = None,
                   session: Session = Depends(get_session)):
        q = select(Publisher).where(Publisher.enabled.is_(True))
        if country:
            q = q.where(Publisher.country == country.upper())
        if language:
            q = q.where(Publisher.language == language)
        return [{"slug": p.slug, "name": p.name, "country": p.country, "region": p.region,
                 "language": p.language, "website": p.website, "domain": p.domain,
                 "rss_status": p.rss_status, "epaper_status": p.epaper_status,
                 "epaper_url": p.epaper_url if p.epaper_status == "verified" else None,
                 "latest_edition_date": None,
                 "notes": p.notes} for p in session.scalars(q.order_by(Publisher.name)).all()]

    @app.get("/api/newspapers")
    def newspapers(session: Session = Depends(get_session)):
        """Newspaper Library. Only verified e-paper links are offered; otherwise the official website."""
        return publishers(country=None, language=None, session=session)

    @app.get("/api/publishers/compare")
    def compare(publisher_a: str = Query(..., max_length=128), publisher_b: str = Query(..., max_length=128),
                since_hours: int = Query(48, ge=1, le=168), session: Session = Depends(get_session)):
        """Compare the two publishers on articles covering the same events."""
        since = utcnow() - timedelta(hours=since_hours)
        a_events = set(session.scalars(select(Article.event_id).where(
            Article.publisher_name == publisher_a, Article.published_at >= since)).all())
        b_events = set(session.scalars(select(Article.event_id).where(
            Article.publisher_name == publisher_b, Article.published_at >= since)).all())
        a_events.discard(None), b_events.discard(None)
        shared = a_events & b_events
        return {"publisher_a": publisher_a, "publisher_b": publisher_b, "since_hours": since_hours,
                "shared_events": len(shared), "only_a": len(a_events - b_events),
                "only_b": len(b_events - a_events)}

    @app.get("/api/status")
    def status(session: Session = Depends(get_session)):
        """Public, secret-free integration status."""
        providers = session.scalars(select(Provider)).all()
        return [{"name": p.name, "kind": p.kind, "enabled": p.enabled, "last_success_at": iso(p.last_success_at),
                 "last_error": (p.last_error or None) and "see admin dashboard",
                 "articles_imported": p.articles_imported} for p in providers]

    # ---------- admin API (token required) ----------
    @app.get("/api/admin/dashboard", dependencies=[Depends(require_admin)])
    def dashboard(session: Session = Depends(get_session)):
        since_day = utcnow() - timedelta(days=1)
        total = session.scalar(select(func.count(Article.id)))
        today = session.scalar(select(func.count(Article.id)).where(Article.fetched_at >= since_day))
        by_cat = dict(session.execute(select(Article.category, func.count(Article.id))
                                      .group_by(Article.category)).all())
        by_country = dict(session.execute(select(Article.country, func.count(Article.id))
                                          .group_by(Article.country)).all())
        jobs = session.scalars(select(IngestionJob).order_by(IngestionJob.started_at.desc()).limit(20)).all()
        failed = [j for j in jobs if j.status == "failed"][:10]
        dup = session.scalar(select(func.sum(IngestionJob.duplicates)).where(IngestionJob.started_at >= since_day)) or 0
        ins = session.scalar(select(func.sum(IngestionJob.inserted)).where(IngestionJob.started_at >= since_day)) or 0
        return {
            "articles_total": total, "articles_last_24h": today,
            "by_category": by_cat, "by_country": by_country,
            "duplicate_stats_24h": {"duplicates": dup, "inserted": ins,
                                    "duplicate_rate": round(dup / (dup + ins), 3) if (dup + ins) else 0.0},
            "providers": [{"name": p.name, "enabled": p.enabled, "last_success_at": iso(p.last_success_at),
                           "last_error": p.last_error, "last_error_at": iso(p.last_error_at),
                           "articles_imported": p.articles_imported,
                           "request_limit_per_day": p.request_limit_per_day}
                          for p in session.scalars(select(Provider)).all()],
            "recent_jobs": [{"provider": j.provider, "status": j.status, "started_at": iso(j.started_at),
                             "requests": j.requests_made, "inserted": j.inserted, "duplicates": j.duplicates,
                             "invalid": j.invalid, "error": j.error} for j in jobs],
            "failed_jobs": [{"provider": j.provider, "started_at": iso(j.started_at), "error": j.error} for j in failed],
            "briefings": [{"id": b.id, "edition": b.edition, "status": b.status, "stories": b.story_count,
                           "created_at": iso(b.created_at), "error": b.error}
                          for b in session.scalars(select(BriefingRun).order_by(BriefingRun.created_at.desc()).limit(10)).all()],
            "publishers": [{"slug": p.slug, "name": p.name, "enabled": p.enabled, "rss_status": p.rss_status,
                            "epaper_status": p.epaper_status, "rss_last_error": p.rss_last_error}
                           for p in session.scalars(select(Publisher).order_by(Publisher.name)).all()],
        }

    @app.post("/api/admin/collect", dependencies=[Depends(require_admin)])
    def admin_collect(session: Session = Depends(get_session)):
        return run_cycle(session, settings, edition=None)

    @app.post("/api/admin/publishers/{slug}/enabled", dependencies=[Depends(require_admin)])
    def set_publisher_enabled(slug: str, enabled: bool, session: Session = Depends(get_session)):
        p = session.scalar(select(Publisher).where(Publisher.slug == slug))
        if not p:
            raise HTTPException(404, "publisher not found")
        p.enabled = enabled
        session.commit()
        return {"slug": slug, "enabled": enabled}

    @app.post("/api/admin/purge", dependencies=[Depends(require_admin)])
    def admin_purge(session: Session = Depends(get_session)):
        return {"purged_articles": purge_old(session, settings.retention_days)}

    app.include_router(auth_router)
    app.include_router(me_router)

    # ---------- frontend ----------
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    return app


app = create_app() if os.environ.get("WORLDPULSE_IMPORT_APP", "1") == "1" else None
