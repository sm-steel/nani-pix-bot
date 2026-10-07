from nani_pix_bot.services.achievements import catalogue, names


def test_roman() -> None:
    assert [names.roman(n) for n in (1, 4, 6, 9, 14, 40, 90, 149)] == [
        "I",
        "IV",
        "VI",
        "IX",
        "XIV",
        "XL",
        "XC",
        "CXLIX",
    ]


def test_multi_tier_names_carry_their_tier_one_shots_dont() -> None:
    assert names.name(catalogue.get("sharpshooter"), 3, "EN") == "Sharpshooter III"
    assert names.name(catalogue.get("sharpshooter"), 0, "EN") == "Sharpshooter"
    assert names.name(catalogue.get("clutch"), 1, "EN") == "Clutch"


def test_descriptions_show_the_tier_threshold() -> None:
    assert names.description(catalogue.get("sharpshooter"), 2, "EN") == "Games won: 5"
    assert names.description(catalogue.get("sharpshooter"), 7, "EN") == "Games won: 200"


def test_period_labels_and_champion_titles() -> None:
    assert names.period_label("2026-W41", "EN") == "41 (2026)"
    assert names.period_label("2026-10", "EN") == "October 2026"
    assert names.period_label("2026", "EN") == "2026"
    month = catalogue.get("champion_month")
    assert names.title(month, 1, "2026-10", "EN") == "Champion of October 2026"
    assert names.title(catalogue.get("pioneer"), 1, "", "EN") == "Pioneer"
