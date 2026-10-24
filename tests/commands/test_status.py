from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import status
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus, PixelStage, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons.mechanics import HauntTwist
from nani_pix_bot.services import i18n, mascot
from nani_pix_bot.services.cosmetics import stage as stage_cosmetics
from nani_pix_bot.services.game import turns
from nani_pix_bot.services.mascot import Pose
from tests.costume_fixtures import activate_season

HOST, ALICE = 1, 2


def test_the_caption_says_stage_budget_bounty_and_time_left() -> None:
    live = status.LiveStatus(
        game_id=7,
        hard_mode=False,
        number=2,
        total=5,
        remaining=1,
        limit=2,
        pot=40,
        time_left=timedelta(hours=26, minutes=10),
    )

    caption = status.caption(live, "en")

    assert caption.splitlines() == [
        i18n.t("status.stage", "en", stage=2, total=5, remaining=1, limit=2),
        i18n.t("bounty.caption_line", "en", amount=40),
        i18n.t("status.ends", "en", duration="1d 2h"),
        i18n.t("game.id_line", "en", id=7),
    ]


def test_a_hard_mode_caption_names_the_turn_and_skips_an_empty_pot() -> None:
    live = status.LiveStatus(
        game_id=7,
        hard_mode=True,
        number=1,
        total=2,
        remaining=1,
        limit=1,
        pot=0,
        time_left=None,
    )

    lines = status.caption(live, "en").splitlines()

    assert lines == [
        i18n.t("status.turn_hard", "en", stage=1, total=2, remaining=1, limit=1),
        i18n.t("game.id_line", "en", id=7),
    ]


def _players(session: Session) -> None:
    session.add_all(
        [
            Player(telegram_user_id=HOST, username="host"),
            Player(telegram_user_id=ALICE, username="alice"),
        ]
    )
    session.flush()


def _update(thread_id: int = 7) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ALICE
    update.effective_chat.id = 555
    update.effective_chat.type = "supergroup"
    update.message.message_thread_id = thread_id
    update.message.chat_id = 555
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_photo = AsyncMock()
    context.bot.send_media_group = AsyncMock()
    return context


async def _run(update: MagicMock, context: MagicMock) -> None:
    await status.status_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))


async def test_an_active_game_is_reposted_with_a_live_caption(
    session_factory, monkeypatch: pytest.MonkeyPatch, log_records
) -> None:
    monkeypatch.setattr(
        stage_cosmetics.pixelate_service, "pixelate", lambda image, width, _a: image + b"@"
    )
    with session_scope(session_factory) as session:
        _players(session)
        session.add(
            Game(
                starter_id=HOST,
                status=GameStatus.ACTIVE,
                current_stage=PixelStage.STAGE_3,
                wrong_guess_count=1,
                original_image=b"img",
                scheduled_end_at=datetime.now(UTC) + timedelta(hours=5),
            )
        )
    context = _context(session_factory)

    await _run(_update(), context)

    context.bot.send_photo.assert_awaited_once()
    kwargs = context.bot.send_photo.await_args_list[0].kwargs
    assert kwargs["photo"] == b"img@"
    assert kwargs["message_thread_id"] == 7
    assert kwargs["caption"].startswith("📸 Stage 3/5")
    assert any(r.message.startswith("re-posted the current") for r in log_records)


async def test_a_hard_mode_game_is_reposted_as_its_pair(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        stage_cosmetics.pixelate_service, "pixelate", lambda image, width, _a: image
    )
    with session_scope(session_factory) as session:
        _players(session)
        session.add(
            Game(
                starter_id=HOST,
                status=GameStatus.ACTIVE,
                hard_mode=True,
                hard_mode_turn=1,
                current_stage=PixelStage.STAGE_1,
                hard_mode_image_a=b"a",
                hard_mode_image_b=b"b",
            )
        )
    context = _context(session_factory)

    await _run(_update(), context)

    media = context.bot.send_media_group.await_args_list[0].kwargs["media"]
    assert [m.media.input_file_content for m in media] == [b"a", b"b"]
    assert media[0].caption.startswith("😈")


