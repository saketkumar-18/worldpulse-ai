"""Orchestrates one full cycle: ingest all enabled providers, summarize, rank, build briefing, deliver.

Each stage is isolated: a failing provider or AI call is recorded and the cycle continues.
"""
from __future__ import annotations

import logging
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai import LLMClient, SummaryError, extractive_summary
from .briefing import build_briefing, recompute_scores, record_briefing
from .config import Settings
from .ingest import run_provider
from .models import AISummary, Article, BriefingRun, NotificationLog, Provider, Publisher
from .normalize import utcnow
from .notify import deliver_email
from .providers.gnews import GNewsAdapter
from .providers.newsapi import NewsAPIAdapter
from .providers.rss import RSSAdapter
from .registry import load_registry, seed_categories, sync_publishers
from .security import redact

log = logging.getLogger("worldpulse.pipeline")


def ensure_providers(session: Session, settings: Settings) -> None:
    defs = [
        ("gnews", "api", "https://gnews.io/api/v4/top-headlines", settings.gnews_category_list,
         settings.gnews_language_list, settings.gnews_daily_request_limit, bool(settings.gnews_api_key)),
        ("newsapi", "api", "https://newsapi.org/v2/top-headlines", settings.newsapi_category_list,
         ["en"], settings.newsapi_daily_request_limit, bool(settings.newsapi_api_key)),
        ("rss", "rss", "publisher registry", [], [], 1000, True),
    ]
    for name, kind, endpoint, cats, langs, limit, configured in defs:
        row = session.scalar(select(Provider).where(Provider.name == name))
        if row is None:
            row = Provider(name=name, kind=kind)
            session.add(row)
        row.endpoint, row.categories, row.languages = endpoint, cats, langs
        row.request_limit_per_day = limit
        if not configured and name != "rss":
            row.enabled = False
            row.last_error = row.last_error or "credentials not configured"
    session.commit()


def run_ingestion(session: Session, settings: Settings, http=None) -> list[dict]:
    """Fetch from every enabled provider. Returns a per-provider summary."""
    ensure_providers(session, settings)
    publisher_map = {p.name.lower(): p for p in session.scalars(select(Publisher)).all()}
    adapters = []
    if settings.gnews_api_key:
        adapters.append(("gnews", GNewsAdapter(settings.gnews_api_key, settings.gnews_language_list,
                                               settings.gnews_country_list, settings.gnews_category_list,
                                               settings.gnews_daily_request_limit, http=http)))
    if settings.newsapi_api_key:
        adapters.append(("newsapi", NewsAPIAdapter(settings.newsapi_api_key, settings.newsapi_country_list,
                                                   settings.newsapi_category_list,
                                                   settings.newsapi_daily_request_limit, http=http)))
    feeds = [{"publisher": p.name, "country": p.country, "language": p.language, "url": p.rss_url,
              "domain": p.domain} for p in session.scalars(select(Publisher).where(
                  Publisher.enabled.is_(True), Publisher.rss_url.is_not(None))).all()]
    if feeds:
        adapters.append(("rss", RSSAdapter(feeds, http=http)))

    results = []
    for name, adapter in adapters:
        row = session.scalar(select(Provider).where(Provider.name == name))
        if row is None or not row.enabled:
            results.append({"provider": name, "status": "skipped"})
            continue
        job = run_provider(session, adapter, row, publisher_map)
        results.append({"provider": name, "status": job.status, "inserted": job.inserted,
                        "duplicates": job.duplicates, "invalid": job.invalid, "error": job.error})
    return results


def summarize_pending(session: Session, settings: Settings, llm: LLMClient | None = None,
                      limit: int | None = None) -> dict:
    """Create summaries for articles that do not yet have one. LLM first, extractive fallback."""
    llm = llm or LLMClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model)
    limit = limit or settings.llm_max_articles_per_run
    pending = session.scalars(
        select(Article).outerjoin(AISummary, AISummary.article_id == Article.id)
        .where(AISummary.id.is_(None)).order_by(Article.importance_score.desc()).limit(limit)
    ).all()
    counts = {"llm": 0, "extractive_fallback": 0, "errors": 0}
    for a in pending:
        try:
            if llm.enabled:
                data = llm.summarize(a.title, a.publisher_name, a.url, a.description or a.title)
                generator, model = "llm", data["model"]
                if data["category"] and a.category_source != "provider":
                    a.category = data["category"]
                    a.category_source = "llm"
                row = AISummary(article_id=a.id, headline_en=data["headline_en"][:300],
                                detail_en=data["detail_en"], headline_hi=data["headline_hi"][:300],
                                detail_hi=data["detail_hi"], category_assigned=data["category"],
                                claim_type=data["claim_type"], generator="llm", model=model,
                                needs_review=data["needs_review"], created_at=utcnow())
            else:
                data = extractive_summary(a.title, a.description)
                row = AISummary(article_id=a.id, headline_en=data["headline_en"],
                                detail_en=data["detail_en"], generator="extractive_fallback",
                                needs_review=True, created_at=utcnow())
            session.add(row)
            counts[row.generator] += 1
        except (SummaryError, Exception) as exc:  # AI failure must not stop the cycle
            counts["errors"] += 1
            log.warning("summary failed for article %s: %s", a.id, redact(str(exc)))
    session.commit()
    return counts


