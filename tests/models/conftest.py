from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from nani_pix_bot.models.base import Base


@pytest.fixture
def session() -> Iterator[Session]:
    """An in-memory SQLite session with every model table created fresh."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
