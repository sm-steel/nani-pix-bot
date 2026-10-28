from io import BytesIO

import pytest
from PIL import Image

from nani_pix_bot.models.cosmetics import EquippedItem
from nani_pix_bot.models.enums import CosmeticSlot, PixelAlgorithm, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons.mechanics import HauntTwist
from nani_pix_bot.services import mascot, pixelate
from nani_pix_bot.services.cosmetics import stage
from nani_pix_bot.services.mascot import Pose
from nani_pix_bot.services.seasons import haunting
from tests.costume_fixtures import ORANGE, activate_season

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
        mascot,
        "add_to_png",
        lambda png, pose, costume=None: poses.append(pose) or real(png, pose, costume),
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
    stage.stage_png_with(
        _image(), WIDTH, PixelAlgorithm.BOX, "simple_border", stage.SeasonLook("plain")
    )
    assert calls == [("style", "plain"), ("frame", "simple_border")]


def test_no_style_no_call(monkeypatch) -> None:
    monkeypatch.setattr(stage.styles, "apply_style", lambda *_: pytest.fail("styled"))
    stage.stage_png_with(_image(), WIDTH, PixelAlgorithm.BOX, None)


def _season_game(session, **fields) -> Game:
    return _game(session, season_id=activate_season(session), **fields)


