from dataclasses import replace
from types import MappingProxyType

import pytest

from nani_pix_bot.models.cosmetics import EquippedItem, OwnedItem
from nani_pix_bot.models.enums import CosmeticSlot, ItemSource, RevealEffect
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Item
from nani_pix_bot.services.cosmetics import reveal_choice
from nani_pix_bot.services.reveal import store

_NAMES = MappingProxyType({"EN": "x", "RU": "x"})


@pytest.fixture
def win_effects(monkeypatch) -> None:
    """The demo run plus win effects for both Sakura overlays and an unregistered one."""
    extra = tuple(
        Item(f"win_effect.{look}", CosmeticSlot.WIN_EFFECT, look, _NAMES)
        for look in ("petal_drift", "sakura_storm", "fireworks")
    )
    demo = registry.all_runs()["demo_1"]
    runs = {"demo_1": replace(demo, items=(*demo.items, *extra))}
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()


def _equip_win_effect(session, player: int, look: str) -> None:
    item_id = f"demo_1/win_effect.{look}"
    session.add(Player(telegram_user_id=player))
    session.add(OwnedItem(player_id=player, item_id=item_id, source=ItemSource.ADMIN))
    session.add(EquippedItem(player_id=player, slot=CosmeticSlot.WIN_EFFECT, item_id=item_id))
    session.flush()


def test_no_win_effect_equipped_means_nothing_to_draw_ahead(session, win_effects) -> None:
    assert reveal_choice.overlays_in_use(session) == ()


def test_overlays_in_use_are_the_distinct_registered_ones_without_the_default(
    session, win_effects
) -> None:
    _equip_win_effect(session, 1, "sakura_storm")
    _equip_win_effect(session, 2, "petal_drift")
    _equip_win_effect(session, 3, "sakura_storm")
    _equip_win_effect(session, 4, "confetti")  # drawn ahead anyway
    _equip_win_effect(session, 5, "fireworks")  # not a registered overlay
    assert reveal_choice.overlays_in_use(session) == ("petal_drift", "sakura_storm")


def test_no_transition_falls_back_to_a_random_effect(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(store, "pick_effect", lambda: RevealEffect.GLITCH)
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        assert reveal_choice.effect_for(session, 1) is RevealEffect.GLITCH


def test_equipped_transition_wins_over_random(session_factory, equip_transition, monkeypatch):
    monkeypatch.setattr(store, "pick_effect", lambda: RevealEffect.GLITCH)
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        equip_transition(session, 1)
        session.commit()
        assert reveal_choice.effect_for(session, 1) is RevealEffect.IRIS
