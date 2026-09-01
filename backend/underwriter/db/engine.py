"""Engine and session lifecycle.

WAL is not a performance tweak here. It lets the 15-second dashboard poll read
while a cycle is mid-write, which without it would either block the reader or
fail it — and NFR-005 wants zero loss of committed records across a restart.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from underwriter.db.base import Base

DEFAULT_URL = "sqlite:///data/underwriter.db"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL).strip() or DEFAULT_URL


def _configure_sqlite(engine: Engine) -> None:
    """WAL, foreign keys, and a busy timeout.

    `foreign_keys=ON` matters more than it looks: SQLite ignores foreign key
    constraints unless it is asked to enforce them, so without this the
    NOT NULL on `orders.kernel_decision_id` would still hold but the FK behind
    it would be decorative.
    """

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # A cycle writing while the dashboard reads should wait, not fail.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def get_engine() -> Engine:
    """The process-wide engine. One instance, per OPS-020."""
    global _engine, _session_factory

    if _engine is not None:
        return _engine

    url = database_url()
    if url.startswith("sqlite:///") and ":memory:" not in url:
        path = Path(url.removeprefix("sqlite:///"))
        path.parent.mkdir(parents=True, exist_ok=True)

    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # FastAPI runs sync endpoints in a threadpool, so a pooled connection
        # can be checked out by a different thread than the one that opened it.
        # sqlite3 refuses that by default. SQLAlchemy's pool still guarantees
        # one thread uses a connection at a time, so lifting the check is safe
        # and is what makes the API usable at all under a real server.
        connect_args["check_same_thread"] = False

    _engine = create_engine(url, echo=False, future=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        _configure_sqlite(_engine)

    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session() -> Session:
    """A new session. Callers own the transaction."""
    get_engine()
    assert _session_factory is not None
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on anything else.

    Fail-closed applies to persistence too: a half-written cycle is worse than
    an aborted one, because the next reconcile would treat it as truth.
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all() -> None:
    """Create every table. Alembic owns migrations once the schema settles."""
    Base.metadata.create_all(get_engine())


def reset_engine() -> None:
    """Drop the cached engine. Used by tests to swap databases."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
