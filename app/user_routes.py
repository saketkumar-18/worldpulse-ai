"""User-facing API: accounts, preferences, notification settings, saved stories, history, follows.

All state-changing routes require a session. CSRF: the session cookie is SameSite=Lax and every
mutating route requires a JSON body (browsers cannot send cross-site JSON without CORS preflight,
and CORS is restricted to APP_BASE_URL).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .auth import (clear_session, create_session, dummy_verify, hash_password, normalize_email,
                   require_user, validate_email, validate_password, verify_password, current_user)
from .briefing import serialize
from .config import get_settings
from .db import get_db, utcnow
from .models import (Article, NotificationPreference, ReadingHistory, SavedArticle, User, UserFollow,
                     UserPreference)
from .normalize import CATEGORY_SLUGS

router = APIRouter(prefix="/api/me", tags=["account"])
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
    display_name: str | None = Field(default=None, max_length=80)
    timezone: str = "Asia/Kolkata"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown timezone")
        return v


class PreferencesIn(BaseModel):
    languages: list[Literal["en", "hi"]] = Field(default_factory=lambda: ["en"], min_length=1, max_length=2)
    categories: list[str] = Field(default_factory=list, max_length=12)
    countries: list[str] = Field(default_factory=list, max_length=10)
    stories_per_section: int = Field(default=3, ge=1, le=10)

    @field_validator("categories")
    @classmethod
    def _cats(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in CATEGORY_SLUGS]
        if bad:
            raise ValueError(f"unknown categories: {bad}")
        return list(dict.fromkeys(v))  # keep priority order, drop duplicates

    @field_validator("countries")
    @classmethod
    def _countries(cls, v: list[str]) -> list[str]:
        out = []
        for c in v:
            c = c.strip().upper()
            if len(c) != 2 or not c.isalpha():
                raise ValueError("countries must be 2-letter codes")
            out.append(c)
        return list(dict.fromkeys(out))


class NotificationsIn(BaseModel):
    email_enabled: bool = False
    morning_enabled: bool = True
    morning_time: str = Field(default="07:00", pattern=HHMM)
    evening_enabled: bool = False
    evening_time: str = Field(default="19:00", pattern=HHMM)


class FollowIn(BaseModel):
    kind: Literal["topic", "publisher"]
    value: str = Field(min_length=1, max_length=128)

    @field_validator("value")
    @classmethod
    def _value(cls, v: str) -> str:
        return v.strip()


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    timezone: str = "Asia/Kolkata"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown timezone")
        return v


def _user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "timezone": u.timezone}


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or get_settings().app_env == "production"


# ---------- auth ----------

@auth_router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    try:
        email = validate_email(body.email)
        validate_password(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if db.scalar(select(User.id).where(User.email == email)):
        # Same response shape as success would be unhelpful for enumeration; we accept the
        # standard trade-off here and keep the message generic.
        raise HTTPException(409, "could not create account with these details")
    now = utcnow()
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name,
                timezone=body.timezone, created_at=now)
    db.add(user)
    db.flush()
    db.add(UserPreference(user_id=user.id, languages=["en"], categories=[], countries=[],
                          stories_per_section=3, updated_at=now))
    db.add(NotificationPreference(user_id=user.id, updated_at=now))
    db.commit()
    create_session(db, user, response, _secure(request))
    return _user_out(user)


@auth_router.post("/login")
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    email = normalize_email(body.email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        dummy_verify(body.password)
        raise HTTPException(401, "email or password is incorrect")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "email or password is incorrect")
    create_session(db, user, response, _secure(request))
    return _user_out(user)


@auth_router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    clear_session(db, request, response)
    return Response(status_code=204)


# ---------- profile & preferences ----------

@router.get("")
def me(user: User = Depends(require_user)):
    return _user_out(user)


@router.put("")
def update_profile(body: ProfileIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    user.display_name = body.display_name
    user.timezone = body.timezone
    db.commit()
    return _user_out(user)


@router.get("/preferences")
def get_preferences(user: User = Depends(require_user), db: Session = Depends(get_db)):
    p = db.get(UserPreference, user.id)
    return {"languages": p.languages, "categories": p.categories, "countries": p.countries,
            "stories_per_section": p.stories_per_section}


@router.put("/preferences")
def put_preferences(body: PreferencesIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    p = db.get(UserPreference, user.id)
    p.languages, p.categories = list(dict.fromkeys(body.languages)), body.categories
    p.countries, p.stories_per_section = body.countries, body.stories_per_section
    p.updated_at = utcnow()
    db.commit()
    return get_preferences(user, db)


@router.get("/notifications")
def get_notifications(user: User = Depends(require_user), db: Session = Depends(get_db)):
    n = db.get(NotificationPreference, user.id)
    return {"email_enabled": n.email_enabled, "morning_enabled": n.morning_enabled, "morning_time": n.morning_time,
            "evening_enabled": n.evening_enabled, "evening_time": n.evening_time}


@router.put("/notifications")
def put_notifications(body: NotificationsIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    n = db.get(NotificationPreference, user.id)
    for k, v in body.model_dump().items():
        setattr(n, k, v)
    n.updated_at = utcnow()
    db.commit()
    return body.model_dump()


# ---------- saved stories, history, follows ----------

@router.get("/saved")
def list_saved(page: int = 1, page_size: int = 20, user: User = Depends(require_user), db: Session = Depends(get_db)):
    page, page_size = max(1, min(page, 200)), max(1, min(page_size, 50))
    q = (select(Article).join(SavedArticle, SavedArticle.article_id == Article.id)
         .where(SavedArticle.user_id == user.id).order_by(SavedArticle.created_at.desc()))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.offset((page - 1) * page_size).limit(page_size)).all()
    return {"page": page, "page_size": page_size, "total": total, "items": [serialize(db, a) for a in rows]}


@router.put("/saved/{article_id}", status_code=204)
def save_article(article_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if db.get(Article, article_id) is None:
        raise HTTPException(404, "article not found")
    if db.get(SavedArticle, (user.id, article_id)) is None:
        db.add(SavedArticle(user_id=user.id, article_id=article_id, created_at=utcnow()))
        db.commit()
    return Response(status_code=204)


@router.delete("/saved/{article_id}", status_code=204)
def unsave_article(article_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    db.execute(delete(SavedArticle).where(SavedArticle.user_id == user.id, SavedArticle.article_id == article_id))
    db.commit()
    return Response(status_code=204)


@router.post("/history/{article_id}", status_code=204)
def record_read(article_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if db.get(Article, article_id) is None:
        raise HTTPException(404, "article not found")
    db.add(ReadingHistory(user_id=user.id, article_id=article_id, read_at=utcnow()))
    db.commit()
    return Response(status_code=204)


@router.get("/history")
def list_history(page: int = 1, page_size: int = 20, user: User = Depends(require_user), db: Session = Depends(get_db)):
    page, page_size = max(1, min(page, 200)), max(1, min(page_size, 50))
    q = (select(Article, ReadingHistory.read_at).join(ReadingHistory, ReadingHistory.article_id == Article.id)
         .where(ReadingHistory.user_id == user.id).order_by(ReadingHistory.read_at.desc()))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.execute(q.offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for article, read_at in rows:
        item = serialize(db, article)
        item["read_at"] = read_at.isoformat() + "Z"
        items.append(item)
    return {"page": page, "page_size": page_size, "total": total, "items": items}


@router.get("/follows")
def list_follows(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(UserFollow).where(UserFollow.user_id == user.id).order_by(UserFollow.created_at)).all()
    return [{"id": f.id, "kind": f.kind, "value": f.value} for f in rows]


@router.post("/follows", status_code=201)
def add_follow(body: FollowIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if body.kind == "topic" and body.value not in CATEGORY_SLUGS:
        raise HTTPException(422, "unknown topic")
    exists = db.scalar(select(UserFollow).where(UserFollow.user_id == user.id, UserFollow.kind == body.kind,
                                                 UserFollow.value == body.value))
    if exists:
        return {"id": exists.id, "kind": exists.kind, "value": exists.value}
    f = UserFollow(user_id=user.id, kind=body.kind, value=body.value, created_at=utcnow())
    db.add(f)
    db.commit()
    return {"id": f.id, "kind": f.kind, "value": f.value}


@router.delete("/follows/{follow_id}", status_code=204)
def remove_follow(follow_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    db.execute(delete(UserFollow).where(UserFollow.id == follow_id, UserFollow.user_id == user.id))
    db.commit()
    return Response(status_code=204)


@router.get("/feed")
def personal_feed(page: int = 1, page_size: int = 20, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Stories matching the user's preferred languages/categories/countries/followed publishers, newest first."""
    from datetime import timedelta as td
    from sqlalchemy import or_
    prefs = db.get(UserPreference, user.id)
    follows = db.scalars(select(UserFollow).where(UserFollow.user_id == user.id)).all()
    topics = {f.value for f in follows if f.kind == "topic"} | set(prefs.categories or [])
    pubs = {f.value for f in follows if f.kind == "publisher"}
    q = select(Article).where(Article.published_at >= utcnow() - td(hours=72))
    if prefs.languages:
        q = q.where(Article.language.in_(prefs.languages))
    if prefs.countries:
        q = q.where(Article.country.in_(prefs.countries))
    conds = []
    if topics:
        conds.append(Article.category.in_(list(topics)))
    if pubs:
        conds.append(Article.publisher_name.in_(list(pubs)))
    if conds:
        q = q.where(or_(*conds))
    page, page_size = max(1, min(page, 200)), max(1, min(page_size, 50))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(Article.published_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"page": page, "page_size": page_size, "total": total, "items": [serialize(db, a) for a in rows]}
