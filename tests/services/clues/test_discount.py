from nani_pix_bot.services.clues import discount


def test_no_discount_leaves_the_price() -> None:
    assert discount.apply(10) == 10
    assert discount.apply(10, 0, 0) == 10


def test_discounts_stack_with_one_rounding() -> None:
    assert discount.apply(10, 50, 25) == 3
    assert discount.apply(10, 0, 25) == 7


def test_never_below_one() -> None:
    assert discount.apply(1, 50, 25) == 1
