from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import history
from nani_pix_bot.commands.helpers import durations
from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.commands.history import data
from nani_pix_bot.commands.history.data import Filter, GameRequest, ListRequest, Tab
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.clue_purchase import CluePurchase
from nani_pix_bot.models.enums import ClueKind, CurrencyReason, EventType, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.game_vote import GameVote
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import events, i18n
from nani_pix_bot.services.economy import bounty, wallet

HOST, ALICE, BOB = 1, 2, 3
T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _players(session: Session) -> None:
    session.add_all(
        Player(telegram_user_id=u, username=n)
        for u, n in ((HOST, "host"), (ALICE, "alice"), (BOB, "bob"))
    )
    session.flush()


def _game(session: Session, status: GameStatus, ended: datetime, **fields) -> Game:
    values = {
        "starter_id": HOST,
        "status": status,
        "current_stage": PixelStage.STAGE_2,
        "title_english": "Frieren",
        "title_romaji": "Sousou no Frieren",
        "source": "shikimori",
        "ended_at": ended,
        "activated_at": ended - timedelta(hours=1, minutes=5),
    } | fields
    game = Game(**values)
    session.add(game)
    session.flush()
    return game


def test_callback_data_round_trips_and_rejects_junk() -> None:
    listing = ListRequest(Filter.PLAYED, 3)
    one = GameRequest(104, listing, Tab.GUESSES, 2)
    assert data.parse(data.list_data(listing)) == listing
    assert data.parse(data.game_data(one)) == one
    junk = ("hist:l:x:1", "hist:l:a", "hist:g:1:a:0", "hist:g:x:a:0:0", "hist:g:1:a:0:x:0")
    for raw in (*junk, "hist:z:1", "ach:1"):
        assert data.parse(raw) is None


def test_a_game_button_from_before_the_tabs_opens_the_record() -> None:
    assert data.parse("hist:g:104:m:3:2") == GameRequest(104, ListRequest(Filter.PLAYED, 3))


@pytest.mark.parametrize(
    ("raw", "filt"),
    [("a", Filter.ALL), ("m", Filter.PLAYED), ("h", Filter.HOSTED), ("w", Filter.WON)],
)
def test_every_filter_parses_including_the_legacy_only_mine_value(raw: str, filt: Filter) -> None:
    assert data.parse(f"hist:l:{raw}:0") == ListRequest(filt, 0)


@pytest.mark.parametrize(
    ("span", "text"),
    [
        (timedelta(seconds=20), "1m"),
        (timedelta(hours=1, minutes=5), "1h 5m"),
        (timedelta(days=2, hours=3, minutes=7), "2d 3h"),
    ],
)
def test_durations_read_with_the_two_largest_units(span: timedelta, text: str) -> None:
    assert durations.duration(span, "en") == text


def test_the_list_shows_finished_games_with_a_button_each(session: Session) -> None:
    _players(session)
    won = _game(session, GameStatus.WON, T0, winner_id=ALICE)
    lost = _game(session, GameStatus.UNSOLVED, T0 + timedelta(days=1))
    _game(session, GameStatus.ACTIVE, T0)

    markdown, markup = history.list_view(session, BOB, ListRequest(), "en")

    assert "| # | Date | Anime | Winner | Host |" in markdown
    assert (
        f"| {lost.id} | {md_escape('02.10.26')} | Frieren | ❌ | {md_escape('@host')} |" in markdown
    )
    assert f"| {won.id} | {md_escape('01.10.26')} | Frieren | {md_escape('@alice')} |" in markdown
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert [b.text for b in buttons[:2]] == [f"#{lost.id}", f"#{won.id}"]


def test_the_filters_are_one_row_of_four_tabs_with_the_current_marked(session: Session) -> None:
    _players(session)

    _markdown, markup = history.list_view(session, BOB, ListRequest(Filter.HOSTED), "en")

    tabs = markup.inline_keyboard[-1]
    assert [b.text for b in tabs] == [
        i18n.t("history.filter.all", "en"),
        i18n.t("history.filter.played", "en"),
        "● " + i18n.t("history.filter.hosted", "en"),
        i18n.t("history.filter.won", "en"),
    ]
    assert [data.parse(str(b.callback_data)) for b in tabs] == [ListRequest(f, 0) for f in Filter]


@pytest.mark.parametrize("filt", [Filter.PLAYED, Filter.HOSTED, Filter.WON])
def test_each_player_filter_has_its_title_and_empty_state(session: Session, filt: Filter) -> None:
    _players(session)
    _game(session, GameStatus.WON, T0, winner_id=ALICE)

    markdown, _markup = history.list_view(session, BOB, ListRequest(filt), "en")

    name = filt.name.lower()
    assert md_escape(i18n.t(f"history.title.{name}", "en")) in markdown
    assert md_escape(i18n.t(f"history.empty.{name}", "en")) in markdown


