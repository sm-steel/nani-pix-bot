from contextlib import contextmanager
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.error import TimedOut
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import stop as stop_command_module
from nani_pix_bot.commands.helpers.keyboards import (
    STOP_CANCEL_CALLBACK_DATA,
    STOP_CONFIRM_CALLBACK_DATA,
    STOP_REVEAL_CALLBACK_DATA,
)
from nani_pix_bot.models import CluePurchase
from nani_pix_bot.models.enums import ClueKind, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.economy import bounty, config
from nani_pix_bot.services.economy.config import EconomyKey


def _make_update(
    *, user_id: int = 1, full_name: str = "Someone", chat_type: str = "private"
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.full_name = full_name
    update.effective_chat.type = chat_type
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory, *, admin_ids: set[int] | None = None) -> MagicMock:
    admin_ids = admin_ids or set()
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = []
    context.job_queue.get_jobs_by_name.return_value = []
    context.bot.send_message = AsyncMock()
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    context.bot.send_media_group = AsyncMock(
        return_value=[MagicMock(message_id=998), MagicMock(message_id=999)]
    )
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()

    async def _get_chat_member(_chat_id, user_id):
        status = ChatMemberStatus.ADMINISTRATOR if user_id in admin_ids else ChatMemberStatus.MEMBER
        return MagicMock(status=status)

    context.bot.get_chat_member = AsyncMock(side_effect=_get_chat_member)
    return context


