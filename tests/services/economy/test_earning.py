from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from nani_pix_bot.models import CurrencyTransfer, Player
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.economy import bounty, config, earning
from nani_pix_bot.services.economy.config import EconomyKey

STARTER, ALICE, BOB = 1, 2, 3


def _setup(session: Session, **game_overrides) -> Game:
    session.add_all([Player(telegram_user_id=u) for u in (STARTER, ALICE, BOB)])
    session.flush()
    fields = {
        "starter_id": STARTER,
        "status": GameStatus.ACTIVE,
        "current_stage": PixelStage.STAGE_1,
    }
    fields.update(game_overrides)
    game = Game(**fields)
    session.add(game)
    session.flush()
    return game


def _currency(session: Session, user_id: int) -> int:
    player = session.get(Player, user_id)
    assert player is not None
    return player.currency


def _guess(session: Session, game: Game, guesser_id: int, *, won: bool = False):
    """What /guess does: record_guess counts the guess, then award_guess pays."""
    game.total_guess_count += 1
    return earning.award_guess(session, game, guesser_id=guesser_id, won=won)


def test_first_wrong_guess_pays_first_guess_bonus_plus_wrong_guess(session: Session) -> None:
    game = _setup(session)

    earned = _guess(session, game, ALICE)

    assert earned.guess == 7
    assert _currency(session, ALICE) == 7


def test_first_guess_bonus_is_paid_once_per_game(session: Session) -> None:
    game = _setup(session)
    _guess(session, game, ALICE)

    earned = _guess(session, game, BOB)

    assert earned.guess == 2


def test_first_guess_bonus_follows_the_guess_count_not_the_ledger(session: Session) -> None:
    game = _setup(session)
    config.set_amount(session, EconomyKey.FIRST_GUESS, 0)
    _guess(session, game, ALICE)  # the real first guess, bonus disabled
    config.set_amount(session, EconomyKey.FIRST_GUESS, 5)

    earned = _guess(session, game, BOB)  # second guess: no first-guess bonus

    assert earned.guess == 2


def test_wrong_guess_earnings_stop_at_cap(session: Session) -> None:
    game = _setup(session)
    for _ in range(8):
        _guess(session, game, ALICE)

    # 5 first-guess + 10 capped wrong-guess
    assert _currency(session, ALICE) == 15


def test_award_guess_skips_zero_amounts(session: Session) -> None:
    game = _setup(session)
    config.set_amount(session, EconomyKey.FIRST_GUESS, 0)
    config.set_amount(session, EconomyKey.WRONG_GUESS, 0)

    earned = _guess(session, game, ALICE)

    assert earned.player_total == 0
    assert session.query(CurrencyTransfer).count() == 0


def test_winning_first_guess_at_stage_one(session: Session) -> None:
    game = _setup(session)
    game.status = GameStatus.WON
    game.winner_id = ALICE

    earned = _guess(session, game, ALICE, won=True)

    assert (earned.guess, earned.win, earned.setter) == (5, 40, 0)
    assert _currency(session, ALICE) == 45
    assert _currency(session, STARTER) == 0


def test_award_win_pays_setter_at_stage_two_to_four(session: Session) -> None:
    game = _setup(session, current_stage=PixelStage.STAGE_3)

    earned = earning.award_win(session, game, winner_id=ALICE)

    assert (earned.win, earned.setter) == (25, 15)
    assert _currency(session, STARTER) == 15


def test_award_win_alone_never_pays_first_guess(session: Session) -> None:
    game = _setup(session, current_stage=PixelStage.STAGE_2)
    _guess(session, game, BOB)  # BOB got first-guess

    earned = earning.award_win(session, game, winner_id=ALICE)  # /correct path

    assert earned.guess == 0
    assert _currency(session, ALICE) == 30


def test_award_win_hard_mode_turn_two(session: Session) -> None:
    game = _setup(session, current_stage=None, hard_mode=True, hard_mode_turn=2)

    earned = earning.award_win(session, game, winner_id=ALICE)

    assert (earned.win, earned.setter) == (60, 0)
    assert _currency(session, STARTER) == 0


def test_game_created_from_an_open_turn_earns_no_prompt_bonus(session: Session) -> None:
    game = _setup(session, turn_received_at=None, created_at=datetime.now(UTC))

    assert earning.award_prompt_start(session, game) == 0
    assert _currency(session, STARTER) == 0


def test_prompt_start_pays_starter_within_an_hour(session: Session) -> None:
    received = datetime.now(UTC) - timedelta(minutes=10)
    game = _setup(session, turn_received_at=received, created_at=datetime.now(UTC))

    assert earning.award_prompt_start(session, game) == 10
    assert _currency(session, STARTER) == 10


def test_prompt_start_pays_nothing_when_late_or_unknown_or_hard_mode(session: Session) -> None:
    now = datetime.now(UTC)
    late = _setup(session, turn_received_at=now - timedelta(hours=2), created_at=now)
    assert earning.award_prompt_start(session, late) == 0

    unknown = Game(starter_id=STARTER, status=GameStatus.ACTIVE, turn_received_at=None)
    session.add(unknown)
    session.flush()
    assert earning.award_prompt_start(session, unknown) == 0

    hard = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        hard_mode=True,
        turn_received_at=now,
        created_at=now,
    )
    session.add(hard)
    session.flush()
    assert earning.award_prompt_start(session, hard) == 0


def test_award_win_pays_the_bounty(session: Session) -> None:
    game = _setup(session)
    for user_id, amount in ((STARTER, 30), (BOB, 40)):
        player = session.get(Player, user_id)
        assert player is not None
        player.currency = 100
        bounty.contribute(session, game, player, amount)

    earned = earning.award_win(session, game, winner_id=ALICE)

    assert earned.bounty == 70
    assert earned.player_total == earned.win
    assert bounty.pot_balance(session, game.id) == 0
    assert _currency(session, ALICE) == earned.win + 70
