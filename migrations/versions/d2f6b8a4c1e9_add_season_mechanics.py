"""add season mechanics tables and columns

games.haunt_twist/boss_game/event_day, players.hex_opt_out, and the hexes,
armed_ofuda and omikuji_draws tables (Halloween season mechanics).

Revision ID: d2f6b8a4c1e9
Revises: c5e9a3f7b1d4
Create Date: 2026-10-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2f6b8a4c1e9"
down_revision: str | Sequence[str] | None = "c5e9a3f7b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("haunt_twist", sa.String(16), nullable=True))
    op.add_column(
        "games",
        sa.Column("boss_game", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "games",
        sa.Column("event_day", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "players",
        sa.Column("hex_opt_out", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "hexes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("caster_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("cast_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("game_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["season_id"], ["season_schedule.id"]),
        sa.ForeignKeyConstraint(["caster_id"], ["players.telegram_user_id"]),
        sa.ForeignKeyConstraint(["target_id"], ["players.telegram_user_id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hexes_target_id_resolved_at", "hexes", ["target_id", "resolved_at"])
    op.create_index("ix_hexes_caster_id_kind_cast_at", "hexes", ["caster_id", "kind", "cast_at"])
    op.create_table(
        "armed_ofuda",
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("armed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.telegram_user_id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("player_id", "game_id"),
    )
    op.create_table(
        "omikuji_draws",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("drawn_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["season_id"], ["season_schedule.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.telegram_user_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season_id", "player_id", "local_date", name="uq_omikuji_draws_day"),
    )


def downgrade() -> None:
    # No explicit drop_index: on MariaDB/InnoDB the indexes back foreign keys,
    # so dropping them before the table fails (error 1553); drop_table removes them.
    op.drop_table("omikuji_draws")
    op.drop_table("armed_ofuda")
    op.drop_table("hexes")
    with op.batch_alter_table("players") as batch:
        batch.drop_column("hex_opt_out")
    with op.batch_alter_table("games") as batch:
        batch.drop_column("event_day")
        batch.drop_column("boss_game")
        batch.drop_column("haunt_twist")
