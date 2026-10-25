import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import (
    CurrencyReason,
    EventType,
    SeasonStatus,
)
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.halloween import Hex, OmikujiDraw
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.mechanics import OmikujiKind
from nani_pix_bot.services import settings
from nani_pix_bot.services.cosmetics import consumables
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import omikuji
from nani_pix_bot.services.seasons.omikuji import (
    OmikujiRefusal,
    OmikujiRefusedError,
    draw,
    local_day,
    pick,
)
from tests.services.seasons.conftest import NOW, naive

RUN3 = registry.discover("tests.seasons.fake_runs")["demo_3"]


def outcome_of(kind: OmikujiKind):
    return next(o for o in RUN3.omikuji if o.kind == kind)


class ForcedRng(random.Random):
    """A Random whose weighted pick always lands on one outcome kind."""

    def __init__(self, kind: OmikujiKind) -> None:
        super().__init__(0)
        self._kind = kind

    def choices(self, population, weights=None, *, cum_weights=None, k=1):
        return [outcome_of(self._kind)] * k


def rng_for(kind: OmikujiKind) -> random.Random:
    return ForcedRng(kind)


@pytest.fixture
def omikuji_season(session) -> SeasonSchedule:
    season = SeasonSchedule(
        run_id="demo_3",
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    session.add(Player(telegram_user_id=1))
    session.flush()
    return season


class FakeHexes:
    def __init__(self, session, season) -> None:
        self.calls: list[tuple] = []
        self.live: Hex | None = None
        self.hex = Hex(
            season_id=season.id,
            kind="nopperabo",
            caster_id=1,
            target_id=1,
            cast_at=naive(NOW),
            expires_at=naive(NOW) + timedelta(hours=1),
        )

    def apply(self, session, run, player_id, now):
        self.calls.append((session, run, player_id, now))
        return self.hex


@pytest.fixture
def hexes(session, omikuji_season, monkeypatch) -> FakeHexes:
    fake = FakeHexes(session, omikuji_season)
    monkeypatch.setattr(omikuji.hexes, "apply_self_prank", fake.apply)
    monkeypatch.setattr(omikuji.hexes, "active_hex_for", lambda *_a: fake.live)
    return fake


def player(session) -> Player:
    found = session.get(Player, 1)
    assert found is not None
    return found


def test_pick_uses_the_run_weights():
    rng, outcomes = random.SystemRandom(), RUN3.omikuji
    counts = Counter(pick(outcomes, rng).kind for _ in range(20_000))
    total = sum(o.weight for o in outcomes)
    for o in outcomes:
        assert abs(counts[o.kind] / 20_000 - o.weight / total) < 0.02


def test_a_paying_slip_credits_season_coins(session, omikuji_season):
    out = outcome_of(OmikujiKind.DAIKICHI)
    result = draw(session, player(session), NOW, rng_for(OmikujiKind.DAIKICHI))
    assert result.outcome == out
    assert result.run == RUN3
    assert result.prank is None
    assert not result.spared
    assert wallet.balance(session, 1, omikuji_season.id) == out.coins
    reasons = {t.reason for t in session.scalars(select(CurrencyTransfer))}
    assert reasons == {CurrencyReason.SEASON_OMIKUJI}


def test_a_charm_slip_adds_the_consumable(session, omikuji_season):
    out = outcome_of(OmikujiKind.KICHI)
    assert out.consumable is not None
    draw(session, player(session), NOW, rng_for(OmikujiKind.KICHI))
    assert consumables.count(session, 1, out.consumable) == 1
    assert wallet.balance(session, 1, omikuji_season.id) == out.coins


def test_second_draw_same_local_day_is_refused_and_pays_nothing(session, omikuji_season):
    out = outcome_of(OmikujiKind.SHOKICHI)
    draw(session, player(session), NOW, rng_for(OmikujiKind.SHOKICHI))
    with pytest.raises(OmikujiRefusedError) as err:
        draw(session, player(session), NOW + timedelta(hours=1), rng_for(OmikujiKind.DAIKICHI))
    assert err.value.refusal == OmikujiRefusal.ALREADY
    assert wallet.balance(session, 1, omikuji_season.id) == out.coins


def test_the_day_boundary_is_the_groups_local_midnight(session, omikuji_season):
    session.add(BotSettings(id=settings.SETTINGS_ID, quiet_timezone="Europe/Moscow"))
    session.flush()
    rng = rng_for(OmikujiKind.SHOKICHI)
    day = datetime(2026, 10, 30, tzinfo=UTC)
    draw(session, player(session), day.replace(hour=20, minute=50), rng)
    draw(session, player(session), day.replace(hour=21, minute=10), rng)
    with pytest.raises(OmikujiRefusedError) as err:
        draw(session, player(session), day.replace(hour=23), rng)
    assert err.value.refusal == OmikujiRefusal.ALREADY


def test_local_day_follows_the_zone():
    from zoneinfo import ZoneInfo

    at = datetime(2026, 10, 30, 21, 10, tzinfo=UTC)
    assert local_day(at, ZoneInfo("UTC")).isoformat() == "2026-10-30"
    assert local_day(at, ZoneInfo("Europe/Moscow")).isoformat() == "2026-10-31"


def test_a_race_on_the_unique_key_is_already_not_a_crash(session, omikuji_season, monkeypatch):
    session.add(
        OmikujiDraw(
            season_id=omikuji_season.id,
            player_id=1,
            local_date=NOW.date(),
            outcome=OmikujiKind.SHOKICHI.value,
            drawn_at=naive(NOW),
        )
    )
    session.flush()
    monkeypatch.setattr(omikuji, "drawn_today", lambda *_a: None)
    with pytest.raises(OmikujiRefusedError) as err:
        draw(session, player(session), NOW, rng_for(OmikujiKind.DAIKICHI))
    assert err.value.refusal == OmikujiRefusal.ALREADY
    assert wallet.balance(session, 1, omikuji_season.id) == 0


def test_closed_without_a_season_or_without_omikuji(session):
    session.add(Player(telegram_user_id=1))
    session.flush()
    with pytest.raises(OmikujiRefusedError) as err:
        draw(session, player(session), NOW)
    assert err.value.refusal == OmikujiRefusal.CLOSED

    session.add(
        SeasonSchedule(
            run_id="demo_1",
            start_at=NOW - timedelta(days=1),
            end_at=NOW + timedelta(days=9),
            status=SeasonStatus.ACTIVE,
            created_by=9,
        )
    )
    session.flush()
    demo_1 = registry.get("demo_1")
    assert demo_1 is not None
    assert demo_1.omikuji == ()
    with pytest.raises(OmikujiRefusedError) as err:
        draw(session, player(session), NOW)
    assert err.value.refusal == OmikujiRefusal.CLOSED


def test_kyo_pranks_through_apply_self_prank(session, omikuji_season, hexes):
    result = draw(session, player(session), NOW, rng_for(OmikujiKind.KYO))
    assert hexes.calls == [(session, RUN3, 1, NOW)]
    assert result.prank is hexes.hex
    assert not result.spared
    assert wallet.balance(session, 1, omikuji_season.id) == 0


def test_kyo_spares_opted_out_and_already_hexed_players(session, omikuji_season, hexes):
    me = player(session)
    me.hex_opt_out = True
    session.flush()
    result = draw(session, me, NOW, rng_for(OmikujiKind.KYO))
    assert result.spared
    assert result.prank is None
    assert hexes.calls == []

    me.hex_opt_out = False
    hexes.live = hexes.hex
    result = draw(session, me, NOW + timedelta(days=1), rng_for(OmikujiKind.KYO))
    assert result.spared
    assert result.prank is None
    assert hexes.calls == []


def test_a_non_kyo_slip_never_touches_hexes(session, omikuji_season, hexes):
    draw(session, player(session), NOW, rng_for(OmikujiKind.SHOKICHI))
    assert hexes.calls == []


def test_draw_emits_omikuji_drawn(session, omikuji_season, log_records):
    out = outcome_of(OmikujiKind.KICHI)
    draw(session, player(session), NOW, rng_for(OmikujiKind.KICHI))
    event = session.scalars(
        select(EventLog).where(EventLog.event_type == EventType.OMIKUJI_DRAWN)
    ).one()
    assert event.actor_id == 1
    assert event.data == {
        "season_id": omikuji_season.id,
        "outcome": "kichi",
        "coins": out.coins,
        "consumable": out.consumable.value,
        "hex_kind": None,
        "spared": False,
    }
    line = next(r for r in log_records if r.message == "drew omikuji kichi")
    assert line.level == "INFO"
    assert line.extra["outcome"] == "kichi"
    assert line.extra["coins"] == out.coins


def test_a_refusal_logs_a_warning(session, omikuji_season, log_records):
    draw(session, player(session), NOW, rng_for(OmikujiKind.SHOKICHI))
    with pytest.raises(OmikujiRefusedError):
        draw(session, player(session), NOW)
    line = next(r for r in log_records if r.message == "omikuji refused: already")
    assert line.level == "WARNING"
    assert line.extra["refusal"] == "already"


def test_slip_png_reads_the_run_art(tmp_path, monkeypatch):
    art = tmp_path / "demo_3" / "omikuji"
    art.mkdir(parents=True)
    (art / "kyo.png").write_bytes(b"png")
    monkeypatch.setattr(omikuji, "SEASON_ART_DIR", tmp_path)
    assert omikuji.slip_png("demo_3", OmikujiKind.KYO) == b"png"


def test_slip_png_missing_file_is_none_and_logged(tmp_path, monkeypatch, log_records):
    monkeypatch.setattr(omikuji, "SEASON_ART_DIR", tmp_path)
    assert omikuji.slip_png("demo_3", OmikujiKind.KYO) is None
    assert any(r.level == "ERROR" for r in log_records)
