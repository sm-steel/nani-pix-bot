import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.cosmetics import ITEM_ID_LENGTH
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


DEMO = "demo_1/title.demo"


def test_item_titles_lists_owned_title_items(session: Session, equip_title) -> None:
    session.add(Player(telegram_user_id=1))
    session.flush()
    assert titles.item_titles(session, 1) == []

    equip_title(session, 1)

    assert [r.item_id for r in titles.item_titles(session, 1)] == [DEMO]


def test_choose_item_stores_item_key_and_needs_ownership(session: Session, equip_title) -> None:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
    session.flush()
    equip_title(session, 1)

    assert titles.choose_item(session, 1, DEMO)
    assert _title_of(session, 1) == f"item:{DEMO}"
    assert not titles.choose_item(session, 2, DEMO)
    assert _title_of(session, 2) is None
    assert not titles.choose_item(session, 1, "demo_1/title.missing")
    assert _title_of(session, 1) == f"item:{DEMO}"


def test_item_title_text_uses_the_item_name(equip_title, session: Session) -> None:
    assert titles.text(f"item:{DEMO}", "en") == "Demo Star"
    assert titles.text(f"item:{DEMO}", "ru") == "Демо-звезда"


def test_an_unresolvable_item_title_renders_as_nothing(equip_title, log_records) -> None:
    assert titles.text("item:gone_9/title.gone", "en") is None
    assert any(r.level == "WARNING" for r in log_records)


def test_item_title_id_budget_fits_title_key_and_callback_data() -> None:
    # Callback data "title:i:<id>" must stay <= 64 bytes; Player.title_key is String(96).
    assert len("title:i:") + titles.MAX_ITEM_ID_LENGTH <= 64
    assert len("item:") + titles.MAX_ITEM_ID_LENGTH <= 96
    assert titles.MAX_ITEM_ID_LENGTH <= ITEM_ID_LENGTH
