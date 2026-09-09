from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from nani_pix_bot.db import make_session_factory
from nani_pix_bot.models.base import Base


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
