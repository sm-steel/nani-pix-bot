"""Tests for a98069983e15's data rewrite (Jikan -> Tenrai).

Same technique as test_c4d9e6b3f2a1_backfill.py: the migration module is
loaded directly by path and driven against a real Alembic Operations
context bound to an in-memory SQLite connection, so its own
`op.alter_column`/`op.execute` calls resolve exactly as they would under
a genuine `alembic upgrade` — without going through `migrations/env.py`
or the full chain.

`tests/migrations/test_chain_matches_models.py` already covers the
column rename structurally (it diffs the migrated schema against
`Base.metadata`), and its own docstring names "whether a data migration
moved the right data" as explicitly out of its scope, since its scratch
database is always empty. This file is what closes that gap for this
migration: it seeds a populated row and asserts the *values*, not just
the column names, come out right on both `upgrade()` and `downgrade()`.
"""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

_MIGRATION_PATH = (
    Path(__file__).parents[2] / "migrations" / "versions" / "a98069983e15_rename_jikan_to_tenrai.py"
)


def _load_migration_module() -> ModuleType:
    """Import the migration file fresh under a throwaway module name —
    same reasoning as test_c4d9e6b3f2a1_backfill.py's helper of the same
    shape: it's a standalone script Alembic loads by path, not a package
    member, and a fresh module per test keeps one test's state from
    leaking into another."""
    spec = importlib.util.spec_from_file_location("a98069983e15_under_test", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_pre_migration_games_table(metadata: sa.MetaData) -> sa.Table:
    """The pre-migration 'games' shape this migration's upgrade() touches
    — just the columns it reads or writes, not the full model."""
    return sa.Table(
        "games",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("jikan_id", sa.Integer, nullable=True),
        sa.Column("source", sa.String(16), nullable=True),
        sa.Column("screenshot_source", sa.String(16), nullable=True),
        sa.Column("screenshot_picker_provider", sa.String(16), nullable=True),
    )


def _make_post_migration_games_table(metadata: sa.MetaData) -> sa.Table:
    """The post-migration shape downgrade()'s own pre-state — used only
    to seed a row before exercising downgrade() in isolation."""
    return sa.Table(
        "games",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenrai_id", sa.Integer, nullable=True),
        sa.Column("source", sa.String(16), nullable=True),
        sa.Column("screenshot_source", sa.String(16), nullable=True),
        sa.Column("screenshot_picker_provider", sa.String(16), nullable=True),
    )


@pytest.fixture
def pre_migration_connection() -> Iterator[tuple[sa.Connection, sa.Table]]:
    """A live Alembic Operations context, pre-migration schema, bound to
    a real sqlite connection — same mechanism test_c4d9e6b3f2a1_backfill.py's
    games_connection fixture uses, so op.alter_column/op.execute inside
    the migration resolve exactly as they do under `alembic upgrade`."""
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    games = _make_pre_migration_games_table(metadata)
    metadata.create_all(engine)
    with engine.connect() as conn:
        migration_context = MigrationContext.configure(conn)
        with Operations.context(migration_context):
            yield conn, games


@pytest.fixture
def post_migration_connection() -> Iterator[tuple[sa.Connection, sa.Table]]:
    """The downgrade() counterpart of pre_migration_connection above —
    starts from the post-migration schema/values instead."""
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    games = _make_post_migration_games_table(metadata)
    metadata.create_all(engine)
    with engine.connect() as conn:
        migration_context = MigrationContext.configure(conn)
        with Operations.context(migration_context):
            yield conn, games


def _row(conn: sa.Connection, table_name: str, row_id: int) -> sa.Row:
    table = sa.Table(table_name, sa.MetaData(), autoload_with=conn)
    result = conn.execute(sa.select(table).where(table.c.id == row_id)).fetchone()
    assert result is not None
    return result


def test_upgrade_renames_the_column_and_rewrites_jikan_to_tenrai(
    pre_migration_connection: tuple[sa.Connection, sa.Table],
) -> None:
    conn, games = pre_migration_connection
    conn.execute(
        games.insert(),
        [
            {
                "id": 1,
                "jikan_id": 52991,
                "source": "jikan",
                "screenshot_source": "jikan",
                "screenshot_picker_provider": "jikan",
            }
        ],
    )
    conn.commit()

    module = _load_migration_module()
    module.upgrade()
    conn.commit()

    row = _row(conn, "games", 1)
    assert row.tenrai_id == 52991
    assert row.source == "tenrai"
    assert row.screenshot_source == "tenrai"
    assert row.screenshot_picker_provider == "tenrai"


def test_upgrade_leaves_a_non_jikan_row_untouched(
    pre_migration_connection: tuple[sa.Connection, sa.Table],
) -> None:
    """A row identified through a different provider must not have its
    source columns rewritten just because the migration ran."""
    conn, games = pre_migration_connection
    conn.execute(
        games.insert(),
        [
            {
                "id": 2,
                "jikan_id": None,
                "source": "shikimori",
                "screenshot_source": "tmdb",
                "screenshot_picker_provider": None,
            }
        ],
    )
    conn.commit()

    module = _load_migration_module()
    module.upgrade()
    conn.commit()

    row = _row(conn, "games", 2)
    assert row.source == "shikimori"
    assert row.screenshot_source == "tmdb"
    assert row.screenshot_picker_provider is None


def test_downgrade_renames_the_column_and_rewrites_tenrai_to_jikan(
    post_migration_connection: tuple[sa.Connection, sa.Table],
) -> None:
    conn, games = post_migration_connection
    conn.execute(
        games.insert(),
        [
            {
                "id": 1,
                "tenrai_id": 52991,
                "source": "tenrai",
                "screenshot_source": "tenrai",
                "screenshot_picker_provider": "tenrai",
            }
        ],
    )
    conn.commit()

    module = _load_migration_module()
    module.downgrade()
    conn.commit()

    row = _row(conn, "games", 1)
    assert row.jikan_id == 52991
    assert row.source == "jikan"
    assert row.screenshot_source == "jikan"
    assert row.screenshot_picker_provider == "jikan"


def test_downgrade_leaves_a_non_tenrai_row_untouched(
    post_migration_connection: tuple[sa.Connection, sa.Table],
) -> None:
    conn, games = post_migration_connection
    conn.execute(
        games.insert(),
        [
            {
                "id": 2,
                "tenrai_id": None,
                "source": "anilist",
                "screenshot_source": None,
                "screenshot_picker_provider": None,
            }
        ],
    )
    conn.commit()

    module = _load_migration_module()
    module.downgrade()
    conn.commit()

    row = _row(conn, "games", 2)
    assert row.source == "anilist"
    assert row.screenshot_source is None
    assert row.screenshot_picker_provider is None
