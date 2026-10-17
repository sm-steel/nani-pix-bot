from nani_pix_bot.services.game import reveal_extras


def _sizes(count: int) -> list[int]:
    images = [bytes([i]) for i in range(count)]
    return [len(batch) for batch in reveal_extras.batches(images)]


def test_no_images() -> None:
    assert reveal_extras.batches([]) == []


def test_one_image() -> None:
    assert reveal_extras.batches([b"x"]) == [[b"x"]]


def test_ten_images_one_batch() -> None:
    assert _sizes(10) == [10]


def test_eleven_images() -> None:
    assert _sizes(11) == [10, 1]


def test_twenty_one_images() -> None:
    assert _sizes(21) == [10, 10, 1]


def test_order_is_kept() -> None:
    images = [bytes([i]) for i in range(12)]
    assert [b for batch in reveal_extras.batches(images) for b in batch] == images
