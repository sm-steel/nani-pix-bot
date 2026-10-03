import pytest

from nani_pix_bot.db import get_engine, session_scope
from nani_pix_bot.models.player import Player


def test_session_scope_commits_on_success(session_factory) -> None:
    with session_scope(session_factory) as session:
        session.add(Player(telegram_user_id=1))

    with session_scope(session_factory) as session:
        assert session.get(Player, 1) is not None


def test_session_scope_rolls_back_on_exception(session_factory) -> None:
    def add_player_then_fail() -> None:
        with session_scope(session_factory) as session:
            session.add(Player(telegram_user_id=2))
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        add_player_then_fail()

    with session_scope(session_factory) as session:
        assert session.get(Player, 2) is None


MARIADB_DEFAULT_WAIT_TIMEOUT = 28800


def test_engine_pings_pooled_connections_before_use() -> None:
    """MariaDB drops connections idle past wait_timeout (8h); without a
    pre-ping the pool hands that dead connection to the first query after
    a quiet night ("MySQL server has gone away", issue #194)."""
    engine = get_engine("sqlite://")
    assert engine.pool._pre_ping is True


def test_engine_recycles_connections_before_mariadb_drops_them() -> None:
    engine = get_engine("sqlite://")
    assert 0 < engine.pool._recycle < MARIADB_DEFAULT_WAIT_TIMEOUT
