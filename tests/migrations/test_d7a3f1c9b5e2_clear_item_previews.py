"""d7a3f1c9b5e2 empties item_previews (#424): previews drawn on the old
sample scene (and win effects/banners kept before #427 drew them on the
viewer) are re-rendered on demand. Same harness as
test_a98069983e15_data_migration.py."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

_MIGRATION_PATH = (
    Path(__file__).parents[2] / "migrations" / "versions" / "d7a3f1c9b5e2_clear_item_previews.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("d7a3f1c9b5e2_under_test", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "item_previews",
        metadata,
        sa.Column("item_id", sa.String(96), primary_key=True),
        sa.Column("lang", sa.String(8), primary_key=True),
        sa.Column("kind", sa.String(8)),
        sa.Column("file_id", sa.String(256)),
    )


def test_upgrade_empties_the_preview_cache_and_downgrade_leaves_it_empty() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    previews = _table(metadata)
    metadata.create_all(engine)
    module = _migration()
    with engine.connect() as conn:
        conn.execute(
            previews.insert(),
            [
                {"item_id": "sakura_1/host_frame.washi", "lang": "EN", "kind": "photo"},
                {"item_id": "sakura_1/win_effect.x@7", "lang": "RU", "kind": "video"},
            ],
        )
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            assert conn.execute(sa.select(sa.func.count()).select_from(previews)).scalar() == 0
            module.downgrade()  # a cache: nothing to put back
            assert conn.execute(sa.select(sa.func.count()).select_from(previews)).scalar() == 0
