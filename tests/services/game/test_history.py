from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.clue_purchase import CluePurchase
from nani_pix_bot.models.enums import ClueKind, CurrencyReason, EventType, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.game_vote import GameVote
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import events
from nani_pix_bot.services.economy import bounty, wallet
from nani_pix_bot.services.game import history

HOST, ALICE, BOB, CAROL = 1, 2, 3, 4
T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _players(session: Session) -> None:
    session.add_all(Player(telegram_user_id=u) for u in (HOST, ALICE, BOB, CAROL))
    session.flush()


def _game(session: Session, status: GameStatus, *, ended: datetime | None, **fields) -> Game:
    values = {
        "starter_id": HOST,
        "status": status,
        "current_stage": PixelStage.STAGE_2,
        "title_english": "Frieren",
        "ended_at": ended,
    } | fields
    game = Game(**values)
    session.add(game)
    session.flush()
    return game


def test_finished_games_skip_running_ones_newest_first(session: Session) -> None:
    _players(session)
    old = _game(session, GameStatus.WON, ended=T0, winner_id=ALICE)
    new = _game(session, GameStatus.UNSOLVED, ended=T0 + timedelta(days=1))
    for status in (GameStatus.SETUP, GameStatus.ACTIVE, GameStatus.VOTING):
        _game(session, status, ended=None)

    found = history.finished_games(session, limit=10)

    assert [g.id for g in found] == [new.id, old.id]
    assert history.count_finished(session) == 2


def test_a_game_from_before_ended_at_was_recorded_sorts_by_its_start(session: Session) -> None:
    _players(session)
    legacy = _game(session, GameStatus.WON, ended=None, created_at=T0 + timedelta(days=5))
    recent = _game(session, GameStatus.WON, ended=T0 + timedelta(days=2))

    assert [g.id for g in history.finished_games(session, limit=10)] == [legacy.id, recent.id]


def _involvement_games(session: Session) -> tuple[Game, Game, Game]:
    _players(session)
    hosted = _game(session, GameStatus.UNSOLVED, ended=T0, starter_id=BOB)
    won = _game(session, GameStatus.WON, ended=T0 + timedelta(hours=1), winner_id=BOB)
    guessed = _game(session, GameStatus.UNSOLVED, ended=T0 + timedelta(hours=2))
    _game(session, GameStatus.WON, ended=T0 + timedelta(hours=3), winner_id=CAROL)
    session.add(GameGuess(game_id=guessed.id, player_id=BOB, text="naruto", stage=1))
    session.add(GameGuess(game_id=guessed.id, player_id=BOB, text="bleach", stage=2))
    session.flush()
    return hosted, won, guessed


def test_played_means_hosted_won_or_guessed_in(session: Session) -> None:
    hosted, won, guessed = _involvement_games(session)
    played = history.Scope(history.Involvement.PLAYED, BOB)

    mine = history.finished_games(session, played, limit=10)

    assert [g.id for g in mine] == [guessed.id, won.id, hosted.id]
    assert history.count_finished(session, played) == 3
    page = history.finished_games(session, played, limit=1, offset=1)
    assert [g.id for g in page] == [won.id]


def test_hosted_means_only_games_the_player_hosted(session: Session) -> None:
    hosted, _won, _guessed = _involvement_games(session)
    scope = history.Scope(history.Involvement.HOSTED, BOB)

    assert [g.id for g in history.finished_games(session, scope, limit=10)] == [hosted.id]
    assert history.count_finished(session, scope) == 1


def test_won_means_only_games_the_player_won(session: Session) -> None:
    _hosted, won, _guessed = _involvement_games(session)
    scope = history.Scope(history.Involvement.WON, BOB)

    assert [g.id for g in history.finished_games(session, scope, limit=10)] == [won.id]
    assert history.count_finished(session, scope) == 1


def test_a_player_filter_without_a_player_is_a_bug() -> None:
    with pytest.raises(ValueError, match="needs a player"):
        history.Scope(history.Involvement.WON)