async def test_with_no_game_it_says_whose_turn_it_is(session_factory) -> None:
    with session_scope(session_factory) as session:
        _players(session)
        turns.set_next_starter(session, HOST, reason="test")
    update = _update()

    await _run(update, _context(session_factory))

    update.message.reply_text.assert_awaited_once_with(
        i18n.t("status.turn_player", "en", player="@host")
    )


async def test_with_an_open_turn_anyone_may_start(session_factory) -> None:
    update = _update()

    await _run(update, _context(session_factory))

    update.message.reply_text.assert_awaited_once_with(i18n.t("status.turn_open", "en"))


async def test_a_game_in_setup_is_named(session_factory) -> None:
    with session_scope(session_factory) as session:
        _players(session)
        session.add(Game(starter_id=HOST, status=GameStatus.SETUP))
    update = _update()

    await _run(update, _context(session_factory))

    update.message.reply_text.assert_awaited_once_with(
        i18n.t("status.setup", "en", starter="@host")
    )


async def test_silent_outside_the_game_topic(session_factory) -> None:
    update = _update(thread_id=99)
    context = _context(session_factory)

    await _run(update, context)

    update.message.reply_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()


async def test_the_repost_shows_nani_in_the_current_stages_pose(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    poses: list[Pose] = []

    monkeypatch.setattr(
        stage_cosmetics, "stage_png_with", lambda image, _w, _a, _f, look=None: image
    )
    monkeypatch.setattr(
        mascot, "add_to_png", lambda png, pose, costume=None: poses.append(pose) or png
    )
    with session_scope(session_factory) as session:
        _players(session)
        session.add(
            Game(
                starter_id=HOST,
                status=GameStatus.ACTIVE,
                current_stage=PixelStage.STAGE_3,
                original_image=b"img",
            )
        )

    await _run(_update(), _context(session_factory))

    assert poses == [Pose.NOPE]


async def test_a_season_game_is_reposted_in_its_style(
    session_factory, monkeypatch: pytest.MonkeyPatch, fake_runs, log_records
) -> None:
    rendered: list[str | None] = []

    def fake_stage_png_with(image, _width, _algorithm, _frame, look=None) -> bytes:
        assert look is not None
        rendered.append(look.style)
        return image

    monkeypatch.setattr(stage_cosmetics, "stage_png_with", fake_stage_png_with)
    with session_scope(session_factory) as session:
        _players(session)
        season = SeasonSchedule(
            run_id="demo_1",
            start_at=datetime.now(UTC),
            end_at=datetime.now(UTC) + timedelta(days=9),
            status=SeasonStatus.ACTIVE,
            created_by=HOST,
        )
        session.add(season)
        session.flush()
        session.add(
            Game(
                starter_id=HOST,
                status=GameStatus.ACTIVE,
                current_stage=PixelStage.STAGE_1,
                original_image=b"img",
                season_id=season.id,
            )
        )
    context = _context(session_factory)

    await _run(_update(), context)

    assert rendered == [fake_runs["demo_1"].style]
    styled = [r for r in log_records if r.message.startswith("stage image in the season style")]
    assert [(r.level, r.extra["style"]) for r in styled] == [("INFO", "plain")]


async def test_status_reposts_in_the_costume(
    session_factory, monkeypatch: pytest.MonkeyPatch, costume_run
) -> None:
    costumes: list[str | None] = []
    monkeypatch.setattr(
        stage_cosmetics, "stage_png_with", lambda image, _w, _a, _f, look=None: image
    )
    monkeypatch.setattr(
        mascot, "add_to_png", lambda png, pose, costume=None: costumes.append(costume) or png
    )
    with session_scope(session_factory) as session:
        _players(session)
        session.add(
            Game(
                starter_id=HOST,
                status=GameStatus.ACTIVE,
                current_stage=PixelStage.STAGE_3,
                original_image=b"img",
                season_id=activate_season(session),
            )
        )

    await _run(_update(), _context(session_factory))

    assert costumes == [costume_run]


def _haunted_season(session: Session) -> int:
    season = SeasonSchedule(
        run_id="demo_3",
        start_at=datetime.now(UTC) - timedelta(days=1),
        end_at=datetime.now(UTC) + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=HOST,
    )
    session.add(season)
    session.flush()
    return season.id


async def test_status_repost_of_a_haunted_game_is_twisted(
    session_factory, monkeypatch: pytest.MonkeyPatch, fake_runs
) -> None:
    seen: list[tuple[HauntTwist | None, bool]] = []

    def fake_stage_png_with(image, _w, _a, _f, look=None) -> bytes:
        assert look is not None
        assert look.haunt is not None
        seen.append((look.haunt.twist, look.haunt.last))
        return image

    monkeypatch.setattr(stage_cosmetics, "stage_png_with", fake_stage_png_with)
    monkeypatch.setattr(mascot, "add_to_png", lambda png, _pose, _costume=None: png)
    with session_scope(session_factory) as session:
        _players(session)
        session.add(
            Game(
                starter_id=HOST,
                status=GameStatus.ACTIVE,
                current_stage=PixelStage.STAGE_5,
                original_image=b"img",
                season_id=_haunted_season(session),
                haunt_twist="tanuki_leaf",
            )
        )

    await _run(_update(), _context(session_factory))

    assert seen == [(HauntTwist.TANUKI_LEAF, True)]


def test_the_caption_names_a_haunted_twist_its_hint_and_the_multiplier() -> None:
    live = status.LiveStatus(
        game_id=7,
        hard_mode=False,
        number=2,
        total=5,
        remaining=1,
        limit=2,
        pot=0,
        time_left=None,
        twist=HauntTwist.KITSUNE_MIRROR,
        multiplier=3,
    )

    lines = status.caption(live, "en").splitlines()

    assert lines[1] == i18n.t(
        "status.haunted",
        "en",
        twist=i18n.t("haunt.twist.kitsune_mirror", "en"),
        hint=i18n.t("haunt.hint.kitsune_mirror", "en"),
        multiplier=3,
    )


def _live_after_repost(session_factory, **fields) -> status.LiveStatus:
    with session_scope(session_factory) as session:
        _players(session)
        game = Game(
            starter_id=HOST,
            status=GameStatus.ACTIVE,
            original_image=b"img",
            season_id=_haunted_season(session),
            **fields,
        )
        session.add(game)
        session.flush()
        repost = status._repost(session, game, datetime.now(UTC))
        assert repost is not None
        return repost.live


def test_the_live_status_of_a_haunted_game_carries_the_twist_and_multiplier(
    session_factory, fake_runs
) -> None:
    live = _live_after_repost(
        session_factory, current_stage=PixelStage.STAGE_2, haunt_twist="kyokotsu_xray"
    )

    assert live.twist is HauntTwist.KYOKOTSU_XRAY
    assert live.multiplier == fake_runs["demo_3"].haunt.coin_multiplier


def test_a_faded_twist_on_the_last_stage_gives_no_status_line(session_factory, fake_runs) -> None:
    live = _live_after_repost(
        session_factory, current_stage=PixelStage.STAGE_5, haunt_twist="kyokotsu_xray"
    )

    assert live.twist is None


def test_the_mirror_stays_in_the_status_on_the_last_stage(session_factory, fake_runs) -> None:
    live = _live_after_repost(
        session_factory, current_stage=PixelStage.STAGE_5, haunt_twist="kitsune_mirror"
    )

    assert live.twist is HauntTwist.KITSUNE_MIRROR


def test_a_boss_games_status_names_its_current_stage_twist(session_factory, fake_runs) -> None:
    live = _live_after_repost(session_factory, current_stage=PixelStage.STAGE_2, boss_game=True)

    assert live.twist is list(HauntTwist)[1]


def test_an_unhaunted_game_has_no_status_twist(session_factory, fake_runs) -> None:
    live = _live_after_repost(session_factory, current_stage=PixelStage.STAGE_2)

    assert live.twist is None
    assert live.multiplier == 1