def test_stage_png_dresses_nani_in_the_starters_costume(session, costume_run) -> None:
    game = _season_game(session)
    out = Image.open(BytesIO(stage.stage_png(session, game, _image(), 200))).convert("RGB")
    x, y, width, height = mascot.box(out.size, Pose.CAST, costume_run)
    assert out.getpixel((x + width // 2, y + height // 2)) == ORANGE[:3]


def test_stage_png_logs_the_costume_at_info(session, costume_run, log_records) -> None:
    game = _season_game(session)
    stage.stage_png(session, game, _image(), 200)
    lines = [r for r in log_records if r.extra.get("costume") == "test_hat" and r.level == "INFO"]
    assert len(lines) == 1
    assert lines[0].extra["game_id"] == game.id


def test_no_season_no_costume_is_byte_identical(session, costume_run) -> None:
    game = _game(session)
    assert stage.costume_for(session, game) is None
    assert stage.stage_png(session, game, _image(), 200) == mascot.add_to_png(
        pixelate.pixelate(_image(), 200, game.pixel_algorithm), Pose.CAST
    )


def test_the_twist_goes_under_the_style(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        stage.haunting,
        "apply_twists",
        lambda png, twists: calls.append(("twist", twists)) or png,
    )
    monkeypatch.setattr(
        stage.styles, "apply_style", lambda png, key: calls.append(("style", key)) or png
    )
    monkeypatch.setattr(
        stage.frames, "apply_frame", lambda png, key: calls.append(("frame", key)) or png
    )
    stage.stage_png_with(
        _image(),
        WIDTH,
        PixelAlgorithm.BOX,
        "simple_border",
        stage.SeasonLook("plain", stage.Haunt((HauntTwist.KITSUNE_MIRROR,))),
    )
    assert calls == [
        ("twist", (HauntTwist.KITSUNE_MIRROR,)),
        ("style", "plain"),
        ("frame", "simple_border"),
    ]


def test_no_twist_no_call(monkeypatch) -> None:
    monkeypatch.setattr(stage.haunting, "apply_twists", lambda *_a, **_k: pytest.fail("twisted"))
    stage.stage_png_with(_image(), WIDTH, PixelAlgorithm.BOX, None)


def test_an_unhaunted_game_renders_exactly_as_before(session, tagged_game) -> None:
    tagged_game.pixel_algorithm = PixelAlgorithm.BOX
    expected = mascot.add_to_png(
        stage.stage_png_with(
            _image(),
            WIDTH,
            tagged_game.pixel_algorithm,
            stage.frame_for(session, tagged_game),
            stage.SeasonLook("plain"),
        ),
        Pose.CAST,
        stage.costume_for(session, tagged_game),
    )
    assert stage.stage_png(session, tagged_game, _image(), WIDTH) == expected


def _spy_render(monkeypatch) -> list[tuple[HauntTwist, ...] | None]:
    seen: list[tuple[HauntTwist, ...] | None] = []
    real = stage.stage_png_with

    def spy(*args, **kwargs):
        look = args[4] if len(args) > 4 else kwargs.get("look")
        haunt = look and look.haunt
        seen.append(haunt.twists if haunt else None)
        return real(*args, **kwargs)

    monkeypatch.setattr(stage, "stage_png_with", spy)
    return seen


def test_a_fading_twist_is_left_off_the_last_stage(session, haunted_game, monkeypatch) -> None:
    haunted_game.haunt_twist = "tanuki_leaf"
    haunted_game.current_stage = PixelStage.STAGE_5
    seen = _spy_render(monkeypatch)
    stage.stage_png(session, haunted_game, _image(), WIDTH)
    assert seen == [()]


def test_an_early_stage_keeps_the_fading_twist(session, haunted_game, monkeypatch) -> None:
    haunted_game.haunt_twist = "tanuki_leaf"
    seen = _spy_render(monkeypatch)
    stage.stage_png(session, haunted_game, _image(), WIDTH)
    assert seen == [(HauntTwist.TANUKI_LEAF,)]


def test_hard_mode_last_turn_is_last(session, haunted_game, monkeypatch) -> None:
    haunted_game.haunt_twist = "kyokotsu_xray"
    haunted_game.hard_mode = True
    haunted_game.hard_mode_turn = 2
    haunted_game.current_stage = None
    seen = _spy_render(monkeypatch)
    stage.stage_png(session, haunted_game, _image(), WIDTH)
    assert seen == [()]


def test_a_boss_game_stacks_the_twists_by_stage(session, haunted_game, monkeypatch) -> None:
    haunted_game.boss_game = True
    haunted_game.current_stage = PixelStage.STAGE_2
    seen = _spy_render(monkeypatch)
    stage.stage_png(session, haunted_game, _image(), WIDTH)
    assert seen == [haunting.BOSS_ORDER[:2]]


def test_a_boss_keeps_all_four_twists_on_the_last_stage(session, haunted_game, monkeypatch) -> None:
    haunted_game.boss_game = True
    haunted_game.current_stage = PixelStage.STAGE_5
    seen = _spy_render(monkeypatch)
    stage.stage_png(session, haunted_game, _image(), WIDTH)
    assert seen == [haunting.BOSS_ORDER]


def test_twist_is_logged_once_per_render(session, haunted_game, log_records) -> None:
    haunted_game.haunt_twist = "kitsune_mirror"
    stage.stage_png(session, haunted_game, _image(), WIDTH)
    lines = [
        r for r in log_records if r.extra.get("twists") == "kitsune_mirror" and r.level == "INFO"
    ]
    assert len(lines) == 1
    assert lines[0].extra["stage"] == 1
    assert lines[0].extra["game_id"] == haunted_game.id


def test_a_clean_game_logs_no_twist(session, tagged_game, log_records) -> None:
    stage.stage_png(session, tagged_game, _image(), WIDTH)
    assert not [r for r in log_records if "twists" in r.extra or "twist" in r.extra]


def test_the_stored_twist_really_changes_the_pixels(session, haunted_game) -> None:
    clean = stage.stage_png(session, haunted_game, _image(), WIDTH)
    haunted_game.haunt_twist = "kyokotsu_xray"
    assert stage.stage_png(session, haunted_game, _image(), WIDTH) != clean
    assert haunting.stored_twist(haunted_game) is HauntTwist.KYOKOTSU_XRAY


def test_current_haunt_leaves_a_faded_twist_out(session, haunted_game, fake_runs) -> None:
    haunted_game.haunt_twist = "tanuki_leaf"
    haunted_game.current_stage = PixelStage.STAGE_5

    assert stage.current_haunt(session, haunted_game) == stage.Haunt(())


def test_current_haunt_is_none_for_a_clean_game(session, haunted_game, fake_runs) -> None:
    assert stage.current_haunt(session, haunted_game) is None


def test_the_mirror_never_fades(session, haunted_game, fake_runs) -> None:
    haunted_game.haunt_twist = "kitsune_mirror"
    haunted_game.current_stage = PixelStage.STAGE_5

    assert stage.current_haunt(session, haunted_game) == stage.Haunt((HauntTwist.KITSUNE_MIRROR,))


def test_a_twist_that_raises_does_not_block_the_stage(monkeypatch, log_records) -> None:
    def boom(_image):
        raise OSError("bad image mode")

    monkeypatch.setitem(haunting._OPS, HauntTwist.KYOKOTSU_XRAY, boom)
    look = stage.SeasonLook(None, stage.Haunt((HauntTwist.KYOKOTSU_XRAY,)))
    png = stage.stage_png_with(_image(), WIDTH, PixelAlgorithm.BOX, None, look)
    assert png == pixelate.pixelate(_image(), WIDTH, PixelAlgorithm.BOX)
    assert any(r.level == "ERROR" and r.extra["kind"] == "twist" for r in log_records)
