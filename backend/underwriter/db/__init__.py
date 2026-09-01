"""Persistence — the §18 schema and the session that reaches it.

TD-02: SQLite with WAL on a persistent volume. Single-writer workload, zero
operational overhead, and one less service to be down at 3am. SQLAlchemy keeps
the Postgres path open, so nothing here uses SQLite-only syntax.

F-11 is why this layer exists before the Execution Engine does: recording is a
precondition for trading, and the system never trades unrecorded.
"""

from underwriter.db.base import Base
from underwriter.db.engine import (
    create_all,
    get_session,
    reset_engine,
    session_scope,
)

__all__ = ["Base", "create_all", "get_session", "reset_engine", "session_scope"]
