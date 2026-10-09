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
    with op.batch_alter_table("period_results") as batch:
        batch.drop_constraint("uq_period_result_rank", type_="unique")
        batch.create_unique_constraint(
            "uq_period_result_player", ["period_type", "period_key", "player_id"]
        )


def downgrade() -> None:
    # Fails with an IntegrityError once a closed period holds a shared rank.
    with op.batch_alter_table("period_results") as batch:
        batch.drop_constraint("uq_period_result_player", type_="unique")
        batch.create_unique_constraint(
            "uq_period_result_rank", ["period_type", "period_key", "rank"]
        )
