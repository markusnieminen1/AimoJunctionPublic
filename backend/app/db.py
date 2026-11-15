"""Database engine, session factory, and DeclarativeBase setup."""

from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base for all ORM models."""


def make_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine configured for SQLite (WAL) or MariaDB/MySQL."""
    if database_url.startswith("sqlite"):
        eng = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(eng, "connect")
        def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return eng

    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


_engine_url: str = settings.database_url
engine: Engine = make_engine(_engine_url)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped SQLAlchemy Session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(target_engine: Engine | None = None) -> None:
    """Register ORM models and create all missing tables via metadata.create_all."""
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=target_engine or engine)
