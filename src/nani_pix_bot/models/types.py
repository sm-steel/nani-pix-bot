"""Custom SQLAlchemy types for the nani-pix-bot models."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """A DateTime type that ensures timezone-aware UTC datetimes.

    Handles conversion to/from naive datetimes in databases like SQLite that
    don't natively support timezone-aware datetimes, while ensuring all
    Python datetime objects are UTC-aware.
    """

    impl = DateTime(timezone=False)
    cache_ok = True

    def process_bind_param(
        self,
        value: Any,
        dialect: Dialect,  # noqa: ARG002
    ) -> Any:
        """Convert a UTC-aware datetime to a naive datetime for storage."""
        if value is not None and isinstance(value, datetime):
            if value.tzinfo is not None:
                # Convert to UTC if not already
                value = value.astimezone(UTC)
            # Store as naive (database-agnostic)
            return value.replace(tzinfo=None)
        return value

    def process_result_value(
        self,
        value: Any,
        dialect: Dialect,  # noqa: ARG002
    ) -> Any:
        """Convert a naive datetime from storage back to UTC-aware."""
        if value is not None:
            if isinstance(value, datetime):
                # Assume stored datetimes are UTC and add timezone info
                return value.replace(tzinfo=UTC)
            if isinstance(value, str):
                dt = datetime.fromisoformat(value)
                return dt.replace(tzinfo=UTC)
        return value
