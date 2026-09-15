"""Runs the whole Alembic chain (base through head) against a scratch
SQLite database and checks the schema it produces still agrees,
structurally, with `Base.metadata` (issue #78).

Nothing else in the suite ever runs a migration: `tests/conftest.py`
builds schema with `Base.metadata.create_all`, which is right for the
~500 tests that just need tables but means the chain and the models can
drift apart indefinitely with every gate green. That matters more here
than in most projects — `deploy.yml` runs the migration *before* `bot`
starts (the bot's startup queries `games` to re-arm pending timeouts),
so a chain that produces the wrong schema takes the bot down rather
than degrading it.

Everything here is deliberately derived from `Base.metadata` and from
`ScriptDirectory` at runtime: no table list, no column list, and no
revision id is hardcoded, so a new migration lands green without
editing this file, and a migration that forgets a column does not.

WHAT THIS CATCHES
    A column the models declare that no migration ever creates; a
    column a migration creates (or fails to drop) that the models no
    longer declare; a rename applied on one side only; a table missing
    on either side; a primary key or foreign key the chain and the
    models disagree about; and a chain that has branched into more than
    one head or cannot be walked from base to head at all.

WHAT THIS DOES NOT CATCH — the SQLite-vs-MariaDB limit
    Production runs MariaDB; this runs SQLite, and the two do not agree
    on everything. This is scoped to *structural* agreement only, and a
    green run here is not dialect fidelity. Specifically out of scope:

    * Column types, lengths and affinities. SQLite has loose typing and
      does not enforce VARCHAR lengths, and this file additionally
      skips the type half of every `op.alter_column` (see
      `_sqlite_ignoring_column_type_changes` for why it has to), so a
      column's declared type is not compared at all.
    * Native ENUM allowed-value lists. MariaDB stores `sa.Enum` as a
      real ENUM whose member list is part of the schema; SQLite renders
      it as a plain VARCHAR with no CHECK constraint. An ENUM-widening
      migration (`c2a7f0d94b31`, `d7a3e5c9b8f1`) is therefore invisible
      here — omit one entirely and this test still passes while
      production rejects the new member.
    * Server defaults, indexes, unique constraints, collation, charset
      and storage engine.
    * Whether a migration's DDL would even execute against a *populated*
      table. The scratch database is empty, so an `op.add_column` of a
      NOT NULL column with no `server_default` — which aborts on a live
      table with rows, and is exactly the hazard `a5ef10f74e63`'s own
      comment documents — applies cleanly here and this test stays
      green. That is a production-relevant blind spot on the same order
      as the ENUM one, because `deploy.yml` migrates before `bot`
      starts: the failure lands in the deploy, not in CI. Note this is
      a different gap from the data-migration bullet below — that one is
      about whether a backfill moved the right rows, this one is about
      whether the statement runs at all.
    * Foreign-key referential actions. The FK test compares
      `(constrained_columns, referred_table, referred_columns)` and
      nothing else, so `ondelete`/`onupdate` and constraint names are
      not checked. Latent today (no model declares either), but the test
      name is broader than what it verifies.
    * DDL that MariaDB would reject but SQLite accepts (a length over a
      row/index limit, say) — the chain never touches MariaDB here.
    * Whether a data migration moved the right data. The scratch
      database is empty, so every backfill in the chain is a no-op by
      construction — which is also what keeps this test off the network
      (`c4d9e6b3f2a1`'s backfill only calls Telegram for rows it finds,
      and it finds none) regardless of whether BOT_TOKEN is set in the
      environment. `_telegram_credentials_unset` clears BOT_TOKEN and
      TELEGRAM_PROXY_URL for the duration of the run anyway, so the
      no-network property does not rest on the empty table alone.
    * `migrations/env.py`, which is bypassed: the chain is driven
      against a connection made here rather than one env.py builds from
      DATABASE_URL, so the test needs no environment at all.
    * `downgrade()`. Only the upgrade direction is exercised.

Two MariaDB-isms in the existing chain need a shim before SQLite will
run it at all; both are narrow, both are restored afterwards, and both
are explained at their definitions below.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.ddl.impl import DefaultImpl
from alembic.ddl.sqlite import SQLiteImpl
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from nani_pix_bot.models import Base

_REPO_ROOT = Path(__file__).parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"

# Alembic's own bookkeeping table; it has no model and never should.
_ALEMBIC_VERSION_TABLE = "alembic_version"

# Every table the models declare, resolved at import time so the
# per-table tests below parametrize over whatever the models currently
# hold rather than over a list written down here.
_MODEL_TABLE_NAMES = sorted(Base.metadata.tables)


@contextmanager
def _sqlite_ignoring_column_type_changes() -> Iterator[None]:
    """Let `op.alter_column(..., type_=...)` run on SQLite by dropping
    the type change and keeping everything else.

    SQLite has no `ALTER TABLE ... ALTER COLUMN`, so Alembic's SQLite
    impl inherits `DefaultImpl.alter_column` and emits DDL the database
    rejects outright ("near \\"ALTER\\": syntax error"). The chain's two
    ENUM-widening migrations do exactly that, so without this shim the
    run dies at `c2a7f0d94b31` and no comparison happens at all.

    Skipping the type change specifically (rather than the whole
    operation) is safe *for what this file measures*: a `sa.Enum`
    column is a plain VARCHAR under SQLite with no CHECK constraint, so
    restating its allowed-value list is genuinely a no-op there, and
    types are outside this test's scope anyway. A rename, a nullability
    change or a server-default change in the same call still executes
    — SQLite supports `RENAME COLUMN`, and anything it does not support
    raises here rather than being silently swallowed.

    The cost is stated in the module docstring: an ENUM widening is
    invisible to this test. It is a real gap, not a hidden one.

    A WARNING FOR WHOEVER HITS THIS NEXT. The "raises rather than
    swallows" property above is forward-looking, and the first future
    migration that does an ordinary `op.alter_column(..., nullable=
    False)` — routine MariaDB practice — will not fail in one test, it
    will kill this whole module at fixture setup with the same SQLite
    syntax error. The tempting fix is to widen this shim to drop
    `nullable` too. DO NOT: that silently guts
    `test_chain_matches_the_models_nullability`, which would then
    compare the model's nullability against a schema this shim had
    stopped applying it to, and pass. The fix belongs in the migration
    instead — wrap the change in `op.batch_alter_table`, which Alembic
    implements on SQLite by rebuilding the table and which MariaDB
    executes as a plain ALTER. Widening the shim trades a loud failure
    for a green test that checks nothing.
    """
    original = SQLiteImpl.__dict__.get("alter_column")

    def _alter_column_without_type(
        self: DefaultImpl, table_name: str, column_name: str, **kw: Any
    ) -> None:
        kw["type_"] = None
        DefaultImpl.alter_column(self, table_name, column_name, **kw)

    SQLiteImpl.alter_column = _alter_column_without_type
    try:
        yield
    finally:
        if original is None:
            del SQLiteImpl.alter_column
        else:
            SQLiteImpl.alter_column = original


def _register_mariadb_builtins(dbapi_connection: Any, _record: Any) -> None:
    """Teach the scratch SQLite connection the MariaDB built-ins the
    chain's raw `op.execute(...)` data migrations call.

    Only `UTC_TIMESTAMP()` is needed today (`b3c8f5a2d1e6` back-dates
    `inactivity_advance_at` with it). If a later migration reaches for
    another MariaDB built-in, SQLite raises "no such function: X" and
    this test goes red with the name in the message — a clear prompt to
    add it here, not a silent skip.
    """
    dbapi_connection.create_function(
        "UTC_TIMESTAMP",
        0,
        lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )


@contextmanager
def _telegram_credentials_unset() -> Iterator[None]:
    """Run the chain with BOT_TOKEN and TELEGRAM_PROXY_URL out of the
    environment, then put back whatever was there.

    `c4d9e6b3f2a1`'s backfill reads both straight from `os.environ` and
    will call Telegram over the network if BOT_TOKEN is set and it finds
    rows to backfill. The scratch database is empty so it finds none —
    but that makes the no-network property depend on a second fact
    holding somewhere else. Unsetting them makes it depend on nothing:
    the worst the backfill can do is print its "BOT_TOKEN not set"
    warning and return. Restoring afterwards keeps this module from
    changing what any other test in the same session sees.

    Scope caveat: the fixture holds this open for the module's whole
    lifetime, not just the few milliseconds the chain runs, so a test in
    another module interleaved between these would observe both
    variables missing. Same class of caveat as the `SQLiteImpl` class
    patch — harmless for how pytest runs this suite today (single
    process, module by module; `pytest-xdist` would isolate it further
    by using separate processes), worth knowing before anything here is
    made concurrent.
    """
    saved = {name: os.environ.pop(name, None) for name in ("BOT_TOKEN", "TELEGRAM_PROXY_URL")}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


def _run_chain_to_head(connection: Connection) -> None:
    """Upgrade `connection`'s database from base to head.

    Drives the migrations against a connection made here instead of
    going through `alembic.command.upgrade`, which would run
    `migrations/env.py` — that reads DATABASE_URL and builds its own
    engine, neither of which a test should depend on. The `fn=` hook
    below is the shape Alembic's own `EnvironmentContext` docs use for
    running the chain programmatically.
    """
    config = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    with EnvironmentContext(
        config,
        script,
        fn=lambda rev, _context: script._upgrade_revs("head", rev),
        destination_rev="head",
    ) as environment:
        environment.configure(connection=connection, target_metadata=Base.metadata)
        with environment.begin_transaction():
            environment.run_migrations()


@pytest.fixture(scope="module")
def migrated_connection() -> Iterator[Connection]:
    """A scratch in-memory SQLite database with the full chain applied.

    Module-scoped: the chain is the expensive part and every assertion
    below only reads from the result.
    """
    engine: Engine = sa.create_engine("sqlite://")
    event.listen(engine, "connect", _register_mariadb_builtins)
    try:
        with _telegram_credentials_unset(), engine.connect() as connection:
            with _sqlite_ignoring_column_type_changes():
                _run_chain_to_head(connection)
            yield connection
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def migrated_inspector(migrated_connection: Connection) -> sa.Inspector:
    return sa.inspect(migrated_connection)


def test_chain_has_exactly_one_head() -> None:
    """A branched chain is a deploy outage waiting to happen: `alembic
    upgrade head` refuses to run at all with more than one head, and
    `deploy.yml` runs it before `bot` starts."""
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI)))
    assert len(script.get_heads()) == 1, (
        f"the migration chain has branched into multiple heads: {script.get_heads()}"
    )


def test_chain_creates_exactly_the_tables_the_models_declare(
    migrated_inspector: sa.Inspector,
) -> None:
    migrated_tables = set(migrated_inspector.get_table_names()) - {_ALEMBIC_VERSION_TABLE}
    assert migrated_tables == set(_MODEL_TABLE_NAMES)


@pytest.mark.parametrize("table_name", _MODEL_TABLE_NAMES)
def test_chain_creates_exactly_the_columns_the_models_declare(
    migrated_inspector: sa.Inspector, table_name: str
) -> None:
    """The core of this file — a column dropped from a model, added to a
    model without a migration, or renamed on one side only lands here."""
    migrated_columns = {column["name"] for column in migrated_inspector.get_columns(table_name)}
    model_columns = {column.name for column in Base.metadata.tables[table_name].columns}
    assert migrated_columns == model_columns, (
        f"{table_name}: only in the models {sorted(model_columns - migrated_columns)}, "
        f"only in the migrated schema {sorted(migrated_columns - model_columns)}"
    )


@pytest.mark.parametrize("table_name", _MODEL_TABLE_NAMES)
def test_chain_matches_the_models_nullability(
    migrated_inspector: sa.Inspector, table_name: str
) -> None:
    migrated_nullability = {
        column["name"]: bool(column["nullable"])
        for column in migrated_inspector.get_columns(table_name)
    }
    model_nullability = {
        column.name: bool(column.nullable) for column in Base.metadata.tables[table_name].columns
    }
    assert migrated_nullability == model_nullability


@pytest.mark.parametrize("table_name", _MODEL_TABLE_NAMES)
def test_chain_matches_the_models_primary_key(
    migrated_inspector: sa.Inspector, table_name: str
) -> None:
    migrated_primary_key = migrated_inspector.get_pk_constraint(table_name)["constrained_columns"]
    model_primary_key = [column.name for column in Base.metadata.tables[table_name].primary_key]
    assert sorted(migrated_primary_key) == sorted(model_primary_key)


@pytest.mark.parametrize("table_name", _MODEL_TABLE_NAMES)
def test_chain_matches_the_models_foreign_keys(
    migrated_inspector: sa.Inspector, table_name: str
) -> None:
    migrated_foreign_keys = sorted(
        (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
        )
        for foreign_key in migrated_inspector.get_foreign_keys(table_name)
    )
    model_foreign_keys = sorted(
        (
            tuple(element.parent.name for element in constraint.elements),
            constraint.referred_table.name,
            tuple(element.column.name for element in constraint.elements),
        )
        for constraint in Base.metadata.tables[table_name].foreign_key_constraints
    )
    assert migrated_foreign_keys == model_foreign_keys