def deliver_briefing(session: Session, settings: Settings, edition: str, http=None) -> dict:
    """Build, store and send the briefing. Idempotent per (briefing, channel, recipient)."""
    payload = build_briefing(session, edition)
    run = record_briefing(session, edition, payload)
    outcome = {"briefing_id": run.id, "status": run.status, "story_count": run.story_count, "deliveries": []}
    if run.status != "success":
        return outcome
    seen: set[str] = set()  # dedupe within this run (autoflush is off)
    for recipient in dict.fromkeys(r.lower() for r in settings.recipients):
        if recipient in seen:
            continue
        seen.add(recipient)
        key = dict(edition=edition, edition_date=run.edition_date, channel="email", recipient=recipient)
        already = session.scalar(select(NotificationLog).filter_by(**key))
        if already:
            outcome["deliveries"].append({"recipient": recipient, "status": "already_sent"})
            continue
        status, detail = deliver_email(settings, recipient, payload, http=http)
        session.add(NotificationLog(briefing_id=run.id, created_at=utcnow(), detail=detail, status=status, **key))
        session.flush()
        outcome["deliveries"].append({"recipient": recipient, "status": status})
    session.commit()
    return outcome


def deliver_personal_briefings(session: Session, settings: Settings, edition: str,
                               now_utc=None, http=None, public_base: str | None = None) -> dict:
    """Send each opted-in user their own briefing when their chosen time is due.

    Idempotent per (edition, IST date, email): the notification unique key blocks reruns.
    A failure for one user is logged and does not stop the others.
    """
    from zoneinfo import ZoneInfo
    from .briefing import briefing_due, build_personal_briefing, edition_date
    from .models import NotificationPreference, User, UserPreference

    now_utc = now_utc or utcnow()
    base = (public_base or settings.app_base_url).rstrip("/")
    outcome = {"considered": 0, "sent": 0, "skipped_not_due": 0, "skipped_empty": 0,
               "already_sent": 0, "failed": 0}
    edition_flag = (NotificationPreference.morning_enabled if edition == "morning"
                    else NotificationPreference.evening_enabled)
    rows = session.execute(
        select(User, NotificationPreference, UserPreference)
        .join(NotificationPreference, NotificationPreference.user_id == User.id)
        .join(UserPreference, UserPreference.user_id == User.id)
        .where(User.is_active.is_(True), NotificationPreference.email_enabled.is_(True),
               edition_flag.is_(True))
    ).all()
    for user, notif, prefs in rows:
        outcome["considered"] += 1
        try:
            local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(user.timezone))
            target = getattr(notif, f"{edition}_time")
            if not briefing_due(local_now, target):
                outcome["skipped_not_due"] += 1
                continue
            local_date = local_now.date().isoformat()
            key = dict(edition=edition, edition_date=local_date, channel="email", recipient=user.email)
            if session.scalar(select(NotificationLog.id).filter_by(**key)):
                outcome["already_sent"] += 1
                continue
            payload = build_personal_briefing(session, prefs, edition, manage_url=f"{base}/#/notifications")
            if not payload["sections"]:
                outcome["skipped_empty"] += 1
                # Log the decision so a later run in the same window does not re-evaluate it forever.
                run = BriefingRun(edition=edition, edition_date=local_date, created_at=utcnow(),
                                  status="no_content", story_count=0, payload={"user_id": user.id})
                session.add(run)
                session.flush()
                session.add(NotificationLog(briefing_id=run.id, created_at=utcnow(), status="skipped",
                                            detail="no matching stories", **key))
                session.commit()
                continue
            status, detail = deliver_email(settings, user.email, payload, http=http)
            run = BriefingRun(edition=edition, edition_date=local_date, created_at=utcnow(),
                              status="success" if status == "sent" else "failed",
                              story_count=sum(len(x["stories"]) for x in payload["sections"]),
                              payload={"user_id": user.id}, error=None if status == "sent" else detail)
            session.add(run)
            session.flush()
            session.add(NotificationLog(briefing_id=run.id, created_at=utcnow(), status=status,
                                        detail=detail, **key))
            session.commit()
            if status == "sent":
                outcome["sent"] += 1
            else:
                outcome["failed"] += 1
        except Exception as exc:  # one user's failure must not stop the rest
            session.rollback()
            outcome["failed"] += 1
            log.warning("personal briefing failed for user %s: %s", user.id, redact(str(exc)))
    return outcome


def run_cycle(session: Session, settings: Settings, edition: str | None = None,
              http=None, llm: LLMClient | None = None, deliver: bool = True) -> dict:
    """Full cycle used by the scheduler: collect -> summarize -> rank -> briefing -> deliver."""
    report: dict = {"started_at": utcnow().isoformat() + "Z"}
    seed_categories(session)
    sync_publishers(session, load_registry())
    report["ingestion"] = run_ingestion(session, settings, http=http)
    report["summaries"] = summarize_pending(session, settings, llm=llm)
    report["ranked"] = recompute_scores(session)
    if edition and deliver:
        report["briefing"] = deliver_briefing(session, settings, edition, http=http)
        report["personal_briefings"] = deliver_personal_briefings(session, settings, edition, http=http)
    report["finished_at"] = utcnow().isoformat() + "Z"
    return report


def purge_old(session: Session, retention_days: int) -> int:
    """Delete articles (and their summaries) older than the retention window."""
    cutoff = utcnow() - timedelta(days=retention_days)
    old = session.scalars(select(Article.id).where(Article.published_at < cutoff)).all()
    if not old:
        return 0
    session.query(AISummary).filter(AISummary.article_id.in_(old)).delete(synchronize_session=False)
    session.query(Article).filter(Article.id.in_(old)).delete(synchronize_session=False)
    session.commit()
    return len(old)
