from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatType
from telegram.error import TimedOut
from telegram.ext import ContextTypes

from nani_pix_bot.commands import hexes as hex_commands
from nani_pix_bot.jobs import moments
from nani_pix_bot.models.enums import (
    ConsumableKind,
    CurrencyReason,
    GameStatus,
    HexOutcome,
    PixelStage,
    SeasonStatus,
)
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cosmetics import consumables
from nani_pix_bot.services.economy import wallet
from tests.seasons.fake_runs.demo_3 import RUN as DEMO_3

ALICE, BOB, CAROL, DAVE, STRANGER = 1, 2, 3, 4, 5
PRIVATE, TOPIC = ChatType.PRIVATE, ChatType.SUPERGROUP


@pytest.fixture(autouse=True)
def _season_runs(fake_runs):
    """The shared fake runs: demo_3 has all four hexes."""


@pytest.fixture(autouse=True)
def _no_avatars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(moments, "fetch_avatar", AsyncMock(return_value=None))


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _seed(session_factory, *, guessers: tuple[int, ...] = (ALICE, BOB)) -> int:
    """Alice and Bob guessed in a demo_3 game Dave hosted; Carol never played.
    Alice and Bob hold 200 🪙 each. Returns the season id."""
    with session_factory() as session:
        season = SeasonSchedule(
            run_id="demo_3",
            start_at=_now() - timedelta(days=1),
            end_at=_now() + timedelta(days=9),
            status=SeasonStatus.ACTIVE,
            created_by=9,
        )
        session.add(season)
        names = {ALICE: "alice", BOB: "bob", CAROL: "carol", DAVE: "dave"}
        session.add_all([Player(telegram_user_id=u, username=n) for u, n in names.items()])
        session.add_all(
            [Player(telegram_user_id=u) for u in guessers if u not in names]  # extra players
        )
        session.flush()
        game = Game(
            starter_id=DAVE,
            status=GameStatus.ACTIVE,
            current_stage=PixelStage.STAGE_1,
            title_english="Clannad",
            season_id=season.id,
            activated_at=_now(),
        )
        session.add(game)
        session.flush()
        session.add_all(
            [GameGuess(game_id=game.id, player_id=u, text="x", stage=1) for u in guessers]
        )
        for user in (ALICE, BOB):
            entry = wallet.LedgerEntry(CurrencyReason.SEASON_WIN, season_id=season.id, coins=True)
            wallet.credit(session, session.get(Player, user), 200, entry)
        session.commit()
        return season.id


