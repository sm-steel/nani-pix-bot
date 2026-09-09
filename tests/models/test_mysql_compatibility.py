"""SQLite tolerates VARCHAR with no length; MariaDB (the real production
DB) doesn't — this caught a real deploy-time failure once. Guard against
it recurring."""

from sqlalchemy import String

from nani_pix_bot.models import Base


def test_every_string_column_has_an_explicit_length() -> None:
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, String) and column.type.length is None
    ]
    assert offenders == []