def test_the_won_filter_lists_the_viewers_wins(session: Session) -> None:
    _players(session)
    mine = _game(session, GameStatus.WON, T0, winner_id=BOB)
    _game(session, GameStatus.WON, T0, winner_id=ALICE, starter_id=BOB)

    _markdown, markup = history.list_view(session, BOB, ListRequest(Filter.WON), "en")

    picks = [b.text for row in markup.inline_keyboard[:-1] for b in row]
    assert picks == [f"#{mine.id}"]


def test_a_won_game_shows_the_full_record_and_its_guesses(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.WON, T0, winner_id=ALICE, total_guess_count=2)
    session.add_all(
        [
            GameGuess(game_id=game.id, player_id=BOB, text="naruto_x", stage=1, created_at=T0),
            GameGuess(game_id=game.id, player_id=ALICE, text="frieren", stage=2, correct=True),
        ]
    )
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=ALICE, subject_id=HOST, game_id=game.id),
        stage=2,
        hard_mode=False,
        how="guess",
        pot=30,
        seconds=3900,
        ended_at=T0.isoformat(),
    )
    back = ListRequest(Filter.ALL, 1)

    rendered = history.game_view(session, GameRequest(game.id, back), "en")
    guesses = history.game_view(session, GameRequest(game.id, back, Tab.GUESSES), "en")

    assert rendered is not None
    assert guesses is not None
    markdown = rendered[0]
    for fact in (
        f"Game #{game.id} · Frieren",
        "Also known as: Sousou no Frieren",
        "Host: @host",
        "Anime found via Shikimori",
        "Screenshot uploaded by the host",
        "lasted 1h 5m",
        "Won by @alice at stage 2/5 with /guess",
        "Solved in 1h 5m",
        "Bounty paid out: 30 💠",
        "Standings: +4 🌟 to the winner",
    ):
        assert md_escape(fact) in markdown, fact
    assert "naruto" not in markdown
    log = guesses[0]
    assert md_escape("Guesses: 2") in log
    assert md_escape("naruto_x") in log
    assert log.index("naruto") < log.index("| frieren")
    for _view, view_markup in (rendered, guesses):
        back_button = view_markup.inline_keyboard[-1][0]
        assert data.parse(str(back_button.callback_data)) == back


def test_an_unsolved_legacy_game_says_why_and_that_guesses_were_not_logged(
    session: Session,
) -> None:
    _players(session)
    game = _game(session, GameStatus.UNSOLVED, T0, total_guess_count=4)
    events.emit(
        session,
        EventType.GAME_UNSOLVED,
        events.Involved(subject_id=HOST, game_id=game.id),
        cause="2-day timeout",
        hard_mode=False,
        distinct_guessers=2,
    )

    rendered = history.game_view(session, GameRequest(game.id, ListRequest()), "ru")
    log = history.game_view(session, GameRequest(game.id, ListRequest(), Tab.GUESSES), "ru")

    assert rendered is not None
    assert log is not None
    unsolved = i18n.t("history.detail.unsolved", "ru")
    timeout = i18n.t("history.detail.unsolved.timeout", "ru")
    assert md_escape(f"{unsolved} — {timeout}") in rendered[0]
    assert md_escape(i18n.t("history.detail.no_log", "ru")) in log[0]


def test_a_long_guess_log_pages(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.UNSOLVED, T0)
    session.add_all(
        GameGuess(game_id=game.id, player_id=BOB, text=f"g{i}", stage=1) for i in range(30)
    )
    session.flush()

    rendered = history.game_view(session, GameRequest(game.id, ListRequest(), Tab.GUESSES, 1), "en")
    record = history.game_view(session, GameRequest(game.id, ListRequest()), "en")

    assert rendered is not None
    assert record is not None
    markdown, markup = rendered
    assert markdown.count("| g") == 5
    assert "2/2" in [b.text for row in markup.inline_keyboard for b in row]
    assert "2/2" not in [b.text for row in record[1].inline_keyboard for b in row]


def test_the_game_view_has_record_and_guesses_tabs_with_the_current_marked(
    session: Session,
) -> None:
    _players(session)
    game = _game(session, GameStatus.UNSOLVED, T0, total_guess_count=7)
    back = ListRequest(Filter.WON, 2)

    rendered = history.game_view(session, GameRequest(game.id, back, Tab.GUESSES), "en")

    assert rendered is not None
    tabs = rendered[1].inline_keyboard[0]
    assert [b.text for b in tabs] == [
        i18n.t("history.tab.record", "en"),
        "● " + i18n.t("history.tab.guesses", "en", count=7),
    ]
    assert [data.parse(str(b.callback_data)) for b in tabs] == [
        GameRequest(game.id, back, Tab.RECORD),
        GameRequest(game.id, back, Tab.GUESSES),
    ]


def _buy(session: Session, game: Game, buyer_id: int, kind: ClueKind, price: int, **fields) -> None:
    buyer = session.get(Player, buyer_id)
    assert buyer is not None
    buyer.currency = 1000
    entry = wallet.LedgerEntry(CurrencyReason.CLUE_PURCHASE, game_id=game.id)
    charge = wallet.debit(session, buyer, price, entry)
    session.flush()
    session.add(
        CluePurchase(
            game_id=game.id, player_id=buyer_id, kind=kind, transfer_id=charge.id, **fields
        )
    )
    session.flush()


