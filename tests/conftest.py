import shutil
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

import pytest
from loguru import logger
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from nani_pix_bot import log_context
from nani_pix_bot.db import make_session_factory
from nani_pix_bot.models.base import Base
from nani_pix_bot.services import sprites
from nani_pix_bot.services.quiet_hours import QuietHours

pytest_plugins = ["tests.seasons_fixtures", "tests.costume_fixtures"]


@pytest.fixture(autouse=True)
def _missing_art_logged_afresh() -> None:
    """`sprites.log_missing` reports each sprite once per process; every test starts over."""
    sprites._logged_missing.clear()


def memory_engine() -> Engine:
    """A fresh in-memory SQLite database, one connection shared by every thread,
    so code that reads it from a worker thread (`asyncio.to_thread`) sees the
    same tables and rows as the test. The default pool gives each thread its
    own, empty, in-memory database."""
    return create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def session() -> Iterator[Session]:
    """An in-memory SQLite session with every model table created fresh."""
    engine = memory_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    """A session factory (rather than one open session) for code under test
    that opens its own sessions via session_scope(), e.g. command handlers."""
    engine = memory_engine()
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


@pytest.fixture(autouse=True)
def _achievements_off_by_default(request: pytest.FixtureRequest, monkeypatch) -> None:
    """Every emitted event would otherwise evaluate achievements and pay
    rewards, changing the exact balances hundreds of economy tests assert.
    Tests (or modules) marked `achievements` keep evaluation on."""
    if request.node.get_closest_marker("achievements") is None:
        from nani_pix_bot.services import events

        monkeypatch.setattr(events, "_dispatch", lambda _session, _event: None)


def pytest_collection_modifyitems(config, items) -> None:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    skip = pytest.mark.skip(reason="ffmpeg/ffprobe not on PATH")
    for item in items:
        if "ffmpeg" in item.keywords:
            item.add_marker(skip)


def _equipper(
    monkeypatch: pytest.MonkeyPatch, item_id: str
) -> Iterator[Callable[[Session, int], None]]:
    """Fake season registry patched in; yields `equip(session, player_id)`,
    which grants and equips the fake item `item_id`."""
    from nani_pix_bot.models.enums import ItemSource
    from nani_pix_bot.seasons import registry
    from nani_pix_bot.services.cosmetics import inventory

    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()

    def equip(session: Session, player_id: int) -> None:
        inventory.grant(session, player_id, inventory.ItemGrant(item_id, ItemSource.ADMIN))
        inventory.equip(session, player_id, item_id)

    yield equip
    registry.all_items.cache_clear()


@pytest.fixture
def equip_flair(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Session, int], None]]:
    """`equip_flair(session, player_id)` gives the player the fake run's
    blossom name flair and equips it."""
    yield from _equipper(monkeypatch, "demo_1/name_flair.blossom")


@pytest.fixture
def equip_win_effect(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Session, int], None]]:
    """`equip_win_effect(session, player_id)` gives the player the fake run's
    "confetti" win effect and equips it."""
    yield from _equipper(monkeypatch, "demo_1/win_effect.confetti")


@pytest.fixture
def equip_frame(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Session, int], None]]:
    """`equip_frame(session, player_id)` gives the player the fake run's
    "simple_border" host frame and equips it."""
    yield from _equipper(monkeypatch, "demo_1/host_frame.border")


@pytest.fixture
def equip_title(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Session, int], None]]:
    """`equip_title(session, player_id)` gives the player the fake run's
    "Demo Star" title item (the fake registry stays patched in)."""
    yield from _equipper(monkeypatch, "demo_1/title.demo")


@pytest.fixture
def equip_transition(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Session, int], None]]:
    """`equip_transition(session, player_id)` gives the player the fake run's
    "iris" host transition and equips it."""
    yield from _equipper(monkeypatch, "demo_1/host_transition.iris")
