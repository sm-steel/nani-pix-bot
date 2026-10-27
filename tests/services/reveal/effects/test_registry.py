from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal.effects import EFFECTS


def test_every_reveal_effect_has_a_renderer() -> None:
    assert set(EFFECTS) == set(RevealEffect)
