from types import MappingProxyType

from nani_pix_bot.models.enums import CosmeticSlot
from nani_pix_bot.seasons.definition import CoinTable, Gate, GateTag, Item, SeasonRun, XpTable


def _names(en: str, ru: str):
    return MappingProxyType({"EN": en, "RU": ru})


ITEMS = (
    Item("name_flair.blossom", CosmeticSlot.NAME_FLAIR, "🌸", _names("Blossom", "Цветок")),
    Item("guess_reaction.love", CosmeticSlot.GUESS_REACTION, "🥰", _names("Love", "Любовь")),
    Item("host_frame.border", CosmeticSlot.HOST_FRAME, "simple_border", _names("Border", "Рамка")),
    Item("host_transition.iris", CosmeticSlot.HOST_TRANSITION, "IRIS", _names("Iris", "Ирис")),
    Item(
        "win_effect.confetti", CosmeticSlot.WIN_EFFECT, "confetti", _names("Confetti", "Конфетти")
    ),
    Item("title.demo", CosmeticSlot.TITLE, "", _names("Demo Star", "Демо-звезда")),
)

RUN = SeasonRun(
    run_id="demo_1",
    names=MappingProxyType({"EN": "Demo Season", "RU": "Демо-сезон"}),
    xp=XpTable(win_by_stage=(50, 40, 30, 20, 10), host_solved=15, host_unsolved=5, first_guess=5),
    gate=Gate(
        any_of=(GateTag("genre", "Romance", 22, shikimori_id=22),),
        description=MappingProxyType({"EN": "Romance", "RU": "Романтика"}),
    ),
    items=ITEMS,
    coins=CoinTable(win=25, host_solved=15, host_unsolved=5, first_guess=5),
)
