from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatType
from telegram.error import TimedOut
from telegram.ext import ContextTypes

from nani_pix_bot.commands import omikuji as omikuji_command
from nani_pix_bot.jobs import moments
from nani_pix_bot.models.enums import ConsumableKind, SeasonStatus
from nani_pix_bot.models.halloween import Hex, OmikujiDraw
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons.mechanics import HexKind, OmikujiKind
from nani_pix_bot.services import i18n
from nani_pix_bot.services.seasons import hexes, omikuji
from nani_pix_bot.services.seasons.omikuji import Draw
from tests.seasons.fake_runs.demo_3 import RUN as DEMO_3

ALICE = 1
PRIVATE, TOPIC = ChatType.PRIVATE, ChatType.SUPERGROUP
CAST_AT = datetime(2026, 10, 30, 12, 0, tzinfo=UTC).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _season_runs(fake_runs):
    """The shared fake runs: demo_3 has omikuji outcomes."""


@pytest.fixture(autouse=True)
def _no_avatars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(moments, "fetch_avatar", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def member(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    member = AsyncMock(return_value=True)
    monkeypatch.setattr(omikuji_command, "is_group_member", member)
    return member


@pytest.fixture
def slip(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    art = MagicMock(return_value=b"png")
    monkeypatch.setattr(omikuji_command.omikuji, "slip_png", art)
    return art


def outcome_of(kind: OmikujiKind):
    return next(o for o in DEMO_3.omikuji if o.kind == kind)


def force(monkeypatch: pytest.MonkeyPatch, kind: OmikujiKind) -> None:
    monkeypatch.setattr(omikuji, "pick", lambda _outcomes, _rng: outcome_of(kind))


def _season(session_factory, *, active: bool = True) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    with session_factory() as session:
        session.add(
            SeasonSchedule(
                run_id="demo_3",
                start_at=now - timedelta(days=1),
                end_at=now + timedelta(days=9),
                status=SeasonStatus.ACTIVE if active else SeasonStatus.SCHEDULED,
                created_by=9,
            )
        )
        session.merge(Player(telegram_user_id=ALICE, username="alice"))
        session.commit()


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_photo = AsyncMock()
    context.bot.send_message = AsyncMock()
    return context


def _update(chat_type: str = PRIVATE) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ALICE
    update.effective_user.username = "alice"
    update.effective_chat.id = 555 if chat_type != PRIVATE else ALICE
    update.effective_chat.type = chat_type
    update.message.message_thread_id = 7
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    update.message.reply_photo = AsyncMock()
    return update


async def _omikuji(session_factory, chat_type: str = PRIVATE) -> tuple[MagicMock, MagicMock]:
    update, context = _update(chat_type), _context(session_factory)
    await omikuji_command.omikuji_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return update, context


def _draw(kind: OmikujiKind, *, prank: Hex | None = None, spared: bool = False, **changes):
    outcome = replace(outcome_of(kind), **changes)
    return Draw(DEMO_3, outcome, date(2026, 10, 30), prank, spared)


def _prank(kind: str, hours: int) -> Hex:
    return Hex(
        season_id=1,
        kind=kind,
        caster_id=ALICE,
        target_id=ALICE,
        cast_at=CAST_AT,
        expires_at=CAST_AT + timedelta(hours=hours),
    )


def _hexes(session_factory) -> list[Hex]:
    with session_factory() as session:
        return list(session.scalars(select(Hex)))


# --- caption ---


def test_caption_daikichi_with_coins_and_gift():
    text = omikuji_command.caption(
        _draw(OmikujiKind.DAIKICHI, consumable=ConsumableKind.OFUDA), "@alice", "en"
    )
    assert "@alice" in text
    assert "Daikichi" in text or "大吉" in text
    assert f"+{outcome_of(OmikujiKind.DAIKICHI).coins} 🪙" in text
    assert "📜" in text


def test_caption_gift_omamori_and_no_coins_line_for_zero():
    text = omikuji_command.caption(
        _draw(OmikujiKind.SHOKICHI, coins=0, consumable=ConsumableKind.OMAMORI), "@a", "ru"
    )
    assert "🧿" in text
    assert "🪙" not in text


def test_caption_kyo_variants():
    pranked = omikuji_command.caption(
        _draw(OmikujiKind.KYO, prank=_prank("chochin", 6)), "@alice", "en"
    )
    assert i18n.t("omikuji.prank.chochin", "en", hours=6) in pranked
    faceless = omikuji_command.caption(
        _draw(OmikujiKind.KYO, prank=_prank("nopperabo", 3)), "@alice", "ru"
    )
    assert i18n.t("omikuji.prank.nopperabo", "ru", hours=3) in faceless
    spared = omikuji_command.caption(_draw(OmikujiKind.KYO, spared=True), "@alice", "en")
    assert i18n.t("omikuji.prank_spared", "en") in spared
    assert "👤" not in spared
    assert "🏮" not in spared


# --- the command ---


async def test_omikuji_sends_the_slip_photo(session_factory, monkeypatch, slip):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.DAIKICHI)
    update, context = await _omikuji(session_factory)
    update.message.reply_photo.assert_awaited_once()
    kwargs = update.message.reply_photo.await_args.kwargs
    assert kwargs["photo"] == b"png"
    assert "@alice" in kwargs["caption"]
    assert f"+{outcome_of(OmikujiKind.DAIKICHI).coins} 🪙" in kwargs["caption"]
    slip.assert_called_once_with("demo_3", OmikujiKind.DAIKICHI)
    update.message.reply_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()
    with session_factory() as session:
        assert len(list(session.scalars(select(OmikujiDraw)))) == 1


async def test_omikuji_falls_back_to_text_without_art(session_factory, monkeypatch):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KICHI)
    monkeypatch.setattr(omikuji_command.omikuji, "slip_png", lambda *_a: None)
    update, _ = await _omikuji(session_factory)
    update.message.reply_photo.assert_not_awaited()
    text = update.message.reply_text.await_args.args[0]
    assert "@alice" in text
    assert "📜" in text


async def test_kyo_in_dm_posts_the_kyo_card(session_factory, monkeypatch, slip):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KYO)
    monkeypatch.setattr(hexes, "PRANK_HEXES", frozenset({HexKind.NOPPERABO}))
    update, context = await _omikuji(session_factory)
    (prank,) = _hexes(session_factory)
    assert prank.target_id == prank.caster_id == ALICE
    assert prank.kind == HexKind.NOPPERABO.value
    update.message.reply_photo.assert_awaited_once()
    context.bot.send_photo.assert_awaited_once()
    kwargs = context.bot.send_photo.await_args.kwargs
    assert (kwargs["chat_id"], kwargs["message_thread_id"]) == (555, 7)
    assert prank.expires_at is not None
    hours = round((prank.expires_at - prank.cast_at).total_seconds() / 3600)
    expected = i18n.t("hex.notice.self_prank.nopperabo", "en", target="@alice", hours=hours)
    assert kwargs["caption"] == expected  # unmasked, even under the fresh Nopperabō
    assert kwargs["photo"]
    context.bot.send_message.assert_not_awaited()


