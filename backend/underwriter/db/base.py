"""Column types that make the §18 conventions impossible to break by accident.

The schema says all money is TEXT holding a decimal string and all timestamps
are ISO-8601 UTC with `Z`. Those are easy rules to state and easy to violate
one column at a time, so they are types rather than conventions:

* `Money` refuses a float outright. A `REAL` column holding a reserve is the
  bug NFR-013 exists to prevent, and it would be invisible until a rounding
  error turned up in a settled P&L.
* `UtcTimestamp` refuses a naive datetime. NFR-012 stores everything in UTC and
  displays in market time; a naive value is one that has already lost the
  information needed to do that.

Nothing here uses SQLite-only syntax, so the Postgres path TD-02 preserves
stays open.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Dialect, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every table in §18."""


class Money(TypeDecorator[Decimal]):
    """A decimal string in TEXT, a `Decimal` in Python (NFR-013).

    Accepts `Decimal`, `int` and `str`. A float raises: it is never the right
    type for money, and silently converting one would defeat the point.
    """

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError(f"float {value!r} is not storable as money; use Decimal (NFR-013)")
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError(f"non-finite money value: {value}")
            return str(value)
        if isinstance(value, int | str):
            return str(Decimal(str(value)))
        raise TypeError(f"{type(value).__name__} is not storable as money")

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(value)


class UtcTimestamp(TypeDecorator[datetime]):
    """ISO-8601 UTC with a trailing `Z`, stored as TEXT.

    Text rather than a native timestamp so the stored form is identical on
    SQLite and Postgres and so the audit hash chain covers exactly the bytes a
    reader sees.
    """

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"{type(value).__name__} is not a timestamp")
        if value.tzinfo is None:
            raise ValueError(
                f"naive datetime {value!r} refused; timestamps are stored in UTC (NFR-012)"
            )
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class JsonText(TypeDecorator[Any]):
    """A JSON document in TEXT, serialised canonically.

    Sorted keys and no incidental whitespace, so two equal documents are equal
    strings — which is what lets the audit chain hash a row and get the same
    answer twice.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value


def utc_now() -> datetime:
    """The single clock source for row timestamps."""
    return datetime.now(UTC)


def new_id() -> str:
    """UUIDv4 as TEXT, per the §18 conventions."""
    import uuid

    return str(uuid.uuid4())
