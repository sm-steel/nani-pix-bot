from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands import inventory as inventory_module
from nani_pix_bot.commands.inventory import inventory_text
from nani_pix_bot.models.enums import ConsumableKind, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.cosmetics import consumables, ofuda
from nani_pix_bot.services.i18n import t

NONE = dict.fromkeys(ConsumableKind, 0)


def test_inventory_text_lists_only_held_kinds() -> None:
    text = inventory_text({**NONE, ConsumableKind.OFUDA: 2}, ofuda_armed=False, lang="EN")

    assert t("inventory.header", "EN") in text
    assert t("inventory.line.ofuda", "EN", count=2) in text
    assert t("inventory.ofuda_hint", "EN") in text
    assert t("inventory.line.clue_token", "EN", count=0) not in text
    assert "🎟" not in text


def test_inventory_text_marks_an_armed_ofuda() -> None:
    text = inventory_text({**NONE, ConsumableKind.OFUDA: 1}, ofuda_armed=True, lang="EN")

    assert text.splitlines()[1] == (
        t("inventory.line.ofuda", "EN", count=1) + t("inventory.ofuda_armed", "EN")
    )
    assert t("inventory.ofuda_hint", "EN") not in text


def test_inventory_text_follows_enum_order() -> None:
    held = dict.fromkeys(ConsumableKind, 1)

    lines = inventory_text(held, ofuda_armed=False, lang="EN").splitlines()

    assert [line[0] for line in lines[1:4]] == ["🎟", "📜", "🧿"]


def test_inventory_text_empty() -> None:
    assert inventory_text(NONE, ofuda_armed=False, lang="EN") == t("inventory.empty", "EN")


def _make_update(*, chat_type: str, thread_id: int | None = 7) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = 555 if chat_type != ChatType.PRIVATE else 1
    update.effective_chat.type = chat_type
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


async def _run(update: MagicMock, session_factory) -> None:
    await inventory_module.inventory_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory))
    )


def _seed(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.flush()
        consumables.add(session, 1, ConsumableKind.OFUDA, "test", 2)
        session.commit()


async def test_inventory_in_dm_and_topic(session_factory) -> None:
    _seed(session_factory)
    dm = _make_update(chat_type=ChatType.PRIVATE, thread_id=None)
    topic = _make_update(chat_type=ChatType.SUPERGROUP)
    elsewhere = _make_update(chat_type=ChatType.SUPERGROUP, thread_id=999)

    for update in (dm, topic, elsewhere):
        await _run(update, session_factory)

    for update in (dm, topic):
        assert "📜 Ofuda: 2" in update.message.reply_text.await_args.args[0]
    elsewhere.message.reply_text.assert_not_awaited()


async def test_inventory_marks_an_ofuda_armed_for_the_active_game(session_factory) -> None:
    _seed(session_factory)
    with session_factory() as session:
        game = Game(starter_id=2, status=GameStatus.ACTIVE, current_stage=PixelStage.STAGE_1)
        session.add(game)
        session.flush()
        ofuda.arm(session, game, 1, datetime(2026, 10, 25, tzinfo=UTC))
        session.commit()
    update = _make_update(chat_type=ChatType.PRIVATE, thread_id=None)

    await _run(update, session_factory)

    assert "one armed for this round" in update.message.reply_text.await_args.args[0]


async def test_inventory_is_logged_at_info(session_factory, log_records) -> None:
    _seed(session_factory)
    update = _make_update(chat_type=ChatType.PRIVATE, thread_id=None)
    update.effective_user.username = "bob"

    await _run(update, session_factory)

    (line,) = [r for r in log_records if r.message.startswith("checked /inventory")]
    assert (line.level, line.extra["holdings"]) == (
        "INFO",
        {"clue_token": 0, "ofuda": 2, "omamori": 0},
    )
