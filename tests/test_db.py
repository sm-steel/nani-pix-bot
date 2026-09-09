import pytest

from nani_pix_bot.db import session_scope
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
