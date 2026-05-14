"""Time helpers shared across app modules."""
from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return a naive UTC timestamp for SQLAlchemy DateTime columns."""
    return datetime.now(UTC).replace(tzinfo=None)
