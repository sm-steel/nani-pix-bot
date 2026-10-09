"""period results share ranks

Tied players now share a place on a period's podium (competition ranks,
1, 1, 3), so the one-player-per-rank constraint gives way to one row per
player per period. Closed periods keep the rows they were frozen with.

Revision ID: ad3865cad46c
Revises: c8e2a4f6b1d3
Create Date: 2026-10-09 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "ad3865cad46c"
down_revision: str | Sequence[str] | None = "c8e2a4f6b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch: SQLite (the migration test) can't drop a constraint in place.
    # Add before drop: MariaDB DDL isn't transactional, so a failure must
    # leave the previous constraint standing, never no constraint at all.
    with op.batch_alter_table("period_results") as batch:
        batch.create_unique_constraint(
            "uq_period_result_player", ["period_type", "period_key", "player_id"]
        )
        batch.drop_constraint("uq_period_result_rank", type_="unique")


def downgrade() -> None:
    # Once a closed period holds a shared rank, adding the rank constraint
    # fails before anything is dropped: the table keeps its player
    # constraint and the version stays at this revision.
    with op.batch_alter_table("period_results") as batch:
        batch.create_unique_constraint(
            "uq_period_result_rank", ["period_type", "period_key", "rank"]
        )
        batch.drop_constraint("uq_period_result_player", type_="unique")
