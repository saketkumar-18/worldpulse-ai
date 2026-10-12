import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth import hash_password, verify_password, validate_password, validate_email


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1000")
    config.get_settings.cache_clear()
    from app.main import create_app
    app = create_app(f"sqlite:///{tmp_path / 'u.db'}")
    with TestClient(app) as c:
        yield c
    config.get_settings.cache_clear()


PW = "correct-horse-1"


def test_password_hash_roundtrip_and_no_plaintext():
    h = hash_password(PW)
    assert PW not in h and h.startswith("scrypt$")
    assert verify_password(PW, h)
    assert not verify_password("wrong-password-9", h)
    assert not verify_password(PW, "garbage")


@pytest.mark.parametrize("pw", ["short1", "allletters", "1234567890123", "x" * 129])
def test_weak_passwords_rejected(pw):
    with pytest.raises(ValueError):
        validate_password(pw)


def test_email_validation_normalizes_and_rejects():
    assert validate_email("  Saket@Example.COM ") == "saket@example.com"
    with pytest.raises(ValueError):
        validate_email("not-an-email")


def test_register_login_me_logout_flow(client):
    r = client.post("/api/auth/register", json={"email": "a@example.com", "password": PW})
    assert r.status_code == 201 and "password" not in r.text
    assert "wp_session" in r.cookies or "wp_session" in client.cookies
    assert client.get("/api/me").json()["email"] == "a@example.com"
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me").status_code == 401
    ok = client.post("/api/auth/login", json={"email": "A@example.com", "password": PW})
    assert ok.status_code == 200


def test_login_error_is_generic(client):
    client.post("/api/auth/register", json={"email": "b@example.com", "password": PW})
    bad_pw = client.post("/api/auth/login", json={"email": "b@example.com", "password": "wrong-pass-1"})
    no_user = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-pass-1"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json() == no_user.json()


def test_duplicate_registration_rejected(client):
    client.post("/api/auth/register", json={"email": "c@example.com", "password": PW})
    r = client.post("/api/auth/register", json={"email": "c@example.com", "password": PW})
    assert r.status_code == 409


def test_session_cookie_is_httponly_and_samesite(client):
    r = client.post("/api/auth/register", json={"email": "d@example.com", "password": PW})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_user_endpoints_require_auth(client):
    for method, path in [("get", "/api/me/preferences"), ("get", "/api/me/saved"), ("get", "/api/me/feed"),
                         ("put", "/api/me/notifications"), ("get", "/api/me/history")]:
        r = client.put(path, json={}) if method == "put" else client.get(path)
        assert r.status_code == 401, path


def test_preferences_validation_and_roundtrip(client):
    client.post("/api/auth/register", json={"email": "e@example.com", "password": PW})
    bad = client.put("/api/me/preferences", json={"categories": ["nonsense"]})
    assert bad.status_code == 422
    bad_lang = client.put("/api/me/preferences", json={"languages": ["fr"]})
    assert bad_lang.status_code == 422
    ok = client.put("/api/me/preferences", json={"languages": ["hi", "en"], "categories": ["business", "world", "business"],
                                                 "countries": ["in"], "stories_per_section": 5})
    assert ok.status_code == 200
    body = ok.json()
    assert body["categories"] == ["business", "world"]  # de-duplicated, priority order kept
    assert body["countries"] == ["IN"]


def test_notification_times_validated(client):
    client.post("/api/auth/register", json={"email": "f@example.com", "password": PW})
    assert client.put("/api/me/notifications", json={"morning_time": "25:00"}).status_code == 422
    assert client.put("/api/me/notifications", json={"morning_time": "7:00"}).status_code == 422
    ok = client.put("/api/me/notifications", json={"email_enabled": True, "morning_time": "06:30"})
    assert ok.status_code == 200 and ok.json()["morning_time"] == "06:30"


def test_invalid_timezone_rejected(client):
    r = client.post("/api/auth/register", json={"email": "g@example.com", "password": PW, "timezone": "Mars/Base"})
    assert r.status_code == 422


def test_save_history_and_follow_flow(client):
    from app.models import Article
    from app import db
    from app.normalize import utcnow
    client.post("/api/auth/register", json={"email": "h@example.com", "password": PW})
    s = db.session_factory()()
    a = Article(canonical_url="https://thehindu.com/s1", url="https://thehindu.com/s1", title="Save me story",
                title_hash="s1", description="", publisher_name="The Hindu", provider="rss", language="en",
                country="IN", category="business", published_at=utcnow(), fetched_at=utcnow(), updated_at=utcnow())
    s.add(a); s.commit(); aid = a.id; s.close()

    assert client.put(f"/api/me/saved/{aid}").status_code == 204
    assert client.put(f"/api/me/saved/{aid}").status_code == 204  # idempotent
    saved = client.get("/api/me/saved").json()
    assert saved["total"] == 1 and saved["items"][0]["title"] == "Save me story"
    assert client.put("/api/me/saved/999999").status_code == 404

    assert client.post(f"/api/me/history/{aid}").status_code == 204
    hist = client.get("/api/me/history").json()
    assert hist["total"] == 1 and hist["items"][0]["read_at"].endswith("Z")

    f = client.post("/api/me/follows", json={"kind": "topic", "value": "sports"}).json()
    assert client.post("/api/me/follows", json={"kind": "topic", "value": "sports"}).json()["id"] == f["id"]
    assert client.post("/api/me/follows", json={"kind": "topic", "value": "bogus"}).status_code == 422
    assert client.post("/api/me/follows", json={"kind": "publisher", "value": "The Hindu"}).status_code == 201
    assert len(client.get("/api/me/follows").json()) == 2
    assert client.delete(f"/api/me/follows/{f['id']}").status_code == 204
    assert len(client.get("/api/me/follows").json()) == 1


def test_users_cannot_see_each_others_saved_items(client, tmp_path):
    from app.models import Article
    from app import db
    from app.normalize import utcnow
    s = db.session_factory()()
    a = Article(canonical_url="https://thehindu.com/p1", url="https://thehindu.com/p1", title="Private pick",
                title_hash="p1", description="", publisher_name="The Hindu", provider="rss", language="en",
                country="IN", category="world", published_at=utcnow(), fetched_at=utcnow(), updated_at=utcnow())
    s.add(a); s.commit(); aid = a.id; s.close()

    client.post("/api/auth/register", json={"email": "one@example.com", "password": PW})
    client.put(f"/api/me/saved/{aid}")
    client.post("/api/auth/logout")
    client.post("/api/auth/register", json={"email": "two@example.com", "password": PW})
    assert client.get("/api/me/saved").json()["total"] == 0


def test_admin_dashboard_still_requires_token_not_session(client):
    client.post("/api/auth/register", json={"email": "admin-probe@example.com", "password": PW})
    assert client.get("/api/admin/dashboard").status_code == 401
