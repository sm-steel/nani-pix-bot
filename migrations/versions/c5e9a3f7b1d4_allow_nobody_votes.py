"""allow Nobody votes in the HARD MODE vote

game_votes.candidate_id becomes nullable: NULL is a vote for "Nobody"
(no guess was right), issue #458.

Revision ID: c5e9a3f7b1d4
Revises: a7d4c2e9f1b6
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e9a3f7b1d4"
down_revision: str | Sequence[str] | None = "a7d4c2e9f1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("game_votes") as batch:
        batch.alter_column("candidate_id", existing_type=sa.BigInteger(), nullable=True)


def downgrade() -> None:
    """Not data-safe: Nobody votes have no candidate to keep, so they go."""
    op.execute("DELETE FROM game_votes WHERE candidate_id IS NULL")
    with op.batch_alter_table("game_votes") as batch:
        batch.alter_column("candidate_id", existing_type=sa.BigInteger(), nullable=False)
