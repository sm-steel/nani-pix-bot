from datetime import timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import (
    ConsumableKind,
    CurrencyParty,
    CurrencyReason,
    EventType,
    HexOutcome,
    OutboxKind,
    SeasonStatus,
)
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.mechanics import PRANK_HEXES, HexKind
from nani_pix_bot.services import events
from nani_pix_bot.services.cosmetics import consumables
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import hexes, lifecycle
from nani_pix_bot.services.seasons.hexes import HexRefusal, HexRefusedError
from tests.services.seasons.conftest import NOW, naive
from tests.services.seasons.test_hexes import _cast, _events, _p, _rule

HOST = 4
WIN_COINS = 25  # demo_3 coins.win


def _run():
    run = registry.get("demo_3")
    assert run is not None
    return run


def _game_id(session) -> int:
    return session.scalars(select(Game.id)).first()


def _win(session, winner: int, stage: int = 1) -> None:
    involved = events.Involved(actor_id=winner, subject_id=HOST, game_id=_game_id(session))
    events.emit(session, EventType.GAME_WON, involved, stage=stage, hard_mode=False)


def _guess(session, player: int, *, correct: bool) -> None:
    # Not the player's first guess, so no first-guess coins muddy the balance.
    session.add(GameGuess(game_id=_game_id(session), player_id=player, text="x", stage=1))
    session.flush()
    involved = events.Involved(actor_id=player, game_id=_game_id(session))
    events.emit(
        session,
        EventType.GUESS,
        involved,
        stage=1,
        hard_mode=False,
        correct=correct,
        first_of_game=False,
    )


def _only_hex(session) -> Hex:
    rows = list(session.scalars(select(Hex)))
    assert len(rows) == 1
    return rows[0]


def _outbox(session) -> list[tuple[str, dict]]:
    stmt = select(AnnouncementOutbox).where(AnnouncementOutbox.kind == OutboxKind.HEX_NOTICE)
    ordered = stmt.order_by(AnnouncementOutbox.id)
    return [(row.kind, row.payload) for row in session.scalars(ordered)]


def _transfers(session, reason: CurrencyReason) -> list[CurrencyTransfer]:
    stmt = select(CurrencyTransfer).where(CurrencyTransfer.reason == reason)
    return list(session.scalars(stmt))


def _drain(session, player: int, season_id: int, keep: int) -> None:
    left = wallet.balance(session, player, season_id=season_id) - keep
    entry = wallet.LedgerEntry(CurrencyReason.HEX_CAST, season_id=season_id, coins=True)
    wallet.debit(session, _p(session, player), left, entry)


def test_kappa_moves_its_share_of_the_win_coins_to_the_caster(session, hex_season):
    sid = hex_season.id
    _cast(session, 1, 2, HexKind.KAPPA)
    before = wallet.balance(session, 1, season_id=sid)
    _win(session, 2)
    share = WIN_COINS * _rule(HexKind.KAPPA).amount // 100
    assert wallet.balance(session, 1, season_id=sid) == before + share
    (moved,) = _transfers(session, CurrencyReason.HEX_KAPPA)
    assert (moved.amount, moved.from_player_id, moved.to_player_id) == (share, 2, 1)
    row = _only_hex(session)
    assert row.outcome == HexOutcome.TRIGGERED.value
    assert row.game_id == _game_id(session)
    assert row.resolved_at is not None
    event = _events(session, EventType.HEX_TRIGGERED)[0]
    assert (event.actor_id, event.subject_id) == (2, 1)
    assert event.data == {"season_id": sid, "kind": "kappa", "hex_id": row.id, "amount": share}
    assert _outbox(session) == [("hex_notice", {"hex_id": row.id, "notice": "triggered"})]


def test_kappa_waits_for_a_win_that_pays_coins(session, hex_season):
    _cast(session, 1, 2, HexKind.KAPPA)
    _win(session, 2, stage=0)
    assert _only_hex(session).resolved_at is None
    assert not _transfers(session, CurrencyReason.HEX_KAPPA)


def test_kappa_ignores_someone_elses_win(session, hex_season):
    _cast(session, 1, 2, HexKind.KAPPA)
    _win(session, 3)
    assert _only_hex(session).resolved_at is None


def test_kitsune_costs_the_next_wrong_guess(session, hex_season):
    _cast(session, 1, 2, HexKind.KITSUNE)
    amount = _rule(HexKind.KITSUNE).amount
    sid = hex_season.id
    target_before = wallet.balance(session, 2, season_id=sid)
    caster_before = wallet.balance(session, 1, season_id=sid)
    _guess(session, 2, correct=False)
    (moved,) = _transfers(session, CurrencyReason.HEX_KITSUNE)
    assert (moved.amount, moved.from_player_id) == (amount, 2)
    assert moved.to_type == CurrencyParty.HOUSE
    assert moved.to_player_id is None
    assert wallet.balance(session, 2, season_id=sid) == target_before - amount
    assert wallet.balance(session, 1, season_id=sid) == caster_before
    assert _only_hex(session).outcome == HexOutcome.TRIGGERED.value
    assert _events(session, EventType.HEX_TRIGGERED)[0].data["amount"] == amount
    assert [notice["notice"] for _, notice in _outbox(session)] == ["triggered"]


