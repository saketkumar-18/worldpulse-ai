import json
import os
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db
from app.briefing import build_briefing, importance
from app.config import Settings
from app.ingest import store_article
from app.models import AISummary, Article, NewsEvent, NotificationLog, Provider, Publisher
from app.normalize import ArticleInput, utcnow
from app.pipeline import deliver_briefing, run_ingestion, summarize_pending
from app.registry import load_registry, seed_categories, sync_publishers


def make_settings(**kw) -> Settings:
    base = dict(database_url="sqlite:///:memory:", gnews_api_key="", newsapi_api_key="",
                admin_token="test-admin", smtp_host="", email_from="", notify_recipients="")
    base.update(kw)
    return Settings(_env_file=None, **base)


@pytest.fixture()
def seeded(session):
    seed_categories(session)
    sync_publishers(session, load_registry())
    return session


def art(title, url, publisher="The Hindu", hours_ago=1, lang="en", hint="business", desc=""):
    return ArticleInput(title=title, url=url, publisher_name=publisher, provider="rss",
                        language=lang, country="IN", description=desc, category_hint=hint,
                        published_at=utcnow() - timedelta(hours=hours_ago))


def test_store_dedupes_by_canonical_url_and_by_publisher_title(seeded):
    pubs = {p.name.lower(): p for p in seeded.query(Publisher).all()}
    stats = {"inserted": 0, "duplicates": 0}
    assert store_article(seeded, art("RBI holds rates", "https://www.thehindu.com/a?utm_source=x"), pubs, stats)
    assert not store_article(seeded, art("RBI holds rates", "https://thehindu.com/a"), pubs, stats)
    assert not store_article(seeded, art("RBI holds rates!", "https://thehindu.com/b"), pubs, stats)
    assert stats == {"inserted": 1, "duplicates": 2}


def test_same_event_from_two_publishers_is_clustered_and_counted(seeded):
    pubs = {p.name.lower(): p for p in seeded.query(Publisher).all()}
    stats = {"inserted": 0, "duplicates": 0}
    store_article(seeded, art("Reserve Bank keeps repo rate unchanged amid inflation", "https://thehindu.com/x1", "The Hindu"), pubs, stats)
    store_article(seeded, art("Reserve Bank keeps repo rate unchanged amid inflation worries", "https://business-standard.com/x2", "Business Standard"), pubs, stats)
    events = seeded.scalars(select(NewsEvent)).all()
    assert len(events) == 1
    assert events[0].publisher_count == 2


def test_importance_decays_with_age_and_ignores_truth_claims():
    now = utcnow()
    fresh = importance(now, 0.9, 1, False, now=now)
    old = importance(now - timedelta(hours=48), 0.9, 1, False, now=now)
    assert fresh > old


def test_ingestion_failure_does_not_break_other_providers(seeded, monkeypatch):
    settings = make_settings(gnews_api_key="K")

    def boom_http(req):
        raise httpx.ConnectError("down")

    http = httpx.Client(transport=httpx.MockTransport(boom_http))
    results = run_ingestion(seeded, settings, http=http)
    statuses = {r["provider"]: r["status"] for r in results}
    assert statuses.get("gnews") == "failed"
    job = seeded.scalars(select(Provider).where(Provider.name == "gnews")).one()
    assert job.last_error and "K" not in job.last_error.replace("KEY", "")


def test_summarize_falls_back_when_llm_unavailable(seeded):
    seeded.add(Article(canonical_url="https://thehindu.com/z", url="https://thehindu.com/z", title="Story title here",
                       title_hash="h", description="One. Two. Three.", publisher_name="The Hindu", provider="rss",
                       language="en", country="IN", category="business", published_at=utcnow(), fetched_at=utcnow(),
                       updated_at=utcnow()))
    seeded.commit()
    counts = summarize_pending(seeded, make_settings(), llm=type("X", (), {"enabled": False})())
    assert counts["extractive_fallback"] == 1
    row = seeded.scalars(select(AISummary)).one()
    assert row.needs_review is True and row.generator == "extractive_fallback"


def test_briefing_is_saved_and_email_sent_once_per_recipient(seeded, monkeypatch):
    seeded.add(Article(canonical_url="https://thehindu.com/q", url="https://thehindu.com/q", title="Market rises",
                       title_hash="q", description="", publisher_name="The Hindu", provider="rss", language="en",
                       country="IN", category="business", published_at=utcnow(), fetched_at=utcnow(), updated_at=utcnow()))
    seeded.commit()
    sent = []
    import app.pipeline as pl

    def fake_send(settings, recipient, payload, http=None):
        sent.append(recipient)
        return "sent", "ok"

    monkeypatch.setattr(pl, "deliver_email", fake_send)
    settings = make_settings(notify_recipients="a@example.com,a@example.com")
    first = deliver_briefing(seeded, settings, "morning")
    second = deliver_briefing(seeded, settings, "morning")
    assert first["status"] == "success"
    assert sent == ["a@example.com"]  # duplicate recipient and reruns never re-send
    assert second["deliveries"] == [{"recipient": "a@example.com", "status": "already_sent"}]


def test_email_skipped_cleanly_without_smtp():
    from app.notify import deliver_email
    status, detail = deliver_email(make_settings(), "x@example.com", {"edition": "morning", "sections": []})
    assert status == "skipped"


def test_html_email_escapes_untrusted_titles():
    from app.notify import render_html
    payload = {"edition": "morning", "sections": [{"label": "World", "stories": [
        {"title": "<script>alert(1)</script>", "url": "https://x.com/a\"onmouseover=x", "publisher": "P",
         "summary": None, "published_at": "t"}]}]}
    out = render_html(payload)
    assert "<script>" not in out


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1000")
    from app import config
    config.get_settings.cache_clear()
    from app.main import create_app
    app = create_app(f"sqlite:///{tmp_path / 'api.db'}")
    with TestClient(app) as c:
        yield c
    config.get_settings.cache_clear()


def test_public_news_endpoints_and_validation(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/news?category=bogus").status_code == 400
    assert client.get("/api/news?language=fr").status_code == 422
    assert client.get("/api/news?page_size=500").status_code == 422
    r = client.get("/api/news?category=world")
    assert r.status_code == 200 and r.json()["items"] == []


def test_search_uses_bound_parameters(client):
    r = client.get("/api/news?q=' OR 1=1 --")
    assert r.status_code == 200 and r.json()["total"] == 0


def test_admin_requires_token_and_never_echoes_secret(client):
    assert client.get("/api/admin/dashboard").status_code == 401
    assert client.get("/api/admin/dashboard", headers={"X-Admin-Token": "wrong"}).status_code == 401
    ok = client.get("/api/admin/dashboard", headers={"X-Admin-Token": "test-admin"})
    assert ok.status_code == 200
    assert "test-admin" not in ok.text


def test_status_endpoint_has_no_secrets(client):
    body = client.get("/api/status").text
    assert "KEY" not in body.upper() or "api key" not in body.lower()


def test_security_headers_present(client):
    r = client.get("/")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_registry_has_no_fabricated_epaper_links():
    for entry in load_registry():
        if entry.get("epaper_status") != "verified":
            assert entry.get("epaper_url") in (None, "")
