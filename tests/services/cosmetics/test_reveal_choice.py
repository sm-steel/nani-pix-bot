from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.cosmetics import reveal_choice
from nani_pix_bot.services.reveal import store


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
