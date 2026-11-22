"""Veritabanı bağlantısı. SQLite ile başlar; HASTANE_DATABASE_URL ile PostgreSQL'e geçilebilir."""
from __future__ import annotations

import logging
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


def _old_schema(engine) -> bool:
    """Hava kalitesi sürümünden kalma veritabanı mı? (oda tablosunda 'ward' sütunu yok)"""
    from sqlalchemy import inspect
    insp = inspect(engine)
    return insp.has_table("room") and "ward" not in {c["name"] for c in insp.get_columns("room")}


def init_db() -> None:
    from . import models  # noqa: F401  tabloları kaydet
    global _engine
    engine = get_engine()
    url = settings.database_url
    if url.startswith("sqlite:///") and ":memory:" not in url and _old_schema(engine):
        # Eski demo veritabanını silmeden kenara al, yenisini oluştur
        from datetime import datetime
        from pathlib import Path
        path = Path(url.removeprefix("sqlite:///"))
        engine.dispose()
        _engine = None
        backup = path.with_name(f"{path.stem}.eski-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(path) + suffix)
            if f.exists():
                f.rename(Path(str(backup) + suffix))
        logging.getLogger("hastane.db").warning("Eski şemalı veritabanı %s olarak saklandı; yenisi oluşturuluyor", backup.name)
        engine = get_engine()
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as s:
        yield s
