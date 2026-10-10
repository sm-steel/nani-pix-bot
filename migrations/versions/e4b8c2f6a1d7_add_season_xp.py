"""add season_xp and games.season_id

Revision ID: e4b8c2f6a1d7
Revises: d1a5e7c3b9f2
Create Date: 2026-10-10 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b8c2f6a1d7"
down_revision: str | Sequence[str] | None = "d1a5e7c3b9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # batch: SQLite (the migration test) can't add a constraint in place.
    op.add_column("games", sa.Column("season_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_games_season_id"), "games", ["season_id"])
    with op.batch_alter_table("games") as batch:
        batch.create_foreign_key(
            op.f("fk_games_season_id_season_schedule"),
            "season_schedule",
            ["season_id"],
            ["id"],
        )
    op.create_table(
        "season_xp",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_season_xp")),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["season_schedule.id"],
            name=op.f("fk_season_xp_season_id_season_schedule"),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.telegram_user_id"],
            name=op.f("fk_season_xp_player_id_players"),
        ),
    )
    op.create_index(op.f("ix_season_xp_season_id"), "season_xp", ["season_id"])
    op.create_index(op.f("ix_season_xp_player_id"), "season_xp", ["player_id"])
    op.create_index(op.f("ix_season_xp_game_id"), "season_xp", ["game_id"])


def downgrade() -> None:
    """Downgrade schema. The FK goes first: MariaDB won't drop an index that
    backs a live FK, and SQLite's table rebuild needs the index gone before
    its column."""
    op.drop_table("season_xp")
    with op.batch_alter_table("games") as batch:
        batch.drop_constraint("fk_games_season_id_season_schedule", type_="foreignkey")
        batch.drop_index("ix_games_season_id")
        batch.drop_column("season_id")
