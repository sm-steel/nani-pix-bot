"""armed ofuda cascades with its game, a hex keeps its game id as NULL

`armed_ofuda.game_id` had no ON DELETE rule, so stopping a round with an armed
ofuda failed on MariaDB; it now CASCADEs like every other game child table.
`hexes.game_id` becomes ON DELETE SET NULL: a hex outlives the round it was
cast in.

Revision ID: e7a1c3f5b9d2
Revises: d2f6b8a4c1e9
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1c3f5b9d2"
down_revision: str | Sequence[str] | None = "d2f6b8a4c1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, the ON DELETE rule the game_id FK gets)
_RULES = (("armed_ofuda", "CASCADE"), ("hexes", "SET NULL"))
# Names the unnamed FKs SQLite reflects, so batch mode can drop them.
_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _name(table: str) -> str:
    return f"fk_{table}_game_id_games"


def _current_fk_name(table: str) -> str:
    """The game_id FK's name: MariaDB's auto-generated one, or (SQLite reflects
    it unnamed) the naming convention's."""
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(table):
        if fk["constrained_columns"] == ["game_id"] and fk["referred_table"] == "games":
            return fk["name"] or _name(table)
    raise RuntimeError(f"{table}.game_id has no foreign key to games")


def _replace(table: str, drop: str, ondelete: str | None) -> None:
    with op.batch_alter_table(table, naming_convention=_CONVENTION) as batch:
        batch.drop_constraint(drop, type_="foreignkey")
        batch.create_foreign_key(
            op.f(_name(table)), "games", ["game_id"], ["id"], ondelete=ondelete
        )


def upgrade() -> None:
    for table, ondelete in _RULES:
        _replace(table, _current_fk_name(table), ondelete)


def downgrade() -> None:
    for table, _ in _RULES:
        _replace(table, _name(table), None)
