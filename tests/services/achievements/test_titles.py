import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.player import Player
from nani_pix_bot.services.achievements import engine, titles

pytestmark = pytest.mark.achievements


def _title_of(session: Session, player_id: int) -> str | None:
    player = session.get(Player, player_id)
    assert player is not None
    return player.title_key


def test_only_prestige_grants_are_titles(session: Session) -> None:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
    session.flush()
    clutch = engine.grant(session, engine.GrantRequest(1, "clutch"))  # one-shot: no title
    top = engine.grant(session, engine.GrantRequest(1, "hat_trick", 2))  # top tier: title
    champ = engine.grant(session, engine.GrantRequest(1, "champion_month", 1, "2026-10"))

    ids = {g.id for g in titles.eligible(session, 1)}

    assert ids == {top.id, champ.id}
    assert clutch.id not in ids


def test_choose_sets_clears_and_refuses_others_grants(session: Session) -> None:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
    session.flush()
    mine = engine.grant(session, engine.GrantRequest(1, "champion_month", 1, "2026-10"))
    theirs = engine.grant(session, engine.GrantRequest(2, "champion_week", 1, "2026-W41"))

    assert titles.choose(session, 1, mine.id)
    assert _title_of(session, 1) == "champion_month:1:2026-10"
    assert titles.text("champion_month:1:2026-10", "EN") == "Champion of October 2026"
    assert not titles.choose(session, 1, theirs.id)
    assert titles.choose(session, 1, None)
    assert _title_of(session, 1) is None


def test_an_unknown_title_key_renders_as_nothing() -> None:
    assert titles.text("retired_key:1:", "EN") is None
    assert titles.text(None, "EN") is None