def test_kitsune_ignores_a_right_guess(session, hex_season):
    _cast(session, 1, 2, HexKind.KITSUNE)
    _guess(session, 2, correct=True)
    assert _only_hex(session).resolved_at is None


def test_kitsune_takes_only_what_the_target_has(session, hex_season):
    _cast(session, 1, 2, HexKind.KITSUNE)
    _drain(session, 2, hex_season.id, keep=3)
    _guess(session, 2, correct=False)
    assert [t.amount for t in _transfers(session, CurrencyReason.HEX_KITSUNE)] == [3]
    assert wallet.balance(session, 2, season_id=hex_season.id) == 0
    assert _events(session, EventType.HEX_TRIGGERED)[0].data["amount"] == 3


def test_kitsune_on_an_empty_wallet_resolves_and_moves_nothing(session, hex_season):
    _cast(session, 1, 2, HexKind.KITSUNE)
    _drain(session, 2, hex_season.id, keep=0)
    _guess(session, 2, correct=False)
    assert _only_hex(session).outcome == HexOutcome.TRIGGERED.value
    assert not _transfers(session, CurrencyReason.HEX_KITSUNE)


def test_triggers_only_fire_in_their_own_season(session, hex_season):
    old = SeasonSchedule(
        run_id="demo_3",
        start_at=NOW - timedelta(days=30),
        end_at=NOW - timedelta(days=20),
        status=SeasonStatus.ENDED,
        created_by=9,
    )
    session.add(old)
    session.flush()
    stale = Hex(
        season_id=old.id,
        kind="kitsune",
        caster_id=1,
        target_id=2,
        cast_at=naive(NOW - timedelta(days=25)),
    )
    session.add(stale)
    session.flush()
    _guess(session, 2, correct=False)
    assert not _transfers(session, CurrencyReason.HEX_KITSUNE)
    assert stale.resolved_at is None


def test_season_end_cancels_open_hexes(session, hex_season):
    _cast(session, 1, 2, HexKind.KAPPA)
    lifecycle._finalize(session, hex_season, NOW)
    row = _only_hex(session)
    assert row.outcome == HexOutcome.CANCELLED.value
    assert row.resolved_at is not None


def test_cancel_open_leaves_resolved_hexes_alone(session, hex_season):
    _cast(session, 1, 2, HexKind.KITSUNE)
    _guess(session, 2, correct=False)
    assert hexes.cancel_open(session, hex_season.id, NOW) == 0
    assert _only_hex(session).outcome == HexOutcome.TRIGGERED.value


def test_self_prank_puts_a_prank_on_yourself(session, hex_season):
    row = hexes.apply_self_prank(session, _run(), 2, NOW)
    assert HexKind(row.kind) in PRANK_HEXES
    assert row.caster_id == row.target_id == 2
    assert row.expires_at is not None
    assert row.cast_at == naive(NOW)
    assert _outbox(session)[-1][1] == {"hex_id": row.id, "notice": "self_prank"}
    event = _events(session, EventType.HEX_CAST)[-1]
    assert event.data["self_prank"] is True
    assert event.data["outcome"] is None
    assert wallet.balance(session, 2, season_id=hex_season.id) == 200


def test_self_prank_is_not_blocked_by_an_omamori(session, hex_season):
    consumables.add(session, 2, ConsumableKind.OMAMORI, why="test")
    row = hexes.apply_self_prank(session, _run(), 2, NOW)
    assert row.outcome is None
    assert consumables.count(session, 2, ConsumableKind.OMAMORI) == 1


def test_self_prank_refuses_an_opted_out_player(session, hex_season):
    _p(session, 2).hex_opt_out = True
    with pytest.raises(HexRefusedError) as error:
        hexes.apply_self_prank(session, _run(), 2, NOW)
    assert error.value.refusal is HexRefusal.TARGET_OPTED_OUT


def test_self_prank_on_an_already_hexed_player_returns_the_existing_hex(session, hex_season):
    first = _cast(session, 1, 2, HexKind.KAPPA).hex
    again = hexes.apply_self_prank(session, _run(), 2, NOW)
    assert again.id == first.id
    assert again.caster_id == 1
    assert len(list(session.scalars(select(Hex)))) == 1
    assert _outbox(session) == []


def test_self_prank_starts_no_cooldown(session, hex_season):
    hexes.apply_self_prank(session, _run(), 2, NOW)
    assert _cast(session, 2, 1, HexKind.NOPPERABO).hex.caster_id == 2


def test_triggers_are_logged(session, hex_season, log_records):
    _cast(session, 1, 2, HexKind.KITSUNE)
    _guess(session, 2, correct=False)
    row = _only_hex(session)
    lines = [r for r in log_records if r.level == "INFO" and "triggered" in r.message]
    assert len(lines) == 1
    extra = lines[0].extra
    assert extra["hex_id"] == row.id
    assert extra["caster_id"] == 1
    assert extra["target_id"] == 2
    assert extra["amount"] == _rule(HexKind.KITSUNE).amount


def test_a_player_without_a_row_is_skipped(session, hex_season):
    session.add(Player(telegram_user_id=7))
    session.flush()
    _guess(session, 7, correct=False)
    assert not list(session.scalars(select(Hex)))
