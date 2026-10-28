"""While Nopperabo lasts the player is 👤 in every text mention, the localized
"Faceless" label on every image, and still their real self in the logs."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.commands import leaderboard, standings
from nani_pix_bot.jobs import hex_posts
from nani_pix_bot.jobs.timers import vote
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import OutboxKind
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import players
from nani_pix_bot.services.achievements.periods import Standing
from nani_pix_bot.services.cosmetics import flair, profile
from nani_pix_bot.services.seasons import hexes
from tests.services.seasons.conftest import NOW, make_hex_season
from tests.services.seasons.test_hex_triggers import _win


@pytest.fixture(autouse=True)
def _runs(fake_runs):
    """The shared fake runs."""


@pytest.fixture
def season(session):
    row = make_hex_season(session)
    for user, name in {1: "alice", 2: "bob"}.items():
        session.get(Player, user).username = name
    session.flush()
    return row


def _curse(session, season, user: int, kind: str = "nopperabo") -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    session.add(
        Hex(
            season_id=season.id,
            kind=kind,
            caster_id=4,
            target_id=user,
            cast_at=now,
            expires_at=now + timedelta(hours=3),
        )
    )
    session.flush()


def test_a_name_is_masked_only_while_the_curse_lasts(session, season):
    assert players.display_name(session, 2) == "@bob"
    _curse(session, season, 2)
    assert players.display_name(session, 2) == "👤"
    assert players.display_name(session, 2, unmasked=True) == "@bob"
    assert players.display_name(session, 1) == "@alice"


def test_a_guess_reply_names_the_player_by_the_mask(session, season):
    _curse(session, season, 2)
    assert flair.named(session, 2, "Bob Full Name") == "👤 💀"


def test_the_leaderboard_and_standings_show_the_mask(session, season):
    _curse(session, season, 2)
    row = players.LeaderRow(2, wins=3, currency=5, points=0, title_key=None)
    assert "👤" in leaderboard._name(session, row, "en")
    assert "bob" not in leaderboard._name(session, row, "en")
    table = "\n".join(standings.table(session, [(1, Standing(2, score=9))], "en"))
    assert "👤" in table
    assert "bob" not in table


def test_the_vote_ballot_shows_the_mask(session, season):
    _curse(session, season, 2)
    assert vote._handle(session, 2) == "👤"
    assert vote._handle(session, 1) == "@alice"


def test_a_hex_trigger_notice_masks_a_faceless_caster(session, season):
    hexes.cast(session, session.get(Player, 1), session.get(Player, 2), HexKind.KAPPA, NOW)
    _curse(session, season, 1)
    _win(session, 2)
    stmt = select(AnnouncementOutbox).where(AnnouncementOutbox.kind == OutboxKind.HEX_NOTICE)
    row = session.scalars(stmt.order_by(AnnouncementOutbox.id.desc())).first()
    post = hex_posts.RENDERERS[OutboxKind.HEX_NOTICE](session, row, "en")
    assert post is not None
    assert "👤" in post.text
    assert "@alice" not in post.text
    assert "@bob" in post.text


@pytest.mark.parametrize(("lang", "label"), [("en", "Faceless"), ("ru", "Без лица")])
def test_images_draw_the_label_not_the_name(session, season, lang, label):
    assert players.image_name(session, 2, lang) == "@bob"
    _curse(session, season, 2)
    assert players.image_name(session, 2, lang) == label
    assert players.image_name(session, 2, lang, "Bob Full Name") == label
    assert profile.build(session, 2, lang).name == label  # the profile card's name


def test_logs_keep_the_real_name(session, season):
    _curse(session, season, 2)
    assert players.describe_player_id(session, 2) == "2 (@bob)"