async def test_kyo_in_the_topic_replies_and_posts_exactly_one_card(
    session_factory, monkeypatch, slip, member
):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KYO)
    update, context = await _omikuji(session_factory, TOPIC)
    member.assert_not_awaited()
    update.message.reply_photo.assert_awaited_once()
    context.bot.send_photo.assert_awaited_once()
    assert "🎴" in context.bot.send_photo.await_args.kwargs["caption"]
    context.bot.send_message.assert_not_awaited()


async def test_a_failed_slip_reply_still_posts_the_kyo_card(
    session_factory, monkeypatch, slip, log_records
):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KYO)
    update = _update()
    update.message.reply_photo = AsyncMock(side_effect=TimedOut())
    update.message.reply_text = AsyncMock(side_effect=TimedOut())
    context = _context(session_factory)
    await omikuji_command.omikuji_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    context.bot.send_photo.assert_awaited_once()
    assert "🎴" in context.bot.send_photo.await_args.kwargs["caption"]
    failed = [r for r in log_records if r.message == "could not send the omikuji slip reply"]
    assert [r.level for r in failed] == ["WARNING"]
    with session_factory() as session:
        assert len(list(session.scalars(select(OmikujiDraw)))) == 1


async def test_kyo_card_falls_back_to_its_line(session_factory, monkeypatch, slip):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KYO)
    monkeypatch.setattr(omikuji_command.hex_art, "art", lambda _kind: None)
    _, context = await _omikuji(session_factory)
    context.bot.send_photo.assert_not_awaited()
    text = context.bot.send_message.await_args.kwargs["text"]
    assert "🎴" in text
    assert len(_hexes(session_factory)) == 1


async def test_a_spared_kyo_posts_no_card(session_factory, monkeypatch, slip):
    _season(session_factory)
    with session_factory() as session:
        player = session.get(Player, ALICE)
        assert player is not None
        player.hex_opt_out = True
        session.commit()
    force(monkeypatch, OmikujiKind.KYO)
    update, context = await _omikuji(session_factory)
    caption = update.message.reply_photo.await_args.kwargs["caption"]
    assert i18n.t("omikuji.prank_spared", "en") in caption
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()
    assert _hexes(session_factory) == []


async def test_already_and_closed_reply_their_texts(session_factory, monkeypatch, slip):
    update, _ = await _omikuji(session_factory)
    assert update.message.reply_text.await_args.args[0] == i18n.t("omikuji.closed", "en")
    _season(session_factory)
    force(monkeypatch, OmikujiKind.SHOKICHI)
    await _omikuji(session_factory)
    update, _ = await _omikuji(session_factory)
    assert update.message.reply_text.await_args.args[0] == i18n.t("omikuji.already", "en")
    update.message.reply_photo.assert_not_awaited()


async def test_dm_non_member_is_refused(session_factory, monkeypatch, slip, member):
    _season(session_factory)
    member.return_value = False
    update, _ = await _omikuji(session_factory)
    text = update.message.reply_text.await_args.args[0]
    assert text == i18n.t("dm_start.not_a_member", "en")
    with session_factory() as session:
        assert list(session.scalars(select(OmikujiDraw))) == []


async def test_outside_the_topic_is_ignored(session_factory, slip):
    _season(session_factory)
    update, context = _update(TOPIC), _context(session_factory)
    update.message.message_thread_id = 99
    await omikuji_command.omikuji_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    update.message.reply_text.assert_not_awaited()
    update.message.reply_photo.assert_not_awaited()


async def test_the_draw_is_logged_with_where(session_factory, monkeypatch, slip, log_records):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.SHOKICHI)
    await _omikuji(session_factory, TOPIC)
    drew = [r for r in log_records if r.message == "drew /omikuji in topic"]
    assert drew
    assert drew[0].level == "INFO"
    assert drew[0].extra["where"] == "topic"
