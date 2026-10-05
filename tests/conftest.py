from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

import pytest
from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from nani_pix_bot import log_context
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


@pytest.fixture
def records() -> Iterator[list[tuple[str, str]]]:
    """Every log record emitted while the test runs, as (level, message).

    Shared suite-wide: a skip or a game action only helps an operator if
    it leaves a trace at the right level, so tests check both the level
    and the wording (see CLAUDE.md's "Logging")."""
    captured: list[tuple[str, str]] = []
    sink_id = logger.add(
        lambda message: captured.append((message.record["level"].name, message.record["message"])),
        level="DEBUG",
    )
    yield captured
    logger.remove(sink_id)


@pytest.fixture(autouse=True)
def _fresh_log_context() -> Iterator[None]:
    """The structured log context (issue #230) is a ContextVar, which
    would otherwise carry one test's game/user into the next."""
    log_context.reset()
    yield
    log_context.reset()


class LogLine(NamedTuple):
    level: str
    message: str
    extra: dict[str, object]


@pytest.fixture
def log_records() -> Iterator[list[LogLine]]:
    """Every record emitted while the test runs, with its structured
    fields: the log context (game, user, job) merged in the way
    logging_config wires it in production, plus the call's kwargs."""
    captured: list[LogLine] = []
    logger.configure(patcher=log_context.patcher)
    sink_id = logger.add(
        lambda m: captured.append(
            LogLine(m.record["level"].name, m.record["message"], dict(m.record["extra"]))
        ),
        level="DEBUG",
    )
    yield captured
    logger.remove(sink_id)
    logger.configure(patcher=None)
