from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import onboarding
from nani_pix_bot.commands.shop import menu as shop_menu
from nani_pix_bot.models.enums import ClueKind, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.economy import config
from nani_pix_bot.services.economy.config import EconomyKey

PRICES = config.DEFAULT_AMOUNTS


def _make_update(*, user_id: int = 2, chat_type: str = "private") -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = f"user{user_id}"
    update.effective_chat.type = chat_type
    update.message.reply_text = AsyncMock()
    return update


def _make_context(
    session_factory, *, member: bool = True, args: list[str] | None = None
) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = args or []
    status = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    return context


def _seed_game(session_factory, *, starter_id: int = 1, status=GameStatus.ACTIVE) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        game = Game(
            starter_id=starter_id,
            original_image=b"file123",
            status=status,
            current_stage=PixelStage.STAGE_1,
            title_english="Frieren: Beyond Journey's End",
            shikimori_id=52991,
        )
        session.add(game)
        session.commit()
        return game.id


def _set_currency(session_factory, user_id: int, amount: int) -> None:
    with session_factory() as session:
        player = session.get(Player, user_id)
        if player is None:
            player = Player(telegram_user_id=user_id)
            session.add(player)
        player.currency = amount
        session.commit()


async def _run(update, context):
    await shop_menu.shop_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    return update.message.reply_text.await_args


async def test_shop_ignores_group_chats(session_factory) -> None:
    update = _make_update(chat_type="supergroup")
    await _run(update, _make_context(session_factory))
    update.message.reply_text.assert_not_awaited()


async def test_shop_without_an_active_game_says_so(session_factory) -> None:
    update = _make_update()
    call = await _run(update, _make_context(session_factory))
    assert "No game is running" in call.args[0]


async def test_shop_ignores_a_setup_game(session_factory) -> None:
    _seed_game(session_factory, status=GameStatus.SETUP)
    update = _make_update()
    call = await _run(update, _make_context(session_factory))
    assert "No game is running" in call.args[0]


async def test_shop_is_closed_for_the_setter(session_factory) -> None:
    _seed_game(session_factory, starter_id=2)
    update = _make_update(user_id=2)
    call = await _run(update, _make_context(session_factory))
    assert "You set this round" in call.args[0]


async def test_shop_rejects_a_non_member(session_factory) -> None:
    _seed_game(session_factory)
    update = _make_update()
    call = await _run(update, _make_context(session_factory, member=False))
    assert "member of the group" in call.args[0]


async def test_shop_lists_five_offers_for_a_rich_buyer(session_factory) -> None:
    game_id = _seed_game(session_factory)
    _set_currency(session_factory, 2, 100)
    update = _make_update()
    call = await _run(update, _make_context(session_factory))

    assert "100 💠" in call.args[0]
    rows = call.kwargs["reply_markup"].inline_keyboard
    assert [row[0].callback_data for row in rows] == [
        f"shop:buy:{game_id}:last_letter",
        f"shop:buy:{game_id}:first_letter",
        f"shop:buy:{game_id}:title_shape",
        f"shop:buy:{game_id}:screenshot:0",
        f"shop:buy:{game_id}:tile",
    ]


async def test_menu_still_lists_the_tile_after_someone_else_bought_it(session_factory) -> None:
    game_id = _seed_game(session_factory)
    _set_currency(session_factory, 2, 100)
    _set_currency(session_factory, 3, 100)
    with session_factory() as session:
        game, other = session.get(Game, game_id), session.get(Player, 3)
        assert game is not None
        assert other is not None
        shop.purchase(session, game, other, shop.PurchaseRequest(ClueKind.TILE, tile_index=5))
        session.commit()
    call = await _run(_make_update(), _make_context(session_factory))

    rows = call.kwargs["reply_markup"].inline_keyboard
    assert f"shop:buy:{game_id}:tile" in [row[0].callback_data for row in rows]


async def test_shop_marks_unaffordable_offers_as_locked(session_factory) -> None:
    _seed_game(session_factory)
    # exactly the cheapest clue's price: it is affordable, the next one up is not
    _set_currency(session_factory, 2, PRICES[EconomyKey.CLUE_LAST_LETTER])
    update = _make_update()
    call = await _run(update, _make_context(session_factory))

    rows = call.kwargs["reply_markup"].inline_keyboard
    last_letter = next(r[0] for r in rows if r[0].callback_data.endswith(":last_letter"))
    assert not last_letter.text.startswith("🔒")
    first_letter = next(r[0] for r in rows if r[0].callback_data.endswith(":first_letter"))
    assert first_letter.text.startswith("🔒")


async def test_start_with_shop_arg_opens_the_shop(session_factory) -> None:
    _seed_game(session_factory)
    _set_currency(session_factory, 2, 100)
    update = _make_update()
    context = _make_context(session_factory, args=["shop"])

    await onboarding.start_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    assert "100 💠" in update.message.reply_text.await_args.args[0]


async def test_start_without_args_still_sends_onboarding(session_factory) -> None:
    update = _make_update()
    context = _make_context(session_factory)

    await onboarding.start_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    update.message.reply_text.assert_awaited_once()
    assert "reply_markup" not in update.message.reply_text.await_args.kwargs


async def test_menu_shows_the_hard_mode_discount(session_factory) -> None:
    game_id = _seed_game(session_factory)
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        game.hard_mode = True
        game.hard_mode_turn = 1
        game.hard_mode_clue_discount = 60
        session.commit()
    _set_currency(session_factory, 2, 100)

    call = await _run(_make_update(), _make_context(session_factory))

    assert "\u221260%" in call.args[0]
