from io import BytesIO

from PIL import Image

from nani_pix_bot.services.cosmetics import clue_token_icon


def test_the_icon_is_a_square_png() -> None:
    image = Image.open(BytesIO(clue_token_icon.icon()))
    assert (image.format, image.size) == ("PNG", (clue_token_icon.SIZE, clue_token_icon.SIZE))


def test_the_icon_is_the_same_bytes_every_time() -> None:
    draw = clue_token_icon.icon.__wrapped__
    assert draw() == draw()
