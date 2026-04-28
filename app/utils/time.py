from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    # UTC timestamps avoid connector-specific timezone drift in persisted state and tests.
    return datetime.now(tz=UTC)
