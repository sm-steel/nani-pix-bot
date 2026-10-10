"""The guide's example cards must be exactly what the bot itself would post."""

from pathlib import Path

import pytest

from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.services import i18n
from nani_pix_bot.services.achievements.rewards import POINTS
from nani_pix_bot.services.cards import (
    load_background,
    podium_slot,
    render_podium_card,
    render_unlock_card,
    unlock_slot,
)
from nani_pix_bot.services.economy.config import DEFAULT_AMOUNTS, EconomyKey
from scripts import gen_docs_cards

LANGS = ("en", "ru")


@pytest.mark.parametrize("lang", LANGS)
def test_unlock_card_reads_like_the_bots_announcement(lang: str) -> None:
    card = gen_docs_cards.unlock_card(lang)
    assert card.rarity is Rarity.GOLD
    assert card.headline == i18n.t("card.unlocked", lang)
    assert card.name == i18n.t("achievement.sharpshooter.name", lang) + " V"
    assert card.description == i18n.t("achievement.sharpshooter.desc", lang, n=50)
    assert card.rarity_label == i18n.t("achievement.rarity.gold", lang)
    assert card.reward == DEFAULT_AMOUNTS[EconomyKey.ACHIEVEMENT_GOLD]
    assert card.points_label == i18n.t("card.points", lang, points=POINTS[Rarity.GOLD])


@pytest.mark.parametrize("lang", LANGS)
def test_podium_card_is_a_yearly_one(lang: str) -> None:
    card = gen_docs_cards.podium_card(lang)
    assert card.title == i18n.t("card.podium.year", lang, period=gen_docs_cards.YEAR)
    assert [entry.rank for entry in card.entries] == [1, 2, 3]
    for entry, (name, score, wins) in zip(card.entries, gen_docs_cards.PODIUM, strict=True):
        assert entry.name == name
        assert entry.score_label == i18n.t("card.score", lang, score=score, wins=wins)


def test_writes_the_bots_own_renders_per_language(tmp_path: Path) -> None:
    gen_docs_cards.generate(tmp_path / "nested")
    out = tmp_path / "nested"
    for lang in LANGS:
        unlock = gen_docs_cards.unlock_card(lang)
        background = load_background(unlock_slot(Rarity.GOLD), gen_docs_cards.UNLOCK_SEED)
        assert (out / f"unlock-{lang}.png").read_bytes() == render_unlock_card(
            unlock, None, background
        )
        podium = gen_docs_cards.podium_card(lang)
        background = load_background(podium_slot("year"), podium.entries[0].seed)
        assert (out / f"podium-{lang}.png").read_bytes() == render_podium_card(podium, background)
