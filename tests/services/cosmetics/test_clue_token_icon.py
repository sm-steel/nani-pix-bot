from io import BytesIO

from PIL import Image

from nani_pix_bot.services.cosmetics import clue_token_icon


def test_the_icon_is_a_square_png() -> None:
    image = Image.open(BytesIO(clue_token_icon.icon()))
    assert (image.format, image.size) == ("PNG", (clue_token_icon.SIZE, clue_token_icon.SIZE))


def test_the_icon_is_the_same_bytes_every_time() -> None:
    draw = clue_token_icon.icon.__wrapped__
    assert draw() == draw()


def test_the_ticket_is_the_icon_cut_out_along_its_shape() -> None:
    ticket = clue_token_icon.ticket()
    assert ticket.mode == "RGBA"
    assert ticket.size == (clue_token_icon.SIZE, clue_token_icon.SIZE)
    alpha = ticket.getchannel("A")
    centre = clue_token_icon.SIZE // 2
    assert (alpha.getpixel((0, 0)), alpha.getpixel((centre, centre))) == (0, 255)
