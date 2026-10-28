"""The winning /guess is reacted to with the winner's equipped reaction (#392)."""

from collections.abc import Callable, Iterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import Session
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import guess as guess_command_module
from nani_pix_bot.models.enums import ItemSource, PixelStage
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services.cosmetics import inventory
from tests.commands.game_flow.test_guess import (
    _active_game,
    _make_context,
    _make_update,
    _seed_stage_limit,
)
from tests.conftest import LogLine
from tests.hex_fixtures import add_hex

GUESSER = 2


@pytest.fixture
def equip_reaction(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Session, int], None]]:
    """`equip_reaction(session, player_id)` gives the player the fake run's
    love guess reaction (🥰) and equips it."""
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()

    def equip(session: Session, player_id: int) -> None:
        item_id = "demo_1/guess_reaction.love"
        inventory.grant(session, player_id, inventory.ItemGrant(item_id, ItemSource.ADMIN))
        inventory.equip(session, player_id, item_id)

    yield equip
    registry.all_items.cache_clear()


def _seed_guesser(
    session_factory, equip: Callable[[Session, int], None] | None, *, chochin: bool = False
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=GUESSER, username="guesser"))
        session.flush()
        if equip is not None:
            equip(session, GUESSER)
        if chochin:
            add_hex(session, GUESSER, HexKind.CHOCHIN, hours=1)
        session.commit()


async def _guess(session_factory, text: str) -> tuple[MagicMock, MagicMock]:
    update = _make_update(user_id=GUESSER, args=text.split())
    update.message.set_reaction = AsyncMock()
    context = _make_context(session_factory, args=text.split())
    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return update, context


async def test_winner_with_a_reaction_equipped_gets_it_after_the_announcement(
    session_factory, equip_reaction
) -> None:
    _active_game(session_factory)
    _seed_guesser(session_factory, equip_reaction)
    order: list[str] = []
    update = _make_update(user_id=GUESSER, args=["frieren"])
    update.message.set_reaction = AsyncMock(side_effect=lambda **_: order.append("react"))
    context = _make_context(session_factory, args=["frieren"])
    context.bot.send_photo = AsyncMock(
        side_effect=lambda **_: order.append("announce") or MagicMock(message_id=999)
    )

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.set_reaction.assert_awaited_once_with(reaction="🥰")
    assert order == ["announce", "react"]


async def test_winner_with_nothing_equipped_is_not_reacted_to(
    session_factory, equip_reaction
) -> None:
    _active_game(session_factory)
    _seed_guesser(session_factory, None)

    update, context = await _guess(session_factory, "frieren")

    context.bot.send_photo.assert_awaited()
    update.message.set_reaction.assert_not_awaited()


async def test_reaction_failure_does_not_block_the_win(
    session_factory, equip_reaction, log_records: list[LogLine]
) -> None:
    _active_game(session_factory)
    _seed_guesser(session_factory, equip_reaction)
    update = _make_update(user_id=GUESSER, args=["frieren"])
    update.message.set_reaction = AsyncMock(side_effect=BadRequest("REACTION_INVALID"))
    context = _make_context(session_factory, args=["frieren"])

    await guess_command_module.guess_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_awaited()
    warnings = [r for r in log_records if r.level == "WARNING" and "couldn't react" in r.message]
    assert len(warnings) == 1
    assert warnings[0].extra["reaction"] == "🥰"
    assert "REACTION_INVALID" in str(warnings[0].extra["error"])


async def test_a_wrong_guess_never_reacts(session_factory, equip_reaction) -> None:
    _active_game(session_factory)
    _seed_stage_limit(session_factory, PixelStage.STAGE_1, wrong_guess_limit=3)
    _seed_guesser(session_factory, equip_reaction)

    update, _ = await _guess(session_factory, "naruto")

    update.message.set_reaction.assert_not_awaited()


async def test_wrong_guess_by_a_chochin_target_is_reacted_to(
    session_factory, log_records: list[LogLine]
) -> None:
    _active_game(session_factory)
    _seed_stage_limit(session_factory, PixelStage.STAGE_1, wrong_guess_limit=3)
    _seed_guesser(session_factory, None, chochin=True)

    update, _ = await _guess(session_factory, "naruto")

    update.message.set_reaction.assert_awaited_once_with(reaction="🤪")
    infos = [r for r in log_records if r.level == "INFO" and "reacted" in r.message]
    assert len(infos) == 1
    assert infos[0].extra["chochin"] is True


async def test_winning_guess_by_a_chochin_target_reacts_with_the_prank_not_the_equipped_one(
    session_factory, equip_reaction
) -> None:
    _active_game(session_factory)
    _seed_guesser(session_factory, equip_reaction, chochin=True)

    update, _ = await _guess(session_factory, "frieren")

    update.message.set_reaction.assert_awaited_once_with(reaction="🤪")
