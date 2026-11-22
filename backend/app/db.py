"""Veritabanı bağlantısı. SQLite ile başlar; HASTANE_DATABASE_URL ile PostgreSQL'e geçilebilir."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import DATA_DIR, settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = settings.database_url
        kwargs = {}
        if url.startswith("sqlite"):
            if ":memory:" not in url:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
            if ":memory:" in url:
                from sqlalchemy.pool import StaticPool
                kwargs["poolclass"] = StaticPool
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _pragmas(conn, _):  # eşzamanlı okuma/yazmada kilitlenmeyi azaltır
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
    return _engine


def reset_engine(url: str | None = None) -> None:
    """Testler için: farklı bir veritabanına geç."""
    global _engine
    if url:
        settings.database_url = url
    _engine = None


def init_db() -> None:
    from . import models  # noqa: F401  tabloları kaydet
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as s:
        yield s
