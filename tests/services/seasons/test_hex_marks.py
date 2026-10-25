"""The marks a hex draws on a name and a guess."""

from datetime import timedelta

from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services.cosmetics import flair
from nani_pix_bot.services.seasons import hex_marks
from tests.services.seasons.conftest import NOW, naive


def _hex(session, season, kind: HexKind, **extra) -> Hex:
    row = Hex(
        season_id=season.id,
        kind=kind.value,
        caster_id=1,
        target_id=2,
        cast_at=naive(NOW),
        **extra,
    )
    session.add(row)
    session.flush()
    return row


def test_no_hex_keeps_todays_name(session, hex_season):
    assert hex_marks.marked_name(session, 2, "@bob", "🎃", NOW) == "@bob 🎃"
    assert hex_marks.marked_name(session, 2, "@bob", None, NOW) == "@bob"


def test_a_hexed_name_gets_a_skull_after_the_flair(session, hex_season):
    _hex(session, hex_season, HexKind.KAPPA)
    assert hex_marks.marked_name(session, 2, "@bob", "🎃", NOW) == "@bob 🎃 💀"
    assert hex_marks.marked_name(session, 2, "@bob", None, NOW) == "@bob 💀"


def test_nopperabo_hides_name_flair_and_title(session, hex_season):
    _hex(session, hex_season, HexKind.NOPPERABO, expires_at=naive(NOW) + timedelta(hours=1))
    assert hex_marks.marked_name(session, 2, "@bob", "🎃", NOW) == "👤 💀"
    assert hex_marks.is_faceless(session, 2, NOW) is True


def test_a_kappa_does_not_make_a_name_faceless(session, hex_season):
    _hex(session, hex_season, HexKind.KAPPA)
    assert hex_marks.is_faceless(session, 2, NOW) is False


def test_no_skull_after_a_prank_expires(session, hex_season):
    _hex(session, hex_season, HexKind.CHOCHIN, expires_at=naive(NOW) + timedelta(hours=1))
    later = NOW + timedelta(hours=2)
    assert hex_marks.marked_name(session, 2, "@bob", None, later) == "@bob"
    assert hex_marks.is_faceless(session, 2, later) is False


def test_a_blocked_hex_leaves_no_mark(session, hex_season):
    _hex(session, hex_season, HexKind.KAPPA, resolved_at=naive(NOW), outcome="blocked")
    assert hex_marks.marked_name(session, 2, "@bob", None, NOW) == "@bob"


def test_chochin_reacts_to_every_guess(session, hex_season):
    _hex(session, hex_season, HexKind.CHOCHIN, expires_at=naive(NOW) + timedelta(hours=1))
    assert hex_marks.guess_reaction(session, 2, "❤", False, NOW) == "🤪"
    assert hex_marks.guess_reaction(session, 2, "❤", True, NOW) == "🤪"


def test_no_chochin_keeps_the_win_reaction_only(session, hex_season):
    assert hex_marks.guess_reaction(session, 2, "❤", True, NOW) == "❤"
    assert hex_marks.guess_reaction(session, 2, "❤", False, NOW) is None


def test_a_kappa_leaves_the_reaction_alone(session, hex_season):
    _hex(session, hex_season, HexKind.KAPPA)
    assert hex_marks.guess_reaction(session, 2, "❤", True, NOW) == "❤"
    assert hex_marks.guess_reaction(session, 2, None, False, NOW) is None


def test_flair_named_marks_a_hexed_player(session, hex_season):
    _hex(session, hex_season, HexKind.KAPPA)
    assert flair.named(session, 2, "@bob", NOW).endswith(" 💀")
    assert flair.named(session, 1, "@amy", NOW) == "@amy"
