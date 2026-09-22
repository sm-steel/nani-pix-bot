"""widen mal_credentials token columns

Revision ID: 6c12be962932
Revises: 6a66b9e99547
Create Date: 2026-09-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6c12be962932"
down_revision: str | Sequence[str] | None = "6a66b9e99547"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "mal_credentials",
        "access_token",
        existing_type=sa.String(length=512),
        type_=sa.String(length=4096),
        existing_nullable=False,
    )
    op.alter_column(
        "mal_credentials",
        "refresh_token",
        existing_type=sa.String(length=512),
        type_=sa.String(length=4096),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "mal_credentials",
        "refresh_token",
        existing_type=sa.String(length=4096),
        type_=sa.String(length=512),
        existing_nullable=False,
    )
    op.alter_column(
        "mal_credentials",
        "access_token",
        existing_type=sa.String(length=4096),
        type_=sa.String(length=512),
        existing_nullable=False,
    )
