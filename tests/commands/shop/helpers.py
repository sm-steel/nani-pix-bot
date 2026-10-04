"""Shared fixtures-as-functions for the shop callback tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands.shop import callbacks
from nani_pix_bot.models import CluePurchase
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.economy import config


def make_query(data: str, *, user_id: int = 2) -> MagicMock:
    query = MagicMock()
    query.data = data
    query.from_user.id = user_id
    query.from_user.username = f"user{user_id}"
    query.from_user.full_name = "Buyer Name"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    return query


def make_context(session_factory, *, member: bool = True) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_message = AsyncMock()
    status = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    return context


def seed_game(session_factory, *, status=GameStatus.ACTIVE, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        fields = {
            "starter_id": 1,
            "original_image": b"file123",
            "status": status,
            "current_stage": PixelStage.STAGE_1,
            "title_romaji": "Sousou no Frieren",
        }
        fields.update(overrides)
        game = Game(**fields)
        session.add(game)
        session.commit()
        return game.id


def set_currency(session_factory, user_id: int, amount: int) -> None:
    with session_factory() as session:
        player = session.get(Player, user_id)
        if player is None:
            player = Player(telegram_user_id=user_id)
            session.add(player)
        player.currency = amount
        session.commit()


def balance(session_factory, user_id: int = 2) -> int:
    with session_factory() as session:
        player = session.get(Player, user_id)
        assert player is not None
        return player.currency


def purchases(session_factory) -> list[CluePurchase]:
    with session_factory() as session:
        return list(session.scalars(select(CluePurchase)))


async def tap(context, query) -> None:
    update = MagicMock()
    update.callback_query = query
    await callbacks.shop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )


RICH = 1000  # a balance that covers any clue several times over
PRICES = config.DEFAULT_AMOUNTS
