from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
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
from nani_pix_bot.services.cards import moments as moment_cards
from nani_pix_bot.services.seasons import hexes, omikuji
from nani_pix_bot.services.seasons.omikuji import Draw
from tests.seasons.fake_runs.demo_3 import RUN as DEMO_3

ALICE = 1
PRIVATE, TOPIC = ChatType.PRIVATE, ChatType.SUPERGROUP
CAST_AT = datetime(2026, 10, 30, 12, 0, tzinfo=UTC).replace(tzinfo=None)
JPEG = b"\xff\xd8"


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


def _png() -> bytes:
    out = BytesIO()
    Image.new("RGBA", (200, 300), (240, 230, 200, 255)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def slip(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    art = MagicMock(return_value=_png())
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


def _is_jpeg(photo: object) -> bool:
    return isinstance(photo, bytes) and photo[:2] == JPEG


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


# --- the card's text ---


def _detail(draw: Draw, lang: str = "en") -> str:
    return omikuji_command.card_detail(draw, "@alice", lang)


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_card_title_per_kind(lang: str):
    for kind in OmikujiKind:
        title = omikuji_command.card_title(_draw(kind), "@alice", lang)
        assert title == i18n.t(f"omikuji.card_title.{kind.value}", lang, name="@alice")
        assert "@alice" in title


def test_card_title_text():
    title = omikuji_command.card_title(_draw(OmikujiKind.DAIKICHI), "@alice", "en")
    assert title == "@alice drew Daikichi!"


def test_card_detail_coins_only():
    draw = _draw(OmikujiKind.DAIKICHI, coins=50, consumable=None)
    assert _detail(draw) == "+50 coins for @alice"


def test_card_detail_coins_and_gift():
    draw = _draw(OmikujiKind.KICHI, coins=30, consumable=ConsumableKind.OFUDA)
    assert _detail(draw) == "+30 coins and an Ofuda for @alice"


def test_card_detail_gift_only():
    draw = _draw(OmikujiKind.KICHI, coins=0, consumable=ConsumableKind.OMAMORI)
    assert _detail(draw) == "An Omamori for @alice"


def test_card_detail_kyo_prank():
    draw = _draw(OmikujiKind.KYO, prank=_prank("chochin", 6))
    assert _detail(draw) == i18n.t("hex.card_detail.chochin", "en", target="@alice", hours=6)
    assert _detail(draw) == "For 6 h every guess by @alice gets laughed at"


def test_card_detail_kyo_spared():
    draw = _draw(OmikujiKind.KYO, spared=True)
    assert _detail(draw) == "The spirits pass @alice by this time"


def test_card_detail_nothing():
    draw = _draw(OmikujiKind.SHOKICHI, coins=0, consumable=None)
    assert _detail(draw) == "Just a quiet blessing for @alice"


def test_card_text_has_every_key_in_russian(log_records):
    draws = [
        _draw(OmikujiKind.DAIKICHI, coins=50, consumable=None),
        _draw(OmikujiKind.KICHI, coins=30, consumable=ConsumableKind.OFUDA),
        _draw(OmikujiKind.KICHI, coins=0, consumable=ConsumableKind.OMAMORI),
        _draw(OmikujiKind.KYO, prank=_prank("nopperabo", 3)),
        _draw(OmikujiKind.KYO, spared=True),
        _draw(OmikujiKind.SHOKICHI, coins=0, consumable=None),
    ]
    for draw in draws:
        assert "@alice" in _detail(draw, "ru")
        assert "@alice" in omikuji_command.card_title(draw, "@alice", "ru")
    assert [r for r in log_records if r.level in ("WARNING", "ERROR")] == []
    assert _detail(draws[2], "ru") == "Омамори для @alice"


# --- the command ---


async def test_a_topic_draw_replies_with_one_card(session_factory, monkeypatch, slip, member):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.DAIKICHI)
    update, context = await _omikuji(session_factory, TOPIC)
    member.assert_not_awaited()
    update.message.reply_photo.assert_awaited_once()
    kwargs = update.message.reply_photo.await_args.kwargs
    assert _is_jpeg(kwargs["photo"])
    assert "@alice" in kwargs["caption"]
    assert f"+{outcome_of(OmikujiKind.DAIKICHI).coins} 🪙" in kwargs["caption"]
    slip.assert_called_once_with("demo_3", OmikujiKind.DAIKICHI)
    update.message.reply_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()
    with session_factory() as session:
        assert len(list(session.scalars(select(OmikujiDraw)))) == 1


async def test_a_topic_kyo_posts_only_the_reply(session_factory, monkeypatch, slip):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KYO)
    update, context = await _omikuji(session_factory, TOPIC)
    assert len(_hexes(session_factory)) == 1
    update.message.reply_photo.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()


async def test_a_dm_draw_without_kyo_posts_nothing_to_the_topic(session_factory, monkeypatch, slip):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KICHI)
    update, context = await _omikuji(session_factory)
    assert _is_jpeg(update.message.reply_photo.await_args.kwargs["photo"])
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()


