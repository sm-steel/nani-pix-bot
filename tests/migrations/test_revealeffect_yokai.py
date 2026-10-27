"""f2c7a9d4e1b8 widens reveal_video.effect with the Yōkai Night transitions. Same harness
as test_d7a3f1c9b5e2_clear_item_previews.py."""

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from nani_pix_bot.models.enums import RevealEffect
from tests.revealeffect_migration import yokai_migration as _migration


def test_the_revealeffect_migration_lists_every_member() -> None:
    mod = _migration()
    assert set(mod._BASE + mod._SAKURA + mod._YOKAI) == set(RevealEffect.__members__)


def test_downgrade_points_a_yokai_slot_at_iris(monkeypatch) -> None:
    mod = _migration()
    # SQLite has no ALTER COLUMN: the ENUM restatement is MariaDB-only; the data fix runs here
    monkeypatch.setattr(mod.op, "alter_column", lambda *args, **kwargs: None)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    table = sa.Table(
        "reveal_video",
        metadata,
        sa.Column("game_id", sa.Integer, primary_key=True),
        sa.Column("effect", sa.Enum(*mod._BASE, *mod._SAKURA, *mod._YOKAI, name="revealeffect")),
    )
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(table.insert(), [{"game_id": 1, "effect": "CANDLE_SNUFF"}])
        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        assert conn.execute(sa.select(table.c.effect)).scalar() == "IRIS"
