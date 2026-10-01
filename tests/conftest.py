from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from nani_pix_bot.db import make_session_factory
from nani_pix_bot.models.base import Base
from nani_pix_bot.services.quiet_hours import QuietHours


@pytest.fixture
def session() -> Iterator[Session]:
    """An in-memory SQLite session with every model table created fresh."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    """A session factory (rather than one open session) for code under test
    that opens its own sessions via session_scope(), e.g. command handlers."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def quiet_now() -> QuietHours:
    """A UTC quiet window from 1h ago to 2h from now (minute precision) —
    i.e. "it is quiet right now", whatever time the test runs at."""
    now = datetime.now(UTC)
    start = (now - timedelta(hours=1)).time().replace(second=0, microsecond=0)
    end = (now + timedelta(hours=2)).time().replace(second=0, microsecond=0)
    return QuietHours(start=start, end=end, tz=ZoneInfo("UTC"))
