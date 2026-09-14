from typing import cast
from unittest.mock import MagicMock

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers import player_tracking
from nani_pix_bot.models.player import Player


def _make_context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory}
    return context


def _make_update(
    *, user_id: int | None = 1, username: str | None = "someone", is_bot: bool = False
) -> MagicMock:
    update = MagicMock()
    if user_id is None:
        update.effective_user = None
        return update
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.is_bot = is_bot
    return update


async def _remember(update, context) -> None:
    await player_tracking.remember_user(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )


async def test_remember_user_records_someone_the_bot_has_never_seen(session_factory) -> None:
    """The whole point: plain chat in the game topic is enough to make
    /correct @them resolvable later."""
    await _remember(_make_update(user_id=4242, username="lurker"), _make_context(session_factory))

    with session_factory() as session:
        player = session.get(Player, 4242)
        assert player is not None
        assert player.username == "lurker"


async def test_remember_user_refreshes_a_changed_username(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1, username="old_handle"))
        session.commit()

    await _remember(_make_update(user_id=1, username="new_handle"), _make_context(session_factory))

    with session_factory() as session:
        player = session.get(Player, 1)
        assert player is not None
        assert player.username == "new_handle"


async def test_remember_user_keeps_a_stored_username_when_the_user_has_none(
    session_factory,
) -> None:
    """A user who removed their @handle shouldn't blank out the one we
    already know — /correct and /skip still need something to match."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1, username="still_useful"))
        session.commit()

    await _remember(_make_update(user_id=1, username=None), _make_context(session_factory))

    with session_factory() as session:
        player = session.get(Player, 1)
        assert player is not None
        assert player.username == "still_useful"


async def test_remember_user_ignores_other_bots(session_factory) -> None:
    await _remember(_make_update(user_id=42, is_bot=True), _make_context(session_factory))

    with session_factory() as session:
        assert session.get(Player, 42) is None


async def test_remember_user_ignores_an_update_with_no_user(session_factory) -> None:
    await _remember(_make_update(user_id=None), _make_context(session_factory))

    with session_factory() as session:
        assert session.query(Player).count() == 0
