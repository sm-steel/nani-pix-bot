from nani_pix_bot.commands.helpers.player_picker import (
    PAGE_SIZE,
    PickEntry,
    PickerData,
    clamp,
    picker_keyboard,
)
from nani_pix_bot.services import i18n

DATA = PickerData(pick=lambda e: f"t:{e.player_id}", page=lambda p: f"p:{p}", cancel="x")
NINE = [PickEntry(player_id=n, label=f"P{n}") for n in range(1, 10)]


def _rows(page: int, entries=NINE) -> list[list[tuple[str, str]]]:
    markup = picker_keyboard(entries, page, DATA, "EN")
    return [[(b.text, str(b.callback_data)) for b in row] for row in markup.inline_keyboard]


def test_player_picker_pages() -> None:
    first, second = _rows(0), _rows(1)

    assert len(first) == PAGE_SIZE + 1
    assert first[0] == [("P1", "t:1")]
    assert first[-1] == [
        (i18n.t("player_picker.cancel", "EN"), "x"),
        (i18n.t("player_picker.next", "EN"), "p:1"),
    ]
    assert second == [
        [("P9", "t:9")],
        [(i18n.t("player_picker.prev", "EN"), "p:0"), (i18n.t("player_picker.cancel", "EN"), "x")],
    ]


def test_an_out_of_range_page_clamps_to_the_last() -> None:
    assert _rows(7) == _rows(1)
    assert clamp(NINE, -3) == 0


def test_one_page_has_only_cancel() -> None:
    assert _rows(0, NINE[:3])[-1] == [(i18n.t("player_picker.cancel", "EN"), "x")]


def test_an_empty_list_has_only_cancel() -> None:
    assert _rows(0, []) == [[(i18n.t("player_picker.cancel", "EN"), "x")]]
