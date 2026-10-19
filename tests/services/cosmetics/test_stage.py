from io import BytesIO

import pytest
from PIL import Image

from nani_pix_bot.models.cosmetics import EquippedItem
from nani_pix_bot.models.enums import CosmeticSlot, PixelAlgorithm, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import mascot, pixelate
from nani_pix_bot.services.cosmetics import stage
from nani_pix_bot.services.mascot import Pose

STARTER = 5
WIDTH = 16


def _image() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (200, 120), (10, 200, 30)).save(buf, "PNG")
    return buf.getvalue()


def _game(session, **fields) -> Game:
    session.add(Player(telegram_user_id=STARTER, username="starter"))
    game = Game(
        starter_id=STARTER,
        pixel_algorithm=PixelAlgorithm.BOX,
        **{"current_stage": PixelStage.STAGE_1, **fields},
    )
    session.add(game)
    session.flush()
    return game


def _spy_poses(monkeypatch) -> list[Pose]:
    poses: list[Pose] = []
    real = mascot.add_to_png
    monkeypatch.setattr(
        mascot, "add_to_png", lambda png, pose: poses.append(pose) or real(png, pose)
    )
    return poses


def test_no_frame_is_the_pixelated_image_with_nani(session) -> None:
    game = _game(session)
    assert stage.stage_png(session, game, _image(), WIDTH) == mascot.add_to_png(
        pixelate.pixelate(_image(), WIDTH, game.pixel_algorithm), Pose.CAST
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
    assert stage.stage_png(session, game, _image(), WIDTH) == mascot.add_to_png(
        pixelate.pixelate(_image(), WIDTH, game.pixel_algorithm), Pose.CAST
    )


@pytest.mark.parametrize(
    ("current", "pose"),
    [
        (PixelStage.STAGE_1, Pose.CAST),
        (PixelStage.STAGE_2, Pose.NOPE),
        (PixelStage.STAGE_4, Pose.NOPE),
        (PixelStage.STAGE_5, Pose.NANI),
    ],
)
def test_each_stage_gets_its_pose(session, monkeypatch, current, pose) -> None:
    poses = _spy_poses(monkeypatch)
    stage.stage_png(session, _game(session, current_stage=current), _image(), WIDTH)
    assert poses == [pose]


@pytest.mark.parametrize(
    ("turn", "overthrow", "pose"),
    [(1, False, Pose.CAST), (1, True, Pose.SLY), (2, False, Pose.NANI), (2, True, Pose.NANI)],
)
def test_hard_mode_turns_get_their_pose(session, monkeypatch, turn, overthrow, pose) -> None:
    poses = _spy_poses(monkeypatch)
    game = _game(session, hard_mode=True, hard_mode_turn=turn, current_stage=None)
    stage.stage_png(session, game, _image(), WIDTH, overthrow=overthrow)
    assert poses == [pose]


def test_nani_goes_on_top_of_the_frame(session, equip_frame) -> None:
    game = _game(session)
    equip_frame(session, STARTER)
    framed = stage.stage_png_with(
        _image(), WIDTH, game.pixel_algorithm, stage.frame_for(session, game)
    )
    assert stage.stage_png(session, game, _image(), WIDTH) == mascot.add_to_png(framed, Pose.CAST)


def test_stage_png_with_applies_the_given_frame() -> None:
    png = stage.stage_png_with(_image(), WIDTH, PixelAlgorithm.BOX, "simple_border")
    out = Image.open(BytesIO(png))
    assert out.getpixel((0, 0)) != (10, 200, 30)


def test_the_style_goes_under_the_frame(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        stage.styles, "apply_style", lambda png, key: calls.append(("style", key)) or png
    )
    monkeypatch.setattr(
        stage.frames, "apply_frame", lambda png, key: calls.append(("frame", key)) or png
    )
    stage.stage_png_with(_image(), WIDTH, PixelAlgorithm.BOX, "simple_border", style="plain")
    assert calls == [("style", "plain"), ("frame", "simple_border")]


def test_no_style_no_call(monkeypatch) -> None:
    monkeypatch.setattr(stage.styles, "apply_style", lambda *_: pytest.fail("styled"))
    stage.stage_png_with(_image(), WIDTH, PixelAlgorithm.BOX, None)
