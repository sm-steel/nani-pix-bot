from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from telegram.error import TimedOut

from nani_pix_bot.jobs import moments
from nani_pix_bot.jobs.moments import CharmMoment
from nani_pix_bot.models.enums import ConsumableKind

CAPTION = "📜 Alice's Ofuda burned away"


def _context() -> MagicMock:
    context = MagicMock()
    context.bot_data = {"group_chat_id": 555, "game_topic_id": 7, "session_factory": MagicMock()}
    context.bot.send_photo = AsyncMock()
    context.bot.send_message = AsyncMock()
    return context


def _moment(kind: ConsumableKind = ConsumableKind.OFUDA) -> CharmMoment:
    return CharmMoment(
        kind,
        user_id=2,
        display_name="Alice",
        title="Saved!",
        detail="Alice kept going",
        caption=CAPTION,
    )


@pytest.fixture(autouse=True)
def _no_avatar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(moments, "fetch_avatar", AsyncMock(return_value=None))


async def test_the_card_goes_to_the_game_topic_with_the_caption(log_records) -> None:
    context = _context()

    await moments.post_charm_moment(context, _moment(), game_id=5)

    sent = context.bot.send_photo.await_args.kwargs
    assert sent["chat_id"] == 555
    assert sent["message_thread_id"] == 7
    assert sent["caption"] == CAPTION
    assert sent["photo"][:2] == b"\xff\xd8"  # a JPEG
    context.bot.send_message.assert_not_awaited()
    line = next(r for r in log_records if r.message == "posted the ofuda card")
    assert line.level == "INFO"
    assert line.extra["moment"] == "ofuda"


async def test_the_clue_token_card_is_drawn_too() -> None:
    context = _context()

    await moments.post_charm_moment(context, _moment(ConsumableKind.CLUE_TICKET), game_id=5)

    context.bot.send_photo.assert_awaited_once()


async def test_a_failed_render_falls_back_to_the_text_line(
    monkeypatch: pytest.MonkeyPatch, log_records
) -> None:
    def broken(*_args, **_kwargs):
        raise OSError("no font")

    monkeypatch.setattr(moments.moment_cards, "charm_card", broken)
    context = _context()

    await moments.post_charm_moment(context, _moment(), game_id=5)

    context.bot.send_photo.assert_not_awaited()
    sent = context.bot.send_message.await_args.kwargs
    assert sent["text"] == CAPTION
    assert sent["message_thread_id"] == 7
    assert any(r.level == "WARNING" for r in log_records)


async def test_a_refused_photo_falls_back_to_the_text_line() -> None:
    context = _context()
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())

    await moments.post_charm_moment(context, _moment(), game_id=5)

    assert context.bot.send_message.await_args.kwargs["text"] == CAPTION


async def test_a_refused_text_fallback_is_only_logged(log_records) -> None:
    context = _context()
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())
    context.bot.send_message = AsyncMock(side_effect=TimedOut())

    await moments.post_charm_moment(context, _moment(), game_id=5)

    assert sum(r.level == "WARNING" for r in log_records) >= 2


async def test_no_visible_avatar_still_posts_the_card_with_the_initials_disc() -> None:
    context = _context()  # the autouse fixture makes fetch_avatar return None

    await moments.post_charm_moment(context, _moment(), game_id=5)

    context.bot.send_photo.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()


def _duel(*, blocked: bool = False, art=True) -> moments.DuelMoment:
    picture = Image.new("RGBA", (64, 64), (200, 50, 50, 255)) if art else None
    shield = Image.new("RGBA", (64, 64), (50, 200, 50, 255)) if blocked else None
    return moments.DuelMoment(
        name="hex kappa",
        actor=moments.Person(1, "@alice", "@alice"),
        target=moments.Person(2, "@bob", "@bob"),
        art=picture,
        title="@bob is hexed!",
        detail="Their next wrong guess costs up to 20 coins",
        caption=CAPTION,
        shield=shield,
        blocked=blocked,
    )


@pytest.mark.parametrize("blocked", [False, True])
async def test_the_duel_card_goes_to_the_game_topic(blocked: bool, log_records) -> None:
    context = _context()

    assert await moments.post_duel_moment(context, _duel(blocked=blocked), hex_id=3)

    sent = context.bot.send_photo.await_args.kwargs
    assert (sent["chat_id"], sent["message_thread_id"], sent["caption"]) == (555, 7, CAPTION)
    assert sent["photo"][:2] == b"\xff\xd8"
    line = next(r for r in log_records if r.message == "posted the hex kappa card")
    assert line.extra["hex_id"] == 3


async def test_a_duel_without_art_posts_the_line(log_records) -> None:
    context = _context()

    assert await moments.post_duel_moment(context, _duel(art=False))

    context.bot.send_photo.assert_not_awaited()
    assert context.bot.send_message.await_args.kwargs["text"] == CAPTION
    assert any(r.level == "WARNING" for r in log_records)


async def test_a_duel_that_reaches_nobody_says_so() -> None:
    context = _context()
    context.bot.send_photo = AsyncMock(side_effect=TimedOut())
    context.bot.send_message = AsyncMock(side_effect=TimedOut())

    assert not await moments.post_duel_moment(context, _duel())
