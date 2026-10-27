from nani_pix_bot.services.easing import smoothstep


def test_smoothstep_pins_its_ends_and_midpoint() -> None:
    assert (smoothstep(0), smoothstep(0.5), smoothstep(1)) == (0, 0.5, 1)


def test_smoothstep_clamps_outside_the_unit_range() -> None:
    assert (smoothstep(-3), smoothstep(7)) == (0, 1)
