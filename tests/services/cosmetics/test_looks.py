import pytest

from nani_pix_bot.models.enums import CosmeticSlot
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Item
from nani_pix_bot.services.cosmetics import looks


def _resolved(item: Item) -> registry.ResolvedItem:
    run = registry.discover("tests.seasons.fake_runs")["demo_1"]
    return registry.ResolvedItem(f"demo_1/{item.key}", run, item)


def test_the_fake_runs_items_are_all_valid() -> None:
    runs = registry.discover("tests.seasons.fake_runs")
    for item in runs["demo_1"].items:
        assert (
            looks.problems(registry.ResolvedItem(f"demo_1/{item.key}", runs["demo_1"], item)) == []
        )


@pytest.mark.parametrize(
    ("item", "needle"),
    [
        (
            Item("guess_reaction.x", CosmeticSlot.GUESS_REACTION, "🌸", {"EN": "x", "RU": "x"}),
            "allowed reaction",
        ),
        (
            Item("host_frame.x", CosmeticSlot.HOST_FRAME, "nope", {"EN": "x", "RU": "x"}),
            "unknown frame",
        ),
        (
            Item("host_transition.x", CosmeticSlot.HOST_TRANSITION, "NOPE", {"EN": "x", "RU": "x"}),
            "unknown reveal effect",
        ),
        (
            Item("win_effect.x", CosmeticSlot.WIN_EFFECT, "nope", {"EN": "x", "RU": "x"}),
            "unknown win overlay",
        ),
        (Item("name_flair.x", CosmeticSlot.NAME_FLAIR, "abc", {"EN": "x", "RU": "x"}), "emoji"),
        (Item("title.x", CosmeticSlot.TITLE, "", {"EN": "x"}), "RU"),
        (Item("flair.x", CosmeticSlot.NAME_FLAIR, "🌸", {"EN": "x", "RU": "x"}), "must start with"),
    ],
)
def test_invalid_looks_are_reported(item: Item, needle: str) -> None:
    assert any(needle in p for p in looks.problems(_resolved(item)))


def test_an_over_long_title_id_is_a_problem() -> None:
    name = "title." + "x" * looks.MAX_TITLE_ID_BYTES
    found = looks.problems(_resolved(Item(name, CosmeticSlot.TITLE, "", {"EN": "x", "RU": "x"})))

    assert any("title id too long for /title callbacks" in p for p in found)
    ok = looks.problems(_resolved(Item("title.ok", CosmeticSlot.TITLE, "", {"EN": "x", "RU": "x"})))
    assert ok == []