def test_detail_of_a_won_game_carries_how_it_was_won_and_the_guess_log(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.WON, ended=T0, winner_id=ALICE)
    session.add_all(
        [
            GameGuess(game_id=game.id, player_id=BOB, text="naruto", stage=1, created_at=T0),
            GameGuess(
                game_id=game.id,
                player_id=ALICE,
                text="frieren",
                stage=2,
                correct=True,
                created_at=T0 + timedelta(minutes=1),
            ),
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
        seconds=600,
        ended_at=T0.isoformat(),
    )

    detail = history.game_detail(session, game.id)

    assert detail is not None
    assert detail.how == "guess"
    assert detail.pot == 30
    assert detail.seconds == 600
    assert detail.points == 4  # a stage-2 win
    assert detail.unsolved is None
    assert [(g.text, g.correct) for g in detail.guesses] == [("naruto", False), ("frieren", True)]


def test_detail_of_an_unsolved_game_has_its_cause(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.UNSOLVED, ended=T0)
    events.emit(
        session,
        EventType.GAME_UNSOLVED,
        events.Involved(subject_id=HOST, game_id=game.id),
        cause="2-day timeout",
        hard_mode=False,
        distinct_guessers=0,
    )

    detail = history.game_detail(session, game.id)

    assert detail is not None
    assert detail.unsolved is history.UnsolvedReason.TIMEOUT
    assert detail.points is None


def test_detail_of_a_game_from_before_the_event_log_just_lacks_those_fields(
    session: Session,
) -> None:
    _players(session)
    game = _game(session, GameStatus.WON, ended=None, winner_id=ALICE)

    detail = history.game_detail(session, game.id)

    assert detail is not None
    assert (detail.how, detail.pot, detail.seconds, detail.points) == (None, None, None, None)
    assert detail.guesses == []


def test_no_detail_for_a_running_or_missing_game(session: Session) -> None:
    _players(session)
    running = _game(session, GameStatus.ACTIVE, ended=None)

    assert history.game_detail(session, running.id) is None
    assert history.game_detail(session, 9999) is None


def test_unsolved_causes_map_to_reasons_players_can_read() -> None:
    reason = history.unsolved_reason
    assert reason("2-day timeout") is history.UnsolvedReason.TIMEOUT
    assert reason("final stage exhausted — 3/3 wrong") is history.UnsolvedReason.EXHAUSTED
    assert reason("final stage exhausted; nobody guessed, so no vote") is (
        history.UnsolvedReason.NO_VOTE
    )
    assert reason("vote closed with no winner (votes {})") is history.UnsolvedReason.VOTE_FAILED
    assert reason("something new") is history.UnsolvedReason.OTHER


def _funded(session: Session, user_id: int) -> Player:
    player = session.get(Player, user_id)
    assert player is not None
    player.currency = 1000
    return player


def _clue(
    session: Session, game: Game, buyer: Player, kind: ClueKind, price: int, **fields
) -> CluePurchase:
    entry = wallet.LedgerEntry(CurrencyReason.CLUE_PURCHASE, game_id=game.id)
    charge = wallet.debit(session, buyer, price, entry)
    session.flush()
    purchase = CluePurchase(
        game_id=game.id,
        player_id=buyer.telegram_user_id,
        kind=kind,
        transfer_id=charge.id,
        **fields,
    )
    session.add(purchase)
    session.flush()
    return purchase


def test_detail_lists_clues_in_order_with_their_price_and_share_time(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.WON, ended=T0, winner_id=ALICE)
    bob, carol = _funded(session, BOB), _funded(session, CAROL)
    shared = T0 + timedelta(minutes=3)
    _clue(session, game, carol, ClueKind.TILE, 40, created_at=T0 + timedelta(minutes=2))
    _clue(session, game, bob, ClueKind.FIRST_LETTER, 25, created_at=T0, shared_at=shared)

    detail = history.game_detail(session, game.id)

    assert detail is not None
    assert [(c.player_id, c.kind, c.price) for c in detail.clues] == [
        (BOB, ClueKind.FIRST_LETTER, 25),
        (CAROL, ClueKind.TILE, 40),
    ]
    assert [c.shared_at is not None for c in detail.clues] == [True, False]


def test_detail_sums_bounty_per_contributor_without_refunded_ones(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.ACTIVE, ended=None)
    bob, carol = _funded(session, BOB), _funded(session, CAROL)
    first = bounty.contribute(session, game, bob, 30)
    bounty.contribute(session, game, carol, 50)
    bounty.contribute(session, game, bob, 40)
    wallet.transfer(
        session,
        wallet.Party.pot(),
        wallet.Party.of(bob),
        30,
        wallet.LedgerEntry(CurrencyReason.REFUND, game_id=game.id, reverses_id=first.id),
    )
    game.status = GameStatus.WON
    game.winner_id = ALICE
    session.flush()

    detail = history.game_detail(session, game.id)

    assert detail is not None
    assert detail.bounty == [(CAROL, 50), (BOB, 40)]
    assert [c.amount for c in bounty.contributions(session, game.id)] == [50, 40]


def test_detail_has_the_stage_timeline_with_readable_reasons(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.UNSOLVED, ended=T0)
    for step, reason in enumerate(("wrong-guess limit reached", "sharpened", "no guesses for 6h")):
        events.emit(
            session,
            EventType.STAGE_ADVANCED,
            events.Involved(game_id=game.id),
            from_stage=step + 1,
            to_stage=step + 2,
            reason=reason,
        )

    detail = history.game_detail(session, game.id)

    assert detail is not None
    assert [(s.from_stage, s.to_stage, s.reason) for s in detail.stages] == [
        (1, 2, history.StageReason.EXHAUSTED),
        (2, 3, history.StageReason.SHARPENED),
        (3, 4, history.StageReason.INACTIVITY),
    ]


def test_stage_reasons_map_to_reasons_players_can_read() -> None:
    reason = history.stage_reason
    assert reason("wrong-guess limit reached") is history.StageReason.EXHAUSTED
    assert reason("sharpened") is history.StageReason.SHARPENED
    assert reason("no guesses for 6h") is history.StageReason.INACTIVITY
    assert reason("an admin skipped it") is history.StageReason.OTHER


def test_detail_has_how_many_players_guessed_for_won_and_unsolved_games(
    session: Session,
) -> None:
    _players(session)
    won = _game(session, GameStatus.WON, ended=T0, winner_id=ALICE)
    lost = _game(session, GameStatus.UNSOLVED, ended=T0)
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=ALICE, subject_id=HOST, game_id=won.id),
        stage=1,
        hard_mode=False,
        how="guess",
        pot=0,
        seconds=60,
        distinct_guessers=3,
        ended_at=T0.isoformat(),
    )
    events.emit(
        session,
        EventType.GAME_UNSOLVED,
        events.Involved(subject_id=HOST, game_id=lost.id),
        cause="2-day timeout",
        hard_mode=False,
        distinct_guessers=2,
    )

    won_detail, lost_detail = (
        history.game_detail(session, won.id),
        history.game_detail(session, lost.id),
    )

    assert won_detail is not None
    assert lost_detail is not None
    assert won_detail.distinct_guessers == 3
    assert lost_detail.distinct_guessers == 2


def test_detail_has_the_hard_mode_votes(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.WON, ended=T0, winner_id=ALICE, hard_mode=True)
    session.add_all(
        [
            GameVote(game_id=game.id, voter_id=BOB, candidate_id=ALICE),
            GameVote(game_id=game.id, voter_id=CAROL, candidate_id=ALICE),
        ]
    )
    session.flush()

    detail = history.game_detail(session, game.id)

    assert detail is not None
    assert detail.votes == [(BOB, ALICE), (CAROL, ALICE)]


def test_a_game_with_no_extras_has_empty_record_parts(session: Session) -> None:
    _players(session)
    game = _game(session, GameStatus.WON, ended=None, winner_id=ALICE)

    detail = history.game_detail(session, game.id)

    assert detail is not None
    assert (detail.clues, detail.bounty, detail.stages, detail.votes) == ([], [], [], [])
    assert detail.distinct_guessers is None