async def test_a_dm_kyo_posts_the_same_card_to_the_topic(session_factory, monkeypatch, slip):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KYO)
    monkeypatch.setattr(hexes, "PRANK_HEXES", frozenset({HexKind.NOPPERABO}))
    update, context = await _omikuji(session_factory)
    (prank,) = _hexes(session_factory)
    assert prank.target_id == prank.caster_id == ALICE
    assert prank.kind == HexKind.NOPPERABO.value
    dm_card = update.message.reply_photo.await_args.kwargs["photo"]
    assert _is_jpeg(dm_card)
    context.bot.send_photo.assert_awaited_once()
    kwargs = context.bot.send_photo.await_args.kwargs
    assert (kwargs["chat_id"], kwargs["message_thread_id"]) == (555, 7)
    assert kwargs["photo"] == dm_card  # rendered once, sent twice
    assert prank.expires_at is not None
    hours = round((prank.expires_at - prank.cast_at).total_seconds() / 3600)
    expected = i18n.t("hex.notice.self_prank.nopperabo", "en", target="@alice", hours=hours)
    assert kwargs["caption"] == expected  # unmasked, even under the fresh Nopperabō
    context.bot.send_message.assert_not_awaited()


def _spy_cards(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    drawn: list[tuple] = []
    real = moment_cards.charm_card

    def spy(mood, face, art, caption):
        drawn.append((mood, face, caption))
        return real(mood, face, art, caption)

    monkeypatch.setattr(moments.moment_cards, "charm_card", spy)
    return drawn


def _already_faceless(session_factory) -> None:
    """Bob's Nopperabō has been on Alice for an hour."""
    now = datetime.now(UTC).replace(tzinfo=None)
    with session_factory() as session:
        season = session.scalars(select(SeasonSchedule)).one()
        session.add(Player(telegram_user_id=2, username="bob"))
        session.add(
            Hex(
                season_id=season.id,
                kind=HexKind.NOPPERABO.value,
                caster_id=2,
                target_id=ALICE,
                cast_at=now - timedelta(hours=1),
                expires_at=now + timedelta(hours=5),
            )
        )
        session.commit()


@pytest.mark.parametrize("kind", [OmikujiKind.KICHI, OmikujiKind.KYO])
async def test_an_already_faceless_drawer_stays_masked(session_factory, monkeypatch, slip, kind):
    """Only a prank landing now unmasks the drawer: a Nopperabō they're already
    under masks the card and the caption (a kyō is spared: they're hexed)."""
    _season(session_factory)
    _already_faceless(session_factory)
    force(monkeypatch, kind)
    drawn = _spy_cards(monkeypatch)
    update, context = await _omikuji(session_factory, TOPIC)
    ((_mood, face, card),) = drawn
    faceless = i18n.t("faceless.label", "en")
    assert face.name == faceless
    assert card.title == i18n.t(f"omikuji.card_title.{kind.value}", "en", name=faceless)
    assert faceless in card.detail
    reply = update.message.reply_photo.await_args.kwargs["caption"]
    for text in (face.name, card.title, card.detail, reply):
        assert "alice" not in text
    assert "👤" in reply
    context.bot.send_photo.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()


async def test_the_card_names_the_drawer_unmasked(session_factory, monkeypatch, slip):
    """A Nopperabō kyō landing now: the one moment the real name shows."""
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KYO)
    monkeypatch.setattr(hexes, "PRANK_HEXES", frozenset({HexKind.NOPPERABO}))
    drawn = _spy_cards(monkeypatch)
    await _omikuji(session_factory)
    ((mood, face, caption),) = drawn
    assert mood == moment_cards.Mood.OMIKUJI
    assert face.name == "@alice"
    assert caption.title == "@alice drew Kyō!"
    assert "@alice" in caption.detail


async def test_a_failed_slip_reply_still_posts_the_topic_card(
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


async def test_missing_art_falls_back_to_text(session_factory, monkeypatch):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KICHI)
    monkeypatch.setattr(omikuji_command.omikuji, "slip_png", lambda *_a: None)
    update, context = await _omikuji(session_factory)
    update.message.reply_photo.assert_not_awaited()
    text = update.message.reply_text.await_args.args[0]
    assert "@alice" in text
    assert "📜" in text
    context.bot.send_message.assert_not_awaited()


async def test_missing_art_on_a_dm_kyo_posts_the_line_to_the_topic(session_factory, monkeypatch):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KYO)
    monkeypatch.setattr(omikuji_command.omikuji, "slip_png", lambda *_a: None)
    update, context = await _omikuji(session_factory)
    update.message.reply_text.assert_awaited_once()
    context.bot.send_photo.assert_not_awaited()
    kwargs = context.bot.send_message.await_args.kwargs
    assert (kwargs["chat_id"], kwargs["message_thread_id"]) == (555, 7)
    assert "🎴" in kwargs["text"]


async def test_a_render_failure_falls_back_to_text(session_factory, monkeypatch, slip, log_records):
    _season(session_factory)
    force(monkeypatch, OmikujiKind.KYO)

    def broken(*_args):
        raise OSError("no font")

    monkeypatch.setattr(moments.moment_cards, "charm_card", broken)
    update, context = await _omikuji(session_factory)
    update.message.reply_photo.assert_not_awaited()
    assert "@alice" in update.message.reply_text.await_args.args[0]
    context.bot.send_photo.assert_not_awaited()
    assert "🎴" in context.bot.send_message.await_args.kwargs["text"]
    warned = [r for r in log_records if r.message.startswith("could not draw the omikuji card")]
    assert [r.level for r in warned] == ["WARNING"]


async def test_a_spared_kyo_in_a_dm_posts_nothing_to_the_topic(session_factory, monkeypatch, slip):
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
