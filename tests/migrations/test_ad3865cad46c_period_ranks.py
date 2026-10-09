"""The period_results constraint swap (#319) round-trips on SQLite, and a
downgrade blocked by a shared rank leaves the newer constraint in place.

SQLite rebuilds the table in a batch, so the operation order inside it
doesn't show here; on MariaDB (non-transactional DDL) the migration adds
the new constraint before dropping the old one for the same reason this
test checks the end state: a failure must not leave the table unguarded.
"""

from collections.abc import Iterator
from io import StringIO

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from nani_pix_bot.models import Base
from tests.migrations.test_chain_matches_models import (
    _ALEMBIC_INI,
    _register_mariadb_builtins,
    _sqlite_ignoring_column_type_changes,
    _telegram_credentials_unset,
)

REVISION = "ad3865cad46c"
PREVIOUS = "c8e2a4f6b1d3"


def _migrate(connection: Connection, target: str, *, down: bool = False) -> None:
    config = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)

    def step(rev, _context):
        return script._downgrade_revs(target, rev) if down else script._upgrade_revs(target, rev)

    with EnvironmentContext(config, script, fn=step, destination_rev=target) as environment:
        environment.configure(connection=connection, target_metadata=Base.metadata)
        with environment.begin_transaction():
            environment.run_migrations()


def _uniques(connection: Connection) -> set[str]:
    found = sa.inspect(connection).get_unique_constraints("period_results")
    return {c["name"] for c in found if c["name"]}


@pytest.fixture
def connection() -> Iterator[Connection]:
    engine = sa.create_engine("sqlite://")
    event.listen(engine, "connect", _register_mariadb_builtins)
    try:
        with (
            _telegram_credentials_unset(),
            engine.connect() as conn,
            _sqlite_ignoring_column_type_changes(),
        ):
            _migrate(conn, "head")
            yield conn
    finally:
        engine.dispose()


def _row(connection: Connection, player: int, rank: int) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO period_results (period_type, period_key, rank, player_id, score, wins)"
            " VALUES ('week', '2026-W41', :rank, :player, 6, 1)"
        ),
        {"rank": rank, "player": player},
    )


def test_the_swap_round_trips(connection: Connection) -> None:
    assert _uniques(connection) == {"uq_period_result_player"}
    _migrate(connection, PREVIOUS, down=True)
    assert _uniques(connection) == {"uq_period_result_rank"}
    _migrate(connection, "head")
    assert _uniques(connection) == {"uq_period_result_player"}


def test_a_downgrade_blocked_by_a_shared_rank_keeps_the_player_constraint(
    connection: Connection,
) -> None:
    _row(connection, 1, 1)  # SQLite leaves the players FK unenforced
    _row(connection, 2, 1)

    with pytest.raises(IntegrityError):
        _migrate(connection, PREVIOUS, down=True)

    assert _uniques(connection) == {"uq_period_result_player"}


def _mysql_sql(start: str, target: str, *, down: bool = False) -> str:
    """The DDL the step emits on MySQL/MariaDB, rendered offline."""
    config = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    buffer = StringIO()

    def step(rev, _context):
        return script._downgrade_revs(target, rev) if down else script._upgrade_revs(target, rev)

    with EnvironmentContext(
        config,
        script,
        fn=step,
        as_sql=True,
        starting_rev=start,
        destination_rev=target,
    ) as environment:
        environment.configure(dialect_name="mysql", output_buffer=buffer, as_sql=True)
        with environment.begin_transaction():
            environment.run_migrations()
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("start", "target", "down", "new", "old"),
    [
        (PREVIOUS, REVISION, False, "uq_period_result_player", "uq_period_result_rank"),
        (REVISION, PREVIOUS, True, "uq_period_result_rank", "uq_period_result_player"),
    ],
)
def test_mariadb_adds_the_new_constraint_before_dropping_the_old(
    start: str, target: str, down: bool, new: str, old: str
) -> None:
    # MariaDB DDL isn't transactional: a failed ADD after the DROP would
    # leave period_results with no unique constraint at all.
    sql = _mysql_sql(start, target, down=down)
    added = sql.index(f"ADD CONSTRAINT {new}")
    dropped = sql.index(f"DROP INDEX {old}")
    assert added < dropped