def test_the_record_shows_clues_bounty_stages_guessers_and_votes(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.ACTIVE, T0, hard_mode=True)
    _buy(session, game, BOB, ClueKind.FIRST_LETTER, 25, created_at=T0, shared_at=T0)
    _buy(session, game, ALICE, ClueKind.TILE, 40, created_at=T0 + timedelta(minutes=1))
    bob = session.get(Player, BOB)
    assert bob is not None
    bounty.contribute(session, game, bob, 30)
    bounty.contribute(session, game, bob, 70)
    events.emit(
        session,
        EventType.STAGE_ADVANCED,
        events.Involved(game_id=game.id),
        from_stage=1,
        to_stage=2,
        reason="sharpened",
    )
    session.add(GameVote(game_id=game.id, voter_id=BOB, candidate_id=ALICE))
    game.status = GameStatus.UNSOLVED
    events.emit(
        session,
        EventType.GAME_UNSOLVED,
        events.Involved(subject_id=HOST, game_id=game.id),
        cause="vote closed with no winner",
        hard_mode=True,
        distinct_guessers=3,
    )

    rendered = history.game_view(session, GameRequest(game.id, ListRequest()), "en")

    assert rendered is not None
    markdown = rendered[0]
    for part in (
        "### " + md_escape(i18n.t("history.detail.clues", "en", count=2)),
        "| " + md_escape("@bob") + " | first letter | 25 | ",
        "| " + md_escape("@alice") + " | unpixelated tile | 40 | — |",
        md_escape(i18n.t("history.detail.bounty", "en", contributions="@bob 100 💠")),
        "### " + md_escape(i18n.t("history.detail.stages", "en")),
        md_escape(i18n.t("history.detail.stage.sharpened", "en")),
        md_escape(i18n.t("history.detail.guessers", "en", count=3)),
        md_escape(i18n.t("history.detail.votes", "en")),
        md_escape("@bob → @alice"),
    ):
        assert part in markdown, part


def test_the_record_leaves_out_parts_a_game_has_none_of(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.WON, T0, winner_id=ALICE)

    rendered = history.game_view(session, GameRequest(game.id, ListRequest()), "en")

    assert rendered is not None
    markdown = rendered[0]
    for key in ("history.detail.stages", "history.detail.votes"):
        assert md_escape(i18n.t(key, "en")) not in markdown, key
    assert "🛒" not in markdown
    assert md_escape(i18n.t("history.col.clue", "en")) not in markdown
    assert "💰" not in markdown
    assert "👥" not in markdown


def _context(session_factory, *, member: bool = True) -> MagicMock:
    context = MagicMock()
    status = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=status))
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    return context


def _dm_update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = BOB
    update.effective_chat.type = "private"
    update.message.chat_id = BOB
    update.message.reply_text = AsyncMock()
    return update


async def test_the_command_sends_the_list_in_dm(session_factory, log_records) -> None:
    with patch.object(history, "send_rich", new=AsyncMock()) as sent:
        await history.history_command(
            cast(Update, _dm_update()), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
        )

    sent.assert_awaited_once()
    assert any(r.message == "opened the game history" for r in log_records)


async def test_the_command_refuses_a_non_member(session_factory) -> None:
    update = _dm_update()

    with patch.object(history, "send_rich", new=AsyncMock()) as sent:
        await history.history_command(
            cast(Update, update),
            cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, member=False)),
        )

    sent.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with(i18n.t("dm_start.not_a_member", "en"))


def _tap(callback_data: str) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = callback_data
    update.callback_query.answer = AsyncMock()
    update.callback_query.from_user.id = BOB
    update.callback_query.message.chat.id = BOB
    update.callback_query.message.message_id = 5
    return update


async def test_a_tap_on_a_deleted_game_alerts_instead_of_editing(
    session_factory, log_records
) -> None:
    update = _tap(data.game_data(GameRequest(404, ListRequest())))

    with patch.object(history, "edit_rich", new=AsyncMock()) as edited:
        await history.history_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
        )

    edited.assert_not_awaited()
    update.callback_query.answer.assert_awaited_once_with(
        i18n.t("history.stale", "en"), show_alert=True
    )
    assert any(r.level == "WARNING" and r.extra.get("game_id") == 404 for r in log_records)


async def test_a_list_tap_edits_the_message(session_factory) -> None:
    with session_scope(session_factory) as session:
        _players(session)
        _game(session, GameStatus.WON, T0, winner_id=ALICE)
    update = _tap(data.list_data(ListRequest(Filter.ALL, 0)))

    with patch.object(history, "edit_rich", new=AsyncMock()) as edited:
        await history.history_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
        )

    edited.assert_awaited_once()
    assert edited.await_args_list[0].args[1].message_id == 5
