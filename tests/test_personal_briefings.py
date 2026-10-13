from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.briefing import briefing_due
from app.config import Settings
from app.models import (Article, NotificationLog, NotificationPreference, User, UserPreference, BriefingRun)
from app.normalize import utcnow
from app.pipeline import deliver_personal_briefings
from app.registry import seed_categories


def settings(**kw):
    base = dict(database_url="sqlite:///:memory:", smtp_host="smtp.test", email_from="x@test",
                smtp_use_tls=False, admin_token="t", notify_recipients="", gnews_api_key="", newsapi_api_key="")
    base.update(kw)
    return Settings(_env_file=None, **base)


def ist(h, m):
    return datetime(2026, 10, 13, h, m, tzinfo=ZoneInfo("Asia/Kolkata"))


@pytest.mark.parametrize("now,target,expected", [
    ((7, 0), "07:00", True),      # exactly on time
    ((7, 45), "07:00", True),     # inside catch-up window
    ((6, 59), "07:00", False),    # too early
    ((13, 30), "07:00", False),   # hours later than the window
])
def test_briefing_due_window(now, target, expected):
    assert briefing_due(ist(*now), target) is expected


def make_user(session, email, morning=True, email_on=True, cats=None, langs=None, tz="Asia/Kolkata", time="07:00"):
    now = utcnow()
    u = User(email=email, password_hash="x", timezone=tz, created_at=now, is_active=True)
    session.add(u); session.flush()
    session.add(UserPreference(user_id=u.id, languages=langs or ["en"], categories=cats or [], countries=[],
                               stories_per_section=3, updated_at=now))
    session.add(NotificationPreference(user_id=u.id, email_enabled=email_on, morning_enabled=morning,
                                       morning_time=time, evening_enabled=False, updated_at=now))
    session.commit()
    return u


def add_story(session, title, category, hours_ago=1, lang="en"):
    n = utcnow()
    session.add(Article(canonical_url=f"https://thehindu.com/{title.replace(' ', '-')}",
                        url=f"https://thehindu.com/{title.replace(' ', '-')}", title=title,
                        title_hash=title, description="", publisher_name="The Hindu", provider="rss",
                        language=lang, country="IN", category=category,
                        published_at=n - timedelta(hours=hours_ago), fetched_at=n, updated_at=n))
    session.commit()


def run_at_ist(session, sent, hour=7, minute=10, monkeypatch=None):
    import app.pipeline as pl
    def fake(settings, recipient, payload, http=None):
        sent.append((recipient, payload))
        return "sent", "ok"
    pl.deliver_email = fake
    now_utc = ist(hour, minute).astimezone(timezone.utc).replace(tzinfo=None)
    return deliver_personal_briefings(session, settings(), "morning", now_utc=now_utc)


def test_each_user_gets_only_their_preferred_categories(session, monkeypatch):
    seed_categories(session)
    add_story(session, "Sensex rises on bank rally", "business")
    add_story(session, "Cricket team wins final", "sports")
    make_user(session, "biz@example.com", cats=["business"])
    make_user(session, "sport@example.com", cats=["sports"])
    sent = []
    out = run_at_ist(session, sent)
    got = {r: [s["label"] for s in p["sections"]] for r, p in sent}
    assert got["biz@example.com"] == ["Business & Economy"]
    assert got["sport@example.com"] == ["Sports"]
    assert out["sent"] == 2


def test_rerun_never_resends_to_same_user(session, monkeypatch):
    seed_categories(session)
    add_story(session, "Market closes higher", "business")
    make_user(session, "once@example.com", cats=["business"])
    sent = []
    first = run_at_ist(session, sent)
    second = run_at_ist(session, sent)
    assert first["sent"] == 1
    assert second["sent"] == 0 and second["already_sent"] == 1
    assert len(sent) == 1


def test_users_outside_their_time_or_opted_out_are_not_sent(session, monkeypatch):
    seed_categories(session)
    add_story(session, "Rupee steadies", "business")
    make_user(session, "later@example.com", cats=["business"], time="19:00")
    make_user(session, "off@example.com", cats=["business"], email_on=False)
    sent = []
    out = run_at_ist(session, sent, hour=7, minute=10)
    assert sent == []
    assert out["sent"] == 0


def test_empty_briefing_is_recorded_but_not_emailed(session, monkeypatch):
    seed_categories(session)
    make_user(session, "quiet@example.com", cats=["sports"])
    sent = []
    out = run_at_ist(session, sent)
    assert sent == []
    assert out["skipped_empty"] == 1
    log = session.scalars(select(NotificationLog).where(NotificationLog.recipient == "quiet@example.com")).all()
    assert len(log) == 1 and log[0].status == "skipped"


def test_one_failed_send_does_not_block_others(session, monkeypatch):
    import app.pipeline as pl
    seed_categories(session)
    add_story(session, "Shares climb", "business")
    make_user(session, "bad@example.com", cats=["business"])
    make_user(session, "good@example.com", cats=["business"])
    calls = []

    def flaky(settings, recipient, payload, http=None):
        calls.append(recipient)
        if recipient == "bad@example.com":
            return "failed", "smtp down"
        return "sent", "ok"
    pl.deliver_email = flaky
    now_utc = ist(7, 10).astimezone(timezone.utc).replace(tzinfo=None)
    out = deliver_personal_briefings(session, settings(), "morning", now_utc=now_utc)
    assert out["sent"] == 1 and out["failed"] == 1
    assert set(calls) == {"bad@example.com", "good@example.com"}


def test_manage_link_points_to_notifications_page(session, monkeypatch):
    seed_categories(session)
    add_story(session, "Trade deal signed", "business")
    make_user(session, "link@example.com", cats=["business"])
    captured = {}
    import app.pipeline as pl
    def cap(settings, recipient, payload, http=None):
        captured["payload"] = payload
        return "sent", "ok"
    pl.deliver_email = cap
    now_utc = ist(7, 10).astimezone(timezone.utc).replace(tzinfo=None)
    deliver_personal_briefings(session, settings(), "morning", now_utc=now_utc, public_base="https://example.onrender.com")
    assert captured["payload"]["manage_url"] == "https://example.onrender.com/#/notifications"