def _active_game(session_factory, *, starter_id: int = 1, **overrides) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        defaults = {
            "starter_id": starter_id,
            "original_image": b"file123",
            "status": GameStatus.ACTIVE,
            "current_stage": PixelStage.STAGE_1,
            "title_english": "Frieren: Beyond Journey's End",
        }
        defaults.update(overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


def _active_hard_mode_game(session_factory, *, starter_id: int = 1, **overrides) -> int:
    """The hard-mode analogue of _active_game — current_stage/
    original_image stay unset (a hard-mode game never sets them; it
    carries its fixed screenshot pair via hard_mode_image_a/_b instead)
    — same shape as test_guess.py's own _active_hard_mode_game fixture."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        defaults = {
            "starter_id": starter_id,
            "status": GameStatus.ACTIVE,
            "hard_mode": True,
            "hard_mode_turn": 1,
            "hard_mode_image_a": b"image-a-bytes",
            "hard_mode_image_b": b"image-b-bytes",
            "title_english": "Frieren: Beyond Journey's End",
        }
        defaults.update(overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


def _setup_game(session_factory, *, starter_id: int = 1) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        game = Game(starter_id=starter_id, original_image=b"file123", status=GameStatus.SETUP)
        session.add(game)
        session.commit()
        return game.id


async def test_stop_command_ignores_group_chat_messages(session_factory) -> None:
    _active_game(session_factory)
    update = _make_update(chat_type="supergroup")
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_not_awaited()


async def test_stop_command_replies_when_no_game_is_running(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    assert "reply_markup" not in kwargs


async def test_stop_command_shows_confirmation_for_the_starter(session_factory) -> None:
    _active_game(session_factory, starter_id=1)
    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert STOP_CONFIRM_CALLBACK_DATA in callbacks
    assert STOP_CANCEL_CALLBACK_DATA in callbacks


async def test_stop_command_shows_confirmation_for_a_group_admin(session_factory) -> None:
    _active_game(session_factory, starter_id=1)
    update = _make_update(user_id=2)
    context = _make_context(session_factory, admin_ids={2})

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    assert "reply_markup" in kwargs


async def test_stop_command_rejects_a_non_starter_non_admin(session_factory) -> None:
    _active_game(session_factory, starter_id=1)
    update = _make_update(user_id=2)
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    assert "reply_markup" not in kwargs
    with session_factory() as session:
        assert session.query(Game).count() == 1


def _make_callback_update(*, data: str, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def test_stop_callback_handler_cancel_leaves_the_game_untouched(session_factory) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CANCEL_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.edit_message_text.assert_awaited_once()
    with session_factory() as session:
        assert session.get(Game, game_id) is not None


async def test_stop_callback_handler_confirm_deletes_active_game_and_notifies_group(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None
        turn_state = session.get(TurnState, 1)
        assert turn_state is None or turn_state.next_starter_id is None

    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == 555
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_stop_callback_handler_confirm_schedules_idle_autostart(session_factory) -> None:
    _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert stop_command_module.timeout_module.IDLE_AUTOSTART_JOB_NAME in names


async def test_stop_callback_handler_confirm_cancels_the_inactivity_timers(session_factory) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.job_queue.get_jobs_by_name.assert_any_call(
        stop_command_module.timeout_module.inactivity_nudge_job_name(game_id)
    )
    context.job_queue.get_jobs_by_name.assert_any_call(
        stop_command_module.timeout_module.inactivity_advance_job_name(game_id)
    )


async def test_stop_callback_handler_confirm_deletes_the_game_even_when_the_notice_times_out(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)
    context.bot.send_message = AsyncMock(side_effect=TimedOut())

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None
        turn_state = session.get(TurnState, 1)
        assert turn_state is None or turn_state.next_starter_id is None
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_stop_callback_handler_confirm_deletes_setup_game(session_factory) -> None:
    game_id = _setup_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None


async def test_stop_callback_handler_confirm_rejects_a_non_starter_non_admin_tap(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=2)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is not None
    context.bot.send_message.assert_not_awaited()


async def test_stop_command_offers_reveal_for_an_active_game(session_factory) -> None:
    _active_game(session_factory, starter_id=1)
    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = update.message.reply_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert STOP_REVEAL_CALLBACK_DATA in callbacks


async def test_stop_command_does_not_offer_reveal_for_a_setup_game(session_factory) -> None:
    """Nothing has been posted to the group yet for a SETUP game, so
    there's no answer anyone is waiting on."""
    _setup_game(session_factory, starter_id=1)
    update = _make_update(user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, kwargs = update.message.reply_text.await_args
    callbacks = [
        button.callback_data for row in kwargs["reply_markup"].inline_keyboard for button in row
    ]
    assert STOP_REVEAL_CALLBACK_DATA not in callbacks


async def test_stop_callback_handler_reveal_posts_the_answer_and_deletes_the_game(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_REVEAL_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None
        turn_state = session.get(TurnState, 1)
        assert turn_state is None or turn_state.next_starter_id is None

    context.bot.send_photo.assert_awaited_once()
    _, kwargs = context.bot.send_photo.await_args
    assert kwargs["chat_id"] == 555
    assert kwargs["message_thread_id"] == 7
    assert kwargs["photo"] == b"file123"
    assert "Frieren: Beyond Journey's End" in kwargs["caption"]
    # The reveal caption already says the turn is open — no second notice.
    context.bot.send_message.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_stop_callback_handler_reveal_deletes_the_game_even_when_the_reveal_times_out(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_REVEAL_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None
        turn_state = session.get(TurnState, 1)
        assert turn_state is None or turn_state.next_starter_id is None
    # The reveal never sent, so the confirming starter should be told
    # "confirmed" (plain), not falsely told "confirmed, revealed".
    update.callback_query.edit_message_text.assert_awaited_once()
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "revealed" not in text.lower()


async def test_stop_callback_handler_reveal_cancels_the_timers(session_factory) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_REVEAL_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.job_queue.get_jobs_by_name.assert_any_call(
        stop_command_module.timeout_module.timeout_job_name(game_id)
    )
    context.job_queue.get_jobs_by_name.assert_any_call(
        stop_command_module.timeout_module.inactivity_advance_job_name(game_id)
    )


async def test_stop_callback_handler_reveal_rejects_a_non_starter_non_admin_tap(
    session_factory,
) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_REVEAL_CALLBACK_DATA, user_id=2)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is not None
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()


async def test_stop_callback_handler_reveal_posts_hard_mode_two_photo_album(
    session_factory,
) -> None:
    """A hard-mode /stop-and-reveal posts the stored screenshot pair as a
    2-photo album (post_current_images), not the normal-mode single
    original_image reveal — can_reveal_now must be computed from
    has_hard_mode_reveal_images rather than the always-None
    game.original_image (a hard-mode game never sets it — see
    models/game.py)."""
    game_id = _active_hard_mode_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_REVEAL_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None
        turn_state = session.get(TurnState, 1)
        assert turn_state is None or turn_state.next_starter_id is None

    context.bot.send_media_group.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    _, kwargs = context.bot.send_media_group.await_args
    media = kwargs["media"]
    assert len(media) == 2
    assert media[0].media.input_file_content == b"image-a-bytes"
    assert media[1].media.input_file_content == b"image-b-bytes"
    expected_caption = stop_command_module.i18n.t(
        "stop.hard_mode_stopped_reveal_caption", "en", title="Frieren: Beyond Journey's End"
    )
    assert media[0].caption == expected_caption
    # The reveal caption already says the turn is open — no second notice.
    context.bot.send_message.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_stop_callback_handler_reveal_falls_back_when_hard_mode_image_missing(
    session_factory,
) -> None:
    """has_hard_mode_reveal_images requires BOTH stored screenshots — a
    stale tap that finds only one still present (the other already
    cleared) must fall back to the plain group notice, same as the
    normal-mode "no image stored" fallback."""
    game_id = _active_hard_mode_game(session_factory, starter_id=1, hard_mode_image_b=None)
    update = _make_callback_update(data=STOP_REVEAL_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None
    context.bot.send_media_group.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_message.assert_awaited_once()
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "revealed" not in text.lower()


async def test_stop_confirm_refunds_clue_purchases(session_factory) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    with session_factory() as session:
        buyer = Player(telegram_user_id=2, currency=200)
        session.add(buyer)
        session.flush()
        game = session.get(Game, game_id)
        assert game is not None
        shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.FIRST_LETTER))
        session.commit()
        assert buyer.currency == 200 - config.DEFAULT_AMOUNTS[EconomyKey.CLUE_FIRST_LETTER]
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None
        refunded = session.get(Player, 2)
        assert refunded is not None
        assert refunded.currency == 200
        assert session.query(CluePurchase).count() == 0


async def test_stop_confirm_checks_admin_outside_the_session(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    open_scopes = 0
    real_scope = stop_command_module.session_scope

    @contextmanager
    def _counting_scope(factory):
        nonlocal open_scopes
        open_scopes += 1
        try:
            with real_scope(factory) as session:
                yield session
        finally:
            open_scopes -= 1

    admin_checks: list[int] = []

    async def _is_group_admin(_bot, _chat_id, user_id) -> bool:
        assert open_scopes == 0, "the admin check ran inside an open session"
        admin_checks.append(user_id)
        return True

    monkeypatch.setattr(stop_command_module, "session_scope", _counting_scope)
    monkeypatch.setattr(stop_command_module, "is_group_admin", _is_group_admin)
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=99)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    assert admin_checks == [99]
    with session_factory() as session:
        assert session.get(Game, game_id) is None


async def test_stop_refunds_a_setup_games_preset_deposit(session_factory) -> None:
    game_id = _setup_game(session_factory, starter_id=1)
    with session_factory() as session:
        game = session.get(Game, game_id)
        starter = session.get(Player, 1)
        assert game is not None
        assert starter is not None
        starter.currency = 100
        bounty.contribute(session, game, starter, 30)
        session.commit()
        assert starter.currency == 70
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None
        starter = session.get(Player, 1)
        assert starter is not None
        assert starter.currency == 100
        assert bounty.pot_balance(session, game_id) == 0


async def test_stop_refunds_pot(session_factory) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        for user_id, amount in ((2, 30), (3, 40)):
            player = Player(telegram_user_id=user_id, currency=100)
            session.add(player)
            session.flush()
            bounty.contribute(session, game, player, amount)
        session.commit()
    update = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    context = _make_context(session_factory)

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        assert session.get(Game, game_id) is None
        for user_id in (2, 3):
            player = session.get(Player, user_id)
            assert player is not None
            assert player.currency == 100


async def test_stop_cancel_is_logged_at_info(session_factory, records) -> None:
    _active_game(session_factory, starter_id=1)
    update = _make_callback_update(data=STOP_CANCEL_CALLBACK_DATA, user_id=1)
    update.callback_query.from_user.username = "bob"

    await stop_command_module.stop_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _make_context(session_factory))
    )

    assert ("INFO", "1 (@bob) cancelled /stop") in records


async def test_stop_prompt_and_stop_are_logged_at_info(session_factory, records) -> None:
    game_id = _active_game(session_factory, starter_id=1)
    update = _make_update(user_id=1)
    update.effective_user.username = "bob"
    context = _make_context(session_factory)

    await stop_command_module.stop_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    callback = _make_callback_update(data=STOP_CONFIRM_CALLBACK_DATA, user_id=1)
    callback.callback_query.from_user.username = "bob"
    await stop_command_module.stop_callback_handler(
        cast(Update, callback), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    infos = [message for level, message in records if level == "INFO"]
    assert any(m.startswith(f"Game {game_id}: 1 (@bob) sent /stop — confirm prompt") for m in infos)
    assert f"Game {game_id}: stopped by 1 (@bob) (was ACTIVE, answer not revealed)" in infos
