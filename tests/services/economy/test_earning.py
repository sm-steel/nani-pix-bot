from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from nani_pix_bot.models import CluePurchase, CurrencyTransfer, Player
from nani_pix_bot.models.enums import ClueKind, CurrencyReason, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.economy import bounty, config, earning, wallet
from nani_pix_bot.services.economy.config import EconomyKey
from tests.conftest import LogLine
from tests.services.economy.ledger import ledger_balance

STARTER, ALICE, BOB = 1, 2, 3


def _info(log_records: list[LogLine], message: str) -> LogLine:
    """The one INFO line with exactly this message."""
    (line,) = [r for r in log_records if r.level == "INFO" and r.message == message]
    return line


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


def test_wrong_guess_payout_is_logged_at_info(session: Session, log_records) -> None:
    game = _setup(session)
    alice = session.get(Player, ALICE)
    assert alice is not None
    alice.username = "alice"
    _guess(session, game, ALICE)

    _guess(session, game, ALICE)

    line = _info(log_records, "guess pays 2 💠 (first-guess bonus 0 💠, wrong-guess reward 2 💠)")
    assert (line.extra["game_id"], line.extra["amount"], line.extra["wrong"]) == (game.id, 2, 2)


def test_first_guess_payout_names_both_parts(session: Session, log_records) -> None:
    game = _setup(session)

    _guess(session, game, ALICE)

    line = _info(log_records, "guess pays 7 💠 (first-guess bonus 5 💠, wrong-guess reward 2 💠)")
    assert (line.extra["first"], line.extra["wrong"]) == (5, 2)
    assert line.extra["game_id"] == game.id


def test_win_payout_names_winner_and_setter(session: Session, log_records) -> None:
    game = _setup(session)

    earned = earning.award_win(session, game, winner_id=ALICE)

    (line,) = [r for r in log_records if r.level == "INFO" and r.message.startswith("win pays")]
    assert (line.extra["winner_id"], line.extra["setter_id"]) == (ALICE, STARTER)
    assert (line.extra["win"], line.extra["setter_pay"]) == (earned.win, earned.setter)
    assert line.extra["game_id"] == game.id
    assert "user_id" not in line.extra


def _hard_mode_game_with_purchases(
    session: Session, spends: list[int], *, hard_mode: bool = True
) -> tuple[Game, Player]:
    """A game where ALICE was charged each amount as a clue purchase."""
    game = _setup(session, hard_mode=hard_mode, hard_mode_turn=1 if hard_mode else None)
    buyer = session.get(Player, ALICE)
    assert buyer is not None
    buyer.currency = sum(spends) + 100
    session.add(
        CurrencyTransfer(
            from_type="house",
            to_type="player",
            to_player_id=ALICE,
            amount=sum(spends) + 100,
            reason=CurrencyReason.GRANT,
        )
    )
    session.flush()
    for amount in spends:
        wallet.debit(
            session,
            buyer,
            amount,
            wallet.LedgerEntry(CurrencyReason.CLUE_PURCHASE, game_id=game.id),
        )
    return game, buyer


def test_cashback_pays_half_of_net_clue_spend_once(session: Session) -> None:
    game, buyer = _hard_mode_game_with_purchases(session, spends=[150, 225])
    before = buyer.currency

    assert earning.award_cashback(session, game) == 187  # (150 + 225) * 50 // 100
    assert earning.award_cashback(session, game) == 0  # never twice

    assert buyer.currency == before + 187
    assert ledger_balance(session, ALICE) == buyer.currency


def test_cashback_skips_refunded_purchases(session: Session) -> None:
    game, buyer = _hard_mode_game_with_purchases(session, spends=[150, 225])
    charges = list(
        session.query(CurrencyTransfer)
        .filter_by(reason=CurrencyReason.CLUE_PURCHASE)
        .order_by(CurrencyTransfer.id)
    )
    purchases = [
        CluePurchase(
            game_id=game.id, player_id=ALICE, kind=ClueKind.FIRST_LETTER, transfer_id=charge.id
        )
        for charge in charges
    ]
    session.add_all(purchases)
    session.flush()

    shop.refund(session, purchases[0])

    second = session.get(CurrencyTransfer, purchases[1].transfer_id)
    assert second is not None
    assert earning.clue_spend(session, game.id) == {ALICE: second.amount}
    assert ledger_balance(session, ALICE) == buyer.currency


def test_no_cashback_for_normal_games(session: Session) -> None:
    game, buyer = _hard_mode_game_with_purchases(session, spends=[100], hard_mode=False)
    before = buyer.currency

    assert earning.award_cashback(session, game) == 0
    assert buyer.currency == before


def test_cashback_follows_the_configured_percent(session: Session) -> None:
    game, _ = _hard_mode_game_with_purchases(session, spends=[100])
    config.set_amount(session, EconomyKey.CLUE_CASHBACK_PERCENT, 0)

    assert earning.award_cashback(session, game) == 0


def test_award_compensation_pays_the_bonus(session: Session) -> None:
    game = _setup(session)

    assert earning.award_compensation(session, game, winner_id=BOB) == 30
    assert _currency(session, BOB) == 30
    assert ledger_balance(session, BOB) == _currency(session, BOB)
