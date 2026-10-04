import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import CurrencyTransfer, Player
from nani_pix_bot.models.enums import CurrencyReason, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.economy import config, sharpen
from nani_pix_bot.services.economy.config import EconomyKey

STARTER, ALICE = 1, 2


def _setup(
    session: Session,
    *,
    stage: PixelStage = PixelStage.STAGE_1,
    hard_mode: bool = False,
    currency: int = 100,
    status: GameStatus = GameStatus.ACTIVE,
) -> tuple[Game, Player, Player]:
    starter = Player(telegram_user_id=STARTER, currency=100)
    alice = Player(telegram_user_id=ALICE, currency=currency)
    session.add_all([starter, alice])
    session.flush()
    game = Game(starter_id=STARTER, status=status, current_stage=stage, hard_mode=hard_mode)
    session.add(game)
    session.flush()
    return game, starter, alice


def test_check_passes_for_a_normal_game(session: Session) -> None:
    game, _, alice = _setup(session)
    assert sharpen.check(game, alice) is None


def test_check_refuses_no_game(session: Session) -> None:
    _, _, alice = _setup(session)
    assert sharpen.check(None, alice) is sharpen.SharpenRefusal.NO_GAME


def test_check_refuses_a_game_that_is_not_active(session: Session) -> None:
    game, _, alice = _setup(session, status=GameStatus.SETUP)
    assert sharpen.check(game, alice) is sharpen.SharpenRefusal.NO_GAME


def test_check_refuses_hard_mode(session: Session) -> None:
    game, _, alice = _setup(session, hard_mode=True)
    assert sharpen.check(game, alice) is sharpen.SharpenRefusal.HARD_MODE


def test_check_refuses_the_last_stage(session: Session) -> None:
    game, _, alice = _setup(session, stage=PixelStage.STAGE_5)
    assert sharpen.check(game, alice) is sharpen.SharpenRefusal.LAST_STAGE


def test_check_refuses_the_setter(session: Session) -> None:
    game, starter, _ = _setup(session)
    assert sharpen.check(game, starter) is sharpen.SharpenRefusal.SETTER


def test_sharpen_charges_and_advances_one_stage(session: Session) -> None:
    game, _, alice = _setup(session)

    paid = sharpen.sharpen(session, game, alice, PixelStage.STAGE_1)
    session.flush()

    assert paid == 50
    assert alice.currency == 50
    assert game.current_stage is PixelStage.STAGE_2
    row = session.query(CurrencyTransfer).one()
    assert (row.reason, row.amount, row.game_id) == (CurrencyReason.SHARPEN, 50, game.id)


def test_stale_sharpen_confirm_is_refused(session: Session) -> None:
    game, _, alice = _setup(session, stage=PixelStage.STAGE_2)

    with pytest.raises(sharpen.SharpenRefusedError) as refused:
        sharpen.sharpen(session, game, alice, PixelStage.STAGE_1)

    assert refused.value.refusal is sharpen.SharpenRefusal.STALE
    assert alice.currency == 100
    assert game.current_stage is PixelStage.STAGE_2
    assert session.query(CurrencyTransfer).count() == 0


def test_insufficient_funds_move_nothing(session: Session) -> None:
    game, _, alice = _setup(session, currency=49)

    with pytest.raises(sharpen.SharpenRefusedError) as refused:
        sharpen.sharpen(session, game, alice, PixelStage.STAGE_1)

    assert refused.value.refusal is sharpen.SharpenRefusal.INSUFFICIENT
    assert alice.currency == 49
    assert game.current_stage is PixelStage.STAGE_1
    assert session.query(CurrencyTransfer).count() == 0


def test_rechecks_the_rules_before_charging(session: Session) -> None:
    game, _, alice = _setup(session, hard_mode=True)

    with pytest.raises(sharpen.SharpenRefusedError) as refused:
        sharpen.sharpen(session, game, alice, PixelStage.STAGE_1)

    assert refused.value.refusal is sharpen.SharpenRefusal.HARD_MODE
    assert alice.currency == 100


def test_price_comes_from_config(session: Session) -> None:
    game, _, alice = _setup(session)
    config.set_amount(session, EconomyKey.SHARPEN, 30)

    assert sharpen.price(session) == 30
    assert sharpen.sharpen(session, game, alice, PixelStage.STAGE_1) == 30
    assert alice.currency == 70
