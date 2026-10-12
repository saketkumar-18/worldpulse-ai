"""Database engine and session management (PostgreSQL in production, SQLite for local dev/tests)."""
from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamp. All stored timestamps are UTC; convert for display only."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def normalize_database_url(url: str) -> str:
    """Accept postgres:// and postgresql:// (what hosts like Neon emit) and use the psycopg3 driver."""
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def configure(database_url: str, create_schema: bool = True) -> Engine:
    global _engine, _session_factory
    database_url = normalize_database_url(database_url)
    kwargs: dict = {"pool_pre_ping": True}
    is_sqlite = database_url.startswith("sqlite")
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)
    _engine = create_engine(database_url, **kwargs)
    if is_sqlite:
        @event.listens_for(_engine, "connect")
        def _sqlite_fk(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    if create_schema:
        # Development convenience. Production uses Alembic migrations (see migrations/).
        from . import models  # noqa: F401  (register models)
        Base.metadata.create_all(_engine)
    return _engine


def session_factory() -> sessionmaker:
    if _session_factory is None:
        raise RuntimeError("Database not configured; call db.configure() first")
    return _session_factory


def get_db() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()
