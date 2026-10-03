"""SQLAlchemy engine/session plumbing. Services get a session via
`session_scope()` — commits on success, rolls back on any exception."""

from collections.abc import Iterator
from contextlib import contextmanager

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# Well under MariaDB's default wait_timeout (8h), after which the server
# silently drops an idle connection the pool still holds.
POOL_RECYCLE_SECONDS = 3600


def get_engine(database_url: str) -> Engine:
    """pool_pre_ping catches a connection the server already closed (an
    overnight lull is enough) and swaps it for a fresh one instead of
    failing the first query with "MySQL server has gone away" — issue #194."""
    return create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE_SECONDS,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        logger.opt(exception=True).warning("Rolling back session due to an exception")
        session.rollback()
        raise
    finally:
        session.close()
