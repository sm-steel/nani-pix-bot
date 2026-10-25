import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from nani_pix_bot.models.enums import (
    CurrencyReason,
    EventType,
    GameStatus,
    HexOutcome,
    PixelStage,
    SeasonStatus,
)
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.economy.wallet import LedgerEntry
from nani_pix_bot.services.seasons import hexes
from nani_pix_bot.services.seasons.hexes import HexRefusal, HexRefusedError

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def _naive(at: datetime) -> datetime:
    return at.replace(tzinfo=None)


def _rule(kind: HexKind):
    run = registry.get("demo_3")
    assert run is not None
    rule = hexes.rule_for(run, kind)
    assert rule is not None
    return rule


def _game(session, season_id, starter, *, activated: bool = True) -> Game:
    game = Game(
        starter_id=starter,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_english="Clannad",
        season_id=season_id,
        activated_at=_naive(NOW) if activated else None,
    )
    session.add(game)
    session.flush()
    return game


@pytest.fixture
def hex_season(session) -> SeasonSchedule:
    season = SeasonSchedule(
        run_id="demo_3",
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    session.add_all([Player(telegram_user_id=u) for u in (1, 2, 3, 4)])
    session.flush()
    game = _game(session, season.id, 4)
    session.add_all([GameGuess(game_id=game.id, player_id=u, text="x", stage=1) for u in (1, 2)])
    session.flush()
    for user in (1, 2):
        entry = LedgerEntry(CurrencyReason.SEASON_WIN, season_id=season.id, coins=True)
        wallet.credit(session, session.get(Player, user), 200, entry)
    return season


def _p(session, user: int) -> Player:
    player = session.get(Player, user)
    assert player is not None
    return player


def _cast(session, caster: int, target: int, kind: HexKind, at: datetime = NOW):
    return hexes.cast(session, _p(session, caster), _p(session, target), kind, at)


def _events(session, event_type):
    stmt = select(EventLog).where(EventLog.event_type == event_type).order_by(EventLog.id)
    return list(session.scalars(stmt))


def test_cast_charges_the_price_and_writes_an_active_hex(session, hex_season):
    rule = _rule(HexKind.NOPPERABO)
    result = _cast(session, 1, 2, HexKind.NOPPERABO)
    assert not result.blocked
    assert result.price == rule.price
    assert wallet.balance(session, 1, season_id=hex_season.id) == 200 - result.price
    expected = _naive(NOW + timedelta(hours=rule.duration_hours))
    assert result.hex.expires_at == expected
    active = hexes.active_hex_for(session, 2, NOW)
    assert active is not None
    assert active.id == result.hex.id
    event = _events(session, EventType.HEX_CAST)[0]
    assert event.actor_id == 1
    assert event.subject_id == 2
    assert event.data == {
        "season_id": hex_season.id,
        "kind": "nopperabo",
        "hex_id": result.hex.id,
        "outcome": None,
        "self_prank": False,
    }


@pytest.mark.parametrize("kind", [HexKind.KAPPA, HexKind.KITSUNE])
def test_kappa_and_kitsune_have_no_expiry(session, hex_season, kind):
    assert _cast(session, 1, 2, kind).hex.expires_at is None


@pytest.mark.parametrize(
    ("caster", "target", "refusal"),
    [
        (1, 1, HexRefusal.SELF),
        (3, 2, HexRefusal.CASTER_NOT_PLAYED),
        (1, 3, HexRefusal.TARGET_NOT_PLAYED),
    ],
)
def test_who_may_cast_on_whom(session, hex_season, caster, target, refusal):
    with pytest.raises(HexRefusedError) as error:
        _cast(session, caster, target, HexKind.KAPPA)
    assert error.value.refusal is refusal


def test_a_host_of_an_activated_game_counts_as_played(session, hex_season):
    assert hexes.played_in_season(session, hex_season.id, 4)


def test_a_setup_only_game_does_not_count_as_played(session, hex_season):
    _game(session, hex_season.id, 3, activated=False)
    assert not hexes.played_in_season(session, hex_season.id, 3)


def test_a_guess_in_another_season_does_not_count(session, hex_season):
    other = _game(session, None, 3)
    session.add(GameGuess(game_id=other.id, player_id=3, text="x", stage=1))
    session.flush()
    assert not hexes.played_in_season(session, hex_season.id, 3)


def test_opted_out_target_and_caster_are_refused(session, hex_season):
    _p(session, 2).hex_opt_out = True
    with pytest.raises(HexRefusedError) as error:
        _cast(session, 1, 2, HexKind.KAPPA)
    assert error.value.refusal is HexRefusal.TARGET_OPTED_OUT
    _p(session, 2).hex_opt_out = False
    _p(session, 1).hex_opt_out = True
    with pytest.raises(HexRefusedError) as error:
        _cast(session, 1, 2, HexKind.KAPPA)
    assert error.value.refusal is HexRefusal.CASTER_OPTED_OUT


def test_one_active_hex_per_target(session, hex_season):
    _cast(session, 1, 2, HexKind.KAPPA)
    with pytest.raises(HexRefusedError) as error:
        _cast(session, 4, 2, HexKind.NOPPERABO)
    assert error.value.refusal is HexRefusal.ALREADY_HEXED


def test_cooldown_is_per_kind(session, hex_season):
    cooldown = _rule(HexKind.NOPPERABO).cooldown_minutes
    _cast(session, 1, 2, HexKind.NOPPERABO)
    with pytest.raises(HexRefusedError) as error:
        _cast(session, 1, 4, HexKind.NOPPERABO, NOW + timedelta(minutes=1))
    assert error.value.refusal is HexRefusal.COOLDOWN
    assert error.value.wait == timedelta(minutes=cooldown - 1)
    _cast(session, 1, 4, HexKind.KAPPA, NOW + timedelta(minutes=1))


def test_cooldown_ends(session, hex_season):
    cooldown = _rule(HexKind.NOPPERABO).cooldown_minutes
    _cast(session, 1, 2, HexKind.NOPPERABO)
    _cast(session, 1, 4, HexKind.NOPPERABO, NOW + timedelta(minutes=cooldown))


def test_insufficient_coins_charges_nothing(session, hex_season):
    _p(session, 4)
    with pytest.raises(HexRefusedError) as error:
        _cast(session, 4, 2, HexKind.KAPPA)
    assert error.value.refusal is HexRefusal.INSUFFICIENT
    assert session.scalar(select(func.count()).select_from(Hex)) == 0
    assert wallet.balance(session, 4, season_id=hex_season.id) == 0


def test_closed_without_an_active_season_or_with_no_hexes_in_the_run(session, hex_season):
    hex_season.run_id = "demo_1"
    session.flush()
    with pytest.raises(HexRefusedError) as error:
        _cast(session, 1, 2, HexKind.KAPPA)
    assert error.value.refusal is HexRefusal.CLOSED
    hex_season.run_id = "demo_3"
    hex_season.status = SeasonStatus.ENDED
    session.flush()
    with pytest.raises(HexRefusedError) as error:
        _cast(session, 1, 2, HexKind.KAPPA)
    assert error.value.refusal is HexRefusal.CLOSED


def test_unknown_kind_for_the_run(session, hex_season, monkeypatch):
    run = registry.get("demo_3")
    assert run is not None
    trimmed = dataclasses.replace(
        run, hexes=tuple(r for r in run.hexes if r.kind is not HexKind.KAPPA)
    )
    monkeypatch.setattr(registry, "get", lambda _run_id: trimmed)
    with pytest.raises(HexRefusedError) as error:
        _cast(session, 1, 2, HexKind.KAPPA)
    assert error.value.refusal is HexRefusal.UNKNOWN_KIND


def test_an_expired_prank_frees_the_target_and_is_stamped(session, hex_season):
    first = _cast(session, 1, 2, HexKind.CHOCHIN).hex
    later = NOW + timedelta(hours=_rule(HexKind.CHOCHIN).duration_hours, minutes=1)
    assert hexes.active_hex_for(session, 2, later) is None
    _cast(session, 1, 2, HexKind.NOPPERABO, later)
    assert first.outcome == HexOutcome.EXPIRED
    assert first.resolved_at is not None


def test_an_ended_season_has_no_active_hex(session, hex_season):
    _cast(session, 1, 2, HexKind.KAPPA)
    hex_season.status = SeasonStatus.ENDED
    session.flush()
    assert hexes.active_hex_for(session, 2, NOW) is None


def test_opt_out_is_locked_after_casting(session, hex_season):
    _cast(session, 1, 2, HexKind.KAPPA)
    with pytest.raises(HexRefusedError) as error:
        hexes.set_opt_out(session, _p(session, 1), True, NOW + timedelta(hours=1))
    assert error.value.refusal is HexRefusal.OPT_OUT_LOCKED
    assert error.value.wait == timedelta(hours=23)
    hexes.set_opt_out(session, _p(session, 1), True, NOW + hexes.OPT_OUT_LOCK)
    assert _p(session, 1).hex_opt_out


def test_opting_out_keeps_the_hex_already_on_you(session, hex_season):
    _cast(session, 1, 2, HexKind.KAPPA)
    hexes.set_opt_out(session, _p(session, 2), True, NOW)
    assert hexes.active_hex_for(session, 2, NOW) is not None


def test_opting_back_in_is_never_locked(session, hex_season):
    _p(session, 1).hex_opt_out = True
    _cast_row = Hex(
        season_id=hex_season.id,
        kind="kappa",
        caster_id=1,
        target_id=2,
        cast_at=_naive(NOW),
    )
    session.add(_cast_row)
    session.flush()
    hexes.set_opt_out(session, _p(session, 1), False, NOW)
    assert not _p(session, 1).hex_opt_out


def test_cast_and_refusal_are_logged(session, hex_season, log_records):
    result = _cast(session, 1, 2, HexKind.KAPPA)
    cast_line = next(r for r in log_records if r.level == "INFO" and r.message.startswith("cast "))
    assert cast_line.extra["target_id"] == 2
    assert cast_line.extra["hex_id"] == result.hex.id
    assert cast_line.extra["season_id"] == hex_season.id
    with pytest.raises(HexRefusedError):
        _cast(session, 1, 1, HexKind.KAPPA)
    refused = [
        r for r in log_records if r.level == "WARNING" and r.message.startswith("hex refused")
    ]
    assert refused
    assert refused[0].extra["refusal"] == "self"
