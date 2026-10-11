from io import BytesIO

from PIL import Image

from nani_pix_bot.models.cosmetics import EquippedItem
from nani_pix_bot.models.enums import CosmeticSlot, PixelAlgorithm
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import pixelate
from nani_pix_bot.services.cosmetics import stage

STARTER = 5
WIDTH = 16


def _image() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (200, 120), (10, 200, 30)).save(buf, "PNG")
    return buf.getvalue()


def _game(session) -> Game:
    session.add(Player(telegram_user_id=STARTER, username="starter"))
    game = Game(starter_id=STARTER, pixel_algorithm=PixelAlgorithm.BOX)
    session.add(game)
    session.flush()
    return game


def test_no_frame_equals_plain_pixelate(session) -> None:
    game = _game(session)
    assert stage.stage_png(session, game, _image(), WIDTH) == pixelate.pixelate(
        _image(), WIDTH, game.pixel_algorithm
    )


def test_equipped_frame_paints_the_edge_and_keeps_size(session, equip_frame) -> None:
    game = _game(session)
    equip_frame(session, STARTER)
    plain = Image.open(BytesIO(pixelate.pixelate(_image(), WIDTH, game.pixel_algorithm)))
    framed = Image.open(BytesIO(stage.stage_png(session, game, _image(), WIDTH)))
    assert framed.size == plain.size
    assert framed.getpixel((0, 0)) != plain.convert("RGB").getpixel((0, 0))


def test_unresolvable_equipped_frame_is_unframed(session, equip_frame) -> None:
    game = _game(session)
    session.add(
        EquippedItem(player_id=STARTER, slot=CosmeticSlot.HOST_FRAME, item_id="gone_1/host_frame.x")
    )
    session.flush()
    assert stage.stage_png(session, game, _image(), WIDTH) == pixelate.pixelate(
        _image(), WIDTH, game.pixel_algorithm
    )


def test_stage_png_with_applies_the_given_frame() -> None:
    out = Image.open(
        BytesIO(stage.stage_png_with(_image(), WIDTH, PixelAlgorithm.BOX, "simple_border"))
    )
    assert out.getpixel((0, 0)) != (10, 200, 30)
