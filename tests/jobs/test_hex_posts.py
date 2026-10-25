import pytest
from sqlalchemy import select

from nani_pix_bot.jobs import hex_posts
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import OutboxKind
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.seasons import hexes
from tests.services.seasons.conftest import NOW, make_hex_season
from tests.services.seasons.test_hex_triggers import _guess, _rule, _run, _win


@pytest.fixture(autouse=True)
def _runs(fake_runs):
    """The shared fake runs."""


@pytest.fixture
def hex_season(session):
    return make_hex_season(session)


def _name(session) -> None:
    _set = {1: "alice", 2: "bob"}
    for user, name in _set.items():
        player = session.get(Player, user)
        player.username = name
    session.flush()


def _render(session, row: AnnouncementOutbox, lang: str = "en") -> str:
    post = hex_posts.RENDERERS[OutboxKind.HEX_NOTICE](session, row, lang)
    assert post is not None
    return post.text


def _last(session) -> AnnouncementOutbox:
    stmt = select(AnnouncementOutbox).where(AnnouncementOutbox.kind == OutboxKind.HEX_NOTICE)
    return session.scalars(stmt.order_by(AnnouncementOutbox.id.desc())).first()


def test_kappa_notice_names_both_and_the_amount(session, hex_season):
    _name(session)
    hexes.cast(session, session.get(Player, 1), session.get(Player, 2), HexKind.KAPPA, NOW)
    _win(session, 2)
    share = 25 * _rule(HexKind.KAPPA).amount // 100
    for lang in ("en", "ru"):
        text = _render(session, _last(session), lang)
        assert "@bob" in text
        assert "@alice" in text
        assert str(share) in text


def test_kitsune_notice(session, hex_season):
    _name(session)
    hexes.cast(session, session.get(Player, 1), session.get(Player, 2), HexKind.KITSUNE, NOW)
    _guess(session, 2, correct=False)
    text = _render(session, _last(session))
    assert "@bob" in text
    assert str(_rule(HexKind.KITSUNE).amount) in text


def test_self_prank_notice_names_the_prank(session, hex_season):
    _name(session)
    row = hexes.apply_self_prank(session, _run(), 2, NOW)
    text = _render(session, _last(session))
    assert "@bob" in text
    assert str(_rule(HexKind(row.kind)).duration_hours) in text


def test_notice_for_a_missing_hex_row_renders_nothing(session, hex_season):
    row = outbox.enqueue_hex_notice(session, 9999, "triggered")
    assert hex_posts.RENDERERS[OutboxKind.HEX_NOTICE](session, row, "en") is None
    assert session.get(Hex, 9999) is None
