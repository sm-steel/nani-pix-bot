from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from nani_pix_bot.db import make_session_factory
from nani_pix_bot.models.base import Base


@pytest.fixture
def session() -> Iterator[Session]:
    """An in-memory SQLite session with every model table created fresh."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _sqlite_enable_foreign_keys(dbapi_connection: Any, _connection: Any) -> None:
        """Enable foreign keys for SQLite."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
