from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementClaim, AchievementGrant
from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import CurrencyReason, EventType, Rarity
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import events
from nani_pix_bot.services.achievements import engine
from nani_pix_bot.services.economy import wallet

pytestmark = pytest.mark.achievements

ME, OTHER, HOST = 1, 2, 9


def _players(session: Session) -> None:
    session.add_all([Player(telegram_user_id=u, currency=0) for u in (ME, OTHER, HOST)])
    session.flush()


def _win(session: Session, winner: int, game: int, **data: Any) -> None:
    facts = {
        "stage": 3,
        "hard_mode": False,
        "how": "guess",
        "pot": 0,
        "seconds": 900.0,
        "last_slot": False,
        "distinct_guessers": 2,
        "winner_wrong": 0,
        "first_guess": False,
        "ended_at": "2026-10-07T12:00:00+00:00",
    } | data
    involved = events.Involved(actor_id=winner, subject_id=HOST, game_id=game)
    events.emit(session, EventType.GAME_WON, involved, **facts)


def _player(session: Session, player_id: int) -> Player:
    player = session.get(Player, player_id)
    assert player is not None
    return player


def _grants(session: Session, player_id: int = ME) -> dict[str, list[int]]:
    rows = session.scalars(select(AchievementGrant).where(AchievementGrant.player_id == player_id))
    held: dict[str, list[int]] = {}
    for row in rows:
        held.setdefault(row.key, []).append(row.tier)
    return held


def test_a_first_win_grants_sharpshooter_one_and_pioneer(session: Session) -> None:
    _players(session)

    _win(session, ME, 1)

    held = _grants(session)
    assert held["sharpshooter"] == [1]
    assert held["pioneer"] == [1]
    claim = session.get(AchievementClaim, ("pioneer", 1))
    assert claim is not None
    assert claim.player_id == ME


def test_the_host_of_a_solved_game_gets_storyteller(session: Session) -> None:
    _players(session)

    _win(session, ME, 1)

    assert _grants(session, HOST)["storyteller"] == [1]


def test_pioneer_has_one_holder_ever(session: Session) -> None:
    _players(session)
    _win(session, ME, 1)

    _win(session, OTHER, 2)

    assert "pioneer" not in _grants(session, OTHER)


def test_a_refinish_of_the_same_game_grants_nothing_twice(session: Session) -> None:
    _players(session)
    _win(session, ME, 1)

    _win(session, ME, 1, how="setwinner")

    rows = session.scalars(select(AchievementGrant).where(AchievementGrant.key == "sharpshooter"))
    assert len(rows.all()) == 1


def test_rewards_are_paid_as_achievement_income_and_cascade_once(session: Session) -> None:
    _players(session)

    _win(session, ME, 1)

    # Sharpshooter I (Bronze 25) + Pioneer (Platinum 500) = 525 received,
    # which crosses Pixel Magnate I (Bronze 25): 550, and no further tier.
    assert _player(session, ME).currency == 550
    assert _grants(session)["pixel_magnate"] == [1]
    reasons = session.scalars(
        select(CurrencyTransfer.reason).where(CurrencyTransfer.to_player_id == ME)
    ).all()
    assert reasons == [CurrencyReason.ACHIEVEMENT] * 3
    pioneer = session.scalars(select(AchievementGrant).where(AchievementGrant.key == "pioneer"))
    row = pioneer.one()
    assert (row.rarity, row.reward, row.points) == (Rarity.PLATINUM, 500, 20)
    assert row.transfer_id is not None


def test_a_reversal_never_triggers_evaluation(session: Session) -> None:
    _players(session)

    wallet.credit(session, _player(session, ME), 600, wallet.LedgerEntry(CurrencyReason.REFUND))

    assert "pixel_magnate" not in _grants(session)


def test_held_tiers_lists_what_a_player_has(session: Session) -> None:
    _players(session)
    _win(session, ME, 1)

    assert engine.held_tiers(session, ME, "sharpshooter") == {1}


def test_milestone_goes_to_the_winner_of_exactly_the_nth_game(session: Session) -> None:
    _players(session)
    for game in range(1, 100):
        _win(session, OTHER, game)

    _win(session, ME, 100)

    assert _grants(session)["milestone_keeper"] == [1]
    assert "milestone_keeper" not in _grants(session, OTHER)


def test_a_zero_reward_is_granted_without_a_transfer(session: Session) -> None:
    _players(session)
    from nani_pix_bot.services.economy import config

    config.set_amount(session, config.EconomyKey.ACHIEVEMENT_BRONZE, 0)

    row = engine.grant(session, engine.GrantRequest(ME, "kingmaker"))

    assert (row.reward, row.transfer_id) == (0, None)


def _moved(session: Session, reason: str, *, reversal: bool) -> events.LoggedEvent:
    involved = events.Involved(actor_id=ME, subject_id=OTHER)
    return events.emit(
        session, EventType.CURRENCY_MOVED, involved, amount=1, reason=reason, reversal=reversal
    )


@pytest.mark.parametrize("reason", ["refund", "cashback", "tip"])
def test_on_event_ignores_every_kind_of_reversal(
    session: Session, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    _players(session)
    monkeypatch.setattr(events, "_dispatch", lambda _session, _event: None)

    reversal = _moved(session, reason, reversal=True)

    # Penny Pincher counts any 1 💠 tip the actor sent, reversal or not, so only
    # the reversal guard keeps this empty.
    assert engine.on_event(session, reversal) == []
    assert engine.on_event(session, _moved(session, "tip", reversal=False)) != []


def test_a_reward_cascade_crossing_two_tiers_grants_each_once(session: Session) -> None:
    from nani_pix_bot.services.economy import config

    _players(session)
    config.set_amount(session, config.EconomyKey.ACHIEVEMENT_BRONZE, 600)
    wallet.credit(session, _player(session, ME), 499, wallet.LedgerEntry(CurrencyReason.GRANT))

    _win(session, ME, 1)

    tiers = _grants(session)["pixel_magnate"]
    assert len(tiers) == len(set(tiers))
    assert {1, 2} <= set(tiers)
