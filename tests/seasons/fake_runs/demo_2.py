"""A second fake run: "another season" in tests, with a gate on a different
genre than demo_1's."""

from types import MappingProxyType

from nani_pix_bot.models.enums import CosmeticSlot
from nani_pix_bot.seasons.definition import Gate, GateTag, Item, SeasonRun, XpTable


def _names(en: str, ru: str):
    return MappingProxyType({"EN": en, "RU": ru})


RUN = SeasonRun(
    run_id="demo_2",
    names=_names("Second Demo", "Второй демо-сезон"),
    xp=XpTable(win_by_stage=(50, 40, 30, 20, 10), host_solved=15, host_unsolved=5, first_guess=5),
    gate=Gate(
        any_of=(GateTag("genre", "Action", 1, shikimori_id=1),),
        description=_names("Action", "Экшен"),
    ),
    items=(
        Item("name_flair.bolt", CosmeticSlot.NAME_FLAIR, "⚡", _names("Bolt", "Молния")),
        Item("name_flair.fire", CosmeticSlot.NAME_FLAIR, "🔥", _names("Fire", "Огонь")),
        Item("guess_reaction.wow", CosmeticSlot.GUESS_REACTION, "😮", _names("Wow", "Удивление")),
    ),
)