def _context(session_factory, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = args or []
    context.bot.send_photo = AsyncMock()
    context.bot.send_message = AsyncMock()
    return context


def _update(
    chat_type: str = PRIVATE, *, user_id: int = ALICE, thread_id: int | None = 7
) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "alice" if user_id == ALICE else None
    update.effective_chat.id = 555 if chat_type != PRIVATE else user_id
    update.effective_chat.type = chat_type
    update.message.chat.type = chat_type
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


async def _hex(session_factory, args, chat_type=PRIVATE, **kwargs) -> tuple[MagicMock, MagicMock]:
    update, context = _update(chat_type, **kwargs), _context(session_factory, args)
    await hex_commands.hex_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    return update, context


async def _hexes(session_factory, args, user_id: int = ALICE) -> str:
    update, context = _update(user_id=user_id), _context(session_factory, args)
    await hex_commands.hexes_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    return _reply(update)


def _query_update(data: str, *, user_id: int = ALICE, chat_type: str = PRIVATE) -> MagicMock:
    update = MagicMock()
    query = update.callback_query
    query.data = data
    query.from_user.id = user_id
    query.from_user.username = "alice" if user_id == ALICE else None
    query.message.chat.type = chat_type
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.delete_message = AsyncMock()
    return update


async def _tap(session_factory, data: str, **kwargs) -> tuple[MagicMock, MagicMock]:
    update, context = _query_update(data, **kwargs), _context(session_factory)
    await hex_commands.hex_callback(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    return update.callback_query, context


def _reply(update: MagicMock) -> str:
    update.message.reply_text.assert_awaited_once()
    return update.message.reply_text.await_args.args[0]


def _keyboard(markup) -> list[list[tuple[str, str]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


def _hexes_on(session_factory) -> list[Hex]:
    with session_factory() as session:
        return list(session.scalars(select(Hex)))


def _coins(session_factory, season_id: int, user: int) -> int:
    with session_factory() as session:
        return wallet.balance(session, user, season_id=season_id)


def _opted_out(session_factory, user: int) -> bool:
    with session_factory() as session:
        player = session.get(Player, user)
        assert player is not None
        return player.hex_opt_out


def _caption(context: MagicMock) -> str:
    context.bot.send_photo.assert_awaited_once()
    return context.bot.send_photo.await_args.kwargs["caption"]


# --- /hex @user <kind> ---


def _spy_cards(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every Caption the duel cards are drawn with."""
    captions: list = []
    cards = moments.moment_cards
    real_duel, real_blocked = cards.duel_card, cards.blocked_duel_card

    def duel(mood, duel_, art, caption, blocked=False):
        captions.append(caption)
        return real_duel(mood, duel_, art, caption, blocked)

    def blocked(mood, duel_, attack, defence, caption):
        captions.append(caption)
        return real_blocked(mood, duel_, attack, defence, caption)

    monkeypatch.setattr(cards, "duel_card", duel)
    monkeypatch.setattr(cards, "blocked_duel_card", blocked)
    return captions


@pytest.mark.parametrize("kind", ["nopperabo", "chochin", "kappa", "kitsune"])
async def test_every_cast_card_carries_an_effect_line(
    session_factory, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _seed(session_factory)
    captions = _spy_cards(monkeypatch)

    await _hex(session_factory, ["@bob", kind])

    [caption] = captions
    assert caption.detail.strip()
    assert "{" not in caption.detail


async def test_a_blocked_card_names_the_caster_and_the_hex_in_its_effect_line(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(session_factory)
    with session_factory() as session:
        consumables.add(session, BOB, ConsumableKind.OMAMORI, why="test")
        session.commit()
    captions = _spy_cards(monkeypatch)

    await _hex(session_factory, ["@bob", "kitsune"])

    [caption] = captions
    assert "@alice" in caption.detail
    assert i18n.t("hex.kind.kitsune.name", "EN") in caption.detail


async def test_hex_with_a_kind_casts_and_posts_the_notice_to_the_topic(session_factory) -> None:
    season_id = _seed(session_factory)

    update, context = await _hex(session_factory, ["@bob", "kappa"])

    sent = context.bot.send_photo.await_args.kwargs
    assert (sent["chat_id"], sent["message_thread_id"]) == (555, 7)
    assert "@bob" in sent["caption"]
    assert "@alice" in sent["caption"]
    assert sent["photo"][:2] == b"\xff\xd8"
    assert _reply(update) == i18n.t("hex.cast_done", "EN")
    [row] = _hexes_on(session_factory)
    assert (row.kind, row.caster_id, row.target_id) == ("kappa", ALICE, BOB)
    assert _coins(session_factory, season_id, ALICE) == 170


async def test_hex_in_the_topic_lets_the_notice_speak(session_factory) -> None:
    _seed(session_factory)

    update, context = await _hex(session_factory, ["bob", "KITSUNE"], TOPIC)

    assert "@bob" in _caption(context)
    update.message.reply_text.assert_not_awaited()


async def test_a_blocked_hex_posts_the_block_notice(session_factory) -> None:
    season_id = _seed(session_factory)
    with session_factory() as session:
        consumables.add(session, BOB, ConsumableKind.OMAMORI, why="test")
        session.commit()

    _, context = await _hex(session_factory, ["@bob", "chochin"])

    caption = _caption(context)
    assert caption.startswith("🧿")
    assert i18n.t("hex.kind.chochin.name", "EN") in caption
    [row] = _hexes_on(session_factory)
    assert row.outcome == HexOutcome.BLOCKED.value
    assert _coins(session_factory, season_id, ALICE) == 180  # still paid


async def test_refusal_reply_carries_the_wait(session_factory) -> None:
    _seed(session_factory)
    await _hex(session_factory, ["@bob", "kappa"])

    update, context = await _hex(session_factory, ["@dave", "kappa"])

    reply = _reply(update)
    assert i18n.t("duration.minute", "EN", n=59) in reply
    assert reply.startswith(i18n.t("hex.refusal.cooldown", "EN", wait="")[:20])
    context.bot.send_photo.assert_not_awaited()


async def test_insufficient_coins_reply_quotes_price_and_balance(session_factory) -> None:
    _seed(session_factory)

    update, _ = await _hex(session_factory, ["@alice", "kappa"], user_id=DAVE)

    assert _reply(update) == i18n.t("hex.refusal.insufficient", "EN", price=30, balance=0)


async def test_unknown_username(session_factory, log_records) -> None:
    _seed(session_factory)

    update, _ = await _hex(session_factory, ["@nobody", "kappa"])

    assert _reply(update) == i18n.t("hex.unknown_user", "EN", username="nobody")
    assert any(r.level == "WARNING" and "unknown username" in r.message for r in log_records)


@pytest.mark.parametrize("args", [["@bob", "dragon"], ["@bob", "kappa", "now"]])
async def test_malformed_args_reply_usage(session_factory, args) -> None:
    _seed(session_factory)

    update, _ = await _hex(session_factory, args)

    assert _reply(update) == i18n.t("hex.usage", "EN")
    assert _hexes_on(session_factory) == []


async def test_hex_is_ignored_outside_dm_and_the_game_topic(session_factory) -> None:
    _seed(session_factory)

    update, context = await _hex(session_factory, ["@bob", "kappa"], TOPIC, thread_id=8)

    update.message.reply_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()
    assert _hexes_on(session_factory) == []


async def test_topic_post_failure_keeps_the_hex(session_factory, log_records) -> None:
    _seed(session_factory)
    update, context = _update(), _context(session_factory, ["@bob", "kappa"])
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())
    context.bot.send_message = AsyncMock(side_effect=TimedOut())

    await hex_commands.hex_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))

    assert len(_hexes_on(session_factory)) == 1
    assert _reply(update) == i18n.t("hex.cast_done", "EN")
    assert any(r.level == "ERROR" and "never reached" in r.message for r in log_records)


# --- pickers ---


async def test_hex_without_args_shows_the_rules_and_the_player_picker(session_factory) -> None:
    _seed(session_factory)

    update, _ = await _hex(session_factory, [])

    text = update.message.reply_text.await_args.args[0]
    rows = _keyboard(update.message.reply_text.await_args.kwargs["reply_markup"])
    assert text.startswith(i18n.t("hex.help.header", "EN"))
    assert all(i18n.t(f"hex.kind.{r.kind.value}.name", "EN") in text for r in DEMO_3.hexes)
    assert i18n.t("hexes.status.in", "EN") in text
    assert text.endswith(i18n.t("hex.pick_target", "EN"))
    assert [row[0] for row in rows[:-1]] == [("@dave", "hex:1:t:4"), ("@bob", "hex:1:t:2")]
    assert rows[-1] == [(i18n.t("player_picker.cancel", "EN"), "hex:1:x")]


async def test_hex_with_a_user_shows_one_button_per_run_hex(session_factory) -> None:
    _seed(session_factory)

    update, _ = await _hex(session_factory, ["@bob"])

    assert update.message.reply_text.await_args.args[0] == i18n.t("hex.pick", "EN", target="@bob")
    rows = _keyboard(update.message.reply_text.await_args.kwargs["reply_markup"])
    assert len(rows) == len(DEMO_3.hexes) + 1
    assert rows[0][0][1] == "hex:1:2:nopperabo"
    assert rows[-1] == [
        (i18n.t("hex.back", "EN"), "hex:1:p:0"),
        (i18n.t("hex.cancel", "EN"), "hex:1:x"),
    ]


async def test_no_targets_message(session_factory) -> None:
    _seed(session_factory)
    with session_factory() as session:
        for user in (BOB, DAVE):
            player = session.get(Player, user)
            assert player is not None
            player.hex_opt_out = True
        session.commit()

    update, _ = await _hex(session_factory, [])

    assert update.message.reply_text.await_args.args[0].endswith(i18n.t("hex.no_targets", "EN"))
    assert update.message.reply_text.await_args.kwargs["reply_markup"] is None


async def test_hex_without_a_season_is_closed(session_factory) -> None:
    update, _ = await _hex(session_factory, [])

    assert update.message.reply_text.await_args.args[0] == i18n.t("hex.closed", "EN")


async def test_player_picker_pages(session_factory) -> None:
    _seed(session_factory, guessers=(ALICE, BOB, *range(10, 17)))  # 9 targets with Dave

    update, _ = await _hex(session_factory, [])
    first = _keyboard(update.message.reply_text.await_args.kwargs["reply_markup"])
    query, _ = await _tap(session_factory, "hex:1:p:1")
    second = _keyboard(query.edit_message_text.await_args.kwargs["reply_markup"])

    assert len(first) == 9
    assert first[-1][-1] == (i18n.t("player_picker.next", "EN"), "hex:1:p:1")
    assert len(second) == 2
    assert second[-1][0] == (i18n.t("player_picker.prev", "EN"), "hex:1:p:0")


async def test_tapping_a_player_opens_the_kind_picker(session_factory) -> None:
    _seed(session_factory)

    query, _ = await _tap(session_factory, "hex:1:t:2")

    query.answer.assert_awaited_once_with()
    assert query.edit_message_text.await_args.args[0] == i18n.t("hex.pick", "EN", target="@bob")
    rows = _keyboard(query.edit_message_text.await_args.kwargs["reply_markup"])
    assert len(rows) == len(DEMO_3.hexes) + 1


async def test_back_returns_to_the_player_picker(session_factory) -> None:
    _seed(session_factory)

    query, _ = await _tap(session_factory, "hex:1:p:0")

    assert query.edit_message_text.await_args.args[0].endswith(i18n.t("hex.pick_target", "EN"))


async def test_tapping_a_kind_casts(session_factory) -> None:
    _seed(session_factory)

    query, context = await _tap(session_factory, "hex:1:2:nopperabo")

    [row] = _hexes_on(session_factory)
    assert (row.kind, row.target_id) == ("nopperabo", BOB)
    assert "@bob" in _caption(context)
    query.edit_message_text.assert_awaited_once_with(i18n.t("hex.cast_done", "EN"))


async def test_a_cast_tap_in_the_topic_removes_the_picker(session_factory) -> None:
    _seed(session_factory)

    query, _ = await _tap(session_factory, "hex:1:2:kappa", chat_type=TOPIC)

    query.delete_message.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()


@pytest.mark.parametrize("data", ["hex:1:2:kappa", "hex:1:p:0", "hex:1:t:2", "hex:1:x"])
async def test_someone_elses_tap_is_refused(session_factory, data: str) -> None:
    season_id = _seed(session_factory)

    query, context = await _tap(session_factory, data, user_id=DAVE)

    query.answer.assert_awaited_once_with(i18n.t("hex.not_yours", "EN"), show_alert=True)
    query.edit_message_text.assert_not_awaited()
    assert _hexes_on(session_factory) == []
    assert _coins(session_factory, season_id, ALICE) == 200
    context.bot.send_photo.assert_not_awaited()


async def test_a_stale_picker_after_the_season_closed_casts_nothing(session_factory) -> None:
    season_id = _seed(session_factory)
    with session_factory() as session:
        season = session.get(SeasonSchedule, season_id)
        assert season is not None
        season.status = SeasonStatus.ENDED
        session.commit()

    query, context = await _tap(session_factory, "hex:1:2:kappa")

    query.answer.assert_awaited_once_with(i18n.t("hex.refusal.closed", "EN"), show_alert=True)
    assert _hexes_on(session_factory) == []
    assert _coins(session_factory, season_id, ALICE) == 200
    context.bot.send_photo.assert_not_awaited()


async def test_cancel_edits_the_picker(session_factory) -> None:
    _seed(session_factory)

    query, _ = await _tap(session_factory, "hex:1:x")

    query.edit_message_text.assert_awaited_once_with(i18n.t("hex.cancelled", "EN"))


@pytest.mark.parametrize("data", ["hex:x:x", "hex:1", "hex:1:q:2", "hex:1:2:dragon", "hex:1:p:a"])
async def test_a_malformed_tap_is_only_answered(session_factory, data: str, log_records) -> None:
    _seed(session_factory)

    query, _ = await _tap(session_factory, data)

    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_not_awaited()
    assert any(r.level == "WARNING" and "malformed" in r.message for r in log_records)


# --- /hexes ---


async def test_hexes_off_and_on(session_factory) -> None:
    _seed(session_factory)

    assert await _hexes(session_factory, ["off"]) == i18n.t("hexes.off", "EN")
    assert _opted_out(session_factory, ALICE) is True
    assert await _hexes(session_factory, ["ON"]) == i18n.t("hexes.on", "EN")
    assert _opted_out(session_factory, ALICE) is False


async def test_hexes_off_is_locked_after_casting(session_factory) -> None:
    _seed(session_factory)
    await _hex(session_factory, ["@bob", "kappa"])

    reply = await _hexes(session_factory, ["off"])

    assert reply.startswith(i18n.t("hex.refusal.opt_out_locked", "EN", wait="")[:30])
    assert i18n.t("duration.hour", "EN", n=23) in reply


async def test_hexes_usage(session_factory) -> None:
    assert await _hexes(session_factory, ["maybe"]) == i18n.t("hexes.usage", "EN")


async def test_hexes_shows_my_active_hex_and_cooldowns(session_factory) -> None:
    _seed(session_factory)
    await _hex(session_factory, ["@bob", "chochin"])

    mine = await _hexes(session_factory, [])
    theirs = await _hexes(session_factory, [], user_id=BOB)

    assert i18n.t("hexes.cooldowns", "EN") in mine
    assert i18n.t("hex.kind.chochin.name", "EN") in mine
    assert i18n.t("hex.kind.kappa.name", "EN") not in mine
    chochin = i18n.t("hex.kind.chochin.name", "EN")
    assert i18n.t("hexes.active", "EN", icon="🏮", name=chochin, left="1h 59m") in theirs
    assert i18n.t("hexes.status.in", "EN") in theirs


async def test_hexes_without_a_season_shows_only_the_state(session_factory) -> None:
    reply = await _hexes(session_factory, [])

    assert reply == "\n".join([i18n.t("hexes.status.in", "EN"), i18n.t("hex.closed", "EN")])
