"""Publisher registry loader. Adding a publisher means editing registry/publishers.json only."""
import json
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import NewsCategory, Publisher
from .normalize import CATEGORIES


def seed_categories(session: Session) -> None:
    """Insert the fixed category list (idempotent). Required before any article is stored."""
    for i, (slug, label) in enumerate(CATEGORIES):
        if session.get(NewsCategory, slug) is None:
            session.add(NewsCategory(slug=slug, label=label, sort_order=i))
    session.commit()

REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "registry", "publishers.json")
REQUIRED = {"slug", "name", "country", "language", "website", "domain"}


def load_registry(path: str = REGISTRY_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    valid = []
    for e in entries:
        if REQUIRED <= e.keys():
            valid.append(e)
    return valid


def sync_publishers(session: Session, entries: list[dict]) -> int:
    """Upsert registry entries. Admin-set enabled flags are preserved."""
    count = 0
    for e in entries:
        row = session.scalar(select(Publisher).where(Publisher.slug == e["slug"]))
        if row is None:
            row = Publisher(slug=e["slug"], enabled=True)
            session.add(row)
        for key in ("name", "country", "region", "language", "website", "domain", "notes"):
            setattr(row, key, e.get(key, "") or "")
        row.rss_url = e.get("rss_url")
        row.epaper_url = e.get("epaper_url")
        row.epaper_status = e.get("epaper_status", "not_verified")
        row.reliability = float(e.get("reliability", 0.6))
        row.rss_status = "configured" if row.rss_url else "not_configured"
        count += 1
    session.commit()
    return count
