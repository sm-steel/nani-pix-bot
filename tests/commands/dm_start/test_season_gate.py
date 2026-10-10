"""The season gate on DM setup (seasons spec §5): checked on a catalogue
pick and again, authoritatively, at the preview's Confirm."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start import preview, search, season_gate
from nani_pix_bot.commands.dm_start.keyboards import (
    PREVIEW_CONFIRM_CALLBACK_DATA,
    back_to_methods_keyboard,
)
from nani_pix_bot.models.enums import GameStatus, Provider, SeasonStatus, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.search import aliases
from nani_pix_bot.services.search import tags as anime_tags
from nani_pix_bot.services.search.tags import AnimeTag
from nani_pix_bot.services.search.tenrai import TenraiResult
from nani_pix_bot.services.seasons import gate as gate_service
from nani_pix_bot.services.seasons.gate import Verdict
from nani_pix_bot.services.seasons.tags import TagsUnavailableError

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
ROMANCE = [AnimeTag("genre", "Romance", 22), AnimeTag("genre", "Drama", 8)]
GURREN = [AnimeTag("genre", "Sci-Fi", 24), AnimeTag("theme", "Mecha", 18)]
_GURREN_TENRAI = TenraiResult(
    tenrai_id=2001,
    title_romaji="Tengen Toppa Gurren Lagann",
    title_english="Gurren Lagann",
    title_native=None,
    synonyms=[],
)


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)


@pytest.fixture
def active_season(session_factory) -> None:
    with session_factory() as session:
        session.add(
            SeasonSchedule(
                run_id="demo_1",
                start_at=NOW,
                end_at=NOW,
                status=SeasonStatus.ACTIVE,
                created_by=7,
            )
        )
        session.commit()


def _patch_tags(monkeypatch: pytest.MonkeyPatch, tags=None, *, error=None) -> AsyncMock:
    fetch = AsyncMock(return_value=tags, side_effect=error)
    monkeypatch.setattr(gate_service, "fetch_tags", fetch)
    return fetch


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
        "search_client": MagicMock(),
        "tenrai_client": MagicMock(),
        "tmdb_client": MagicMock(),
    }
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.MEMBER))
    context.bot.send_message = AsyncMock()
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    context.bot.send_media_group = AsyncMock()
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    context.job_queue.get_jobs_by_name.return_value = []
    return context


def _callback_update(data: str, *, user_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.from_user.full_name = "Starter Name"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _setup_game(session_factory, *, step: SetupStep = SetupStep.CONFIRMING, **fields) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.commit()
        game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
        game.setup_step = step
        for key, value in fields.items():
            setattr(game, key, value)
        session.commit()
        return game.id


def _game(session_factory, game_id: int) -> Game:
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        session.expunge(game)
        return game


def _tenrai_game(session_factory, tenrai_id: int, title: str) -> int:
    return _setup_game(
        session_factory, source=Provider.TENRAI, tenrai_id=tenrai_id, title_english=title
    )


async def _run(session_factory) -> Verdict:
    context = cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
    return await season_gate.run_gate(context, session_factory, 1)


@pytest.mark.usefixtures("active_season")
async def test_off_theme_pick_is_refused_and_cleared(session_factory, monkeypatch) -> None:
    _patch_tags(monkeypatch, GURREN)
    game_id = _tenrai_game(session_factory, 2001, "Gurren Lagann")

    assert await _run(session_factory) is Verdict.OFF_THEME

    game = _game(session_factory, game_id)
    assert game.title_english is None
    assert game.tenrai_id is None
    assert game.anime_tags is None
    assert game.setup_step == SetupStep.PICKING_METHOD


@pytest.mark.usefixtures("active_season")
async def test_unidentifiable_title_is_refused(session_factory, monkeypatch) -> None:
    fetch = _patch_tags(monkeypatch, ROMANCE)
    monkeypatch.setattr(aliases, "mal_id", AsyncMock(return_value=None))
    game_id = _setup_game(session_factory, source="manual", title_english="Some Unknown Title")

    assert await _run(session_factory) is Verdict.UNIDENTIFIABLE

    fetch.assert_not_awaited()
    assert _game(session_factory, game_id).title_english is None


@pytest.mark.usefixtures("active_season")
async def test_unavailable_keeps_identification(session_factory, monkeypatch) -> None:
    _patch_tags(monkeypatch, error=TagsUnavailableError())
    game_id = _tenrai_game(session_factory, 4224, "Toradora!")

    assert await _run(session_factory) is Verdict.UNAVAILABLE

    game = _game(session_factory, game_id)
    assert game.title_english == "Toradora!"
    assert game.anime_tags is None


@pytest.mark.usefixtures("active_season")
async def test_a_failed_mal_id_lookup_is_unavailable(session_factory, monkeypatch) -> None:
    _patch_tags(monkeypatch, ROMANCE)
    monkeypatch.setattr(aliases, "mal_id", AsyncMock(side_effect=RuntimeError("down")))
    game_id = _setup_game(session_factory, source="manual", title_english="Toradora!")

    assert await _run(session_factory) is Verdict.UNAVAILABLE
    assert _game(session_factory, game_id).title_english == "Toradora!"


@pytest.mark.usefixtures("active_season")
async def test_passing_pick_stores_tags(session_factory, monkeypatch, log_records) -> None:
    fetch = _patch_tags(monkeypatch, ROMANCE)
    game_id = _tenrai_game(session_factory, 4224, "Toradora!")

    assert await _run(session_factory) is Verdict.PASSED

    fetch.assert_awaited_once_with(ANY, 4224)
    game = _game(session_factory, game_id)
    assert game.anime_tags == anime_tags.to_json(ROMANCE)
    assert game.title_english == "Toradora!"
    line = next(r for r in log_records if r.message.startswith("season gate:"))
    assert line.level == "INFO"
    assert line.extra["verdict"] == "passed"
    assert line.extra["game_id"] == game_id


@pytest.mark.usefixtures("active_season")
async def test_known_tags_are_rechecked_without_fetching(session_factory, monkeypatch) -> None:
    fetch = _patch_tags(monkeypatch, ROMANCE)
    game_id = _tenrai_game(session_factory, 2001, "Gurren Lagann")
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        game.anime_tags = anime_tags.to_json(GURREN)
        session.commit()

    assert await _run(session_factory) is Verdict.OFF_THEME
    fetch.assert_not_awaited()


async def test_no_season_means_no_gate(session_factory, monkeypatch) -> None:
    fetch = _patch_tags(monkeypatch, GURREN)
    game_id = _tenrai_game(session_factory, 2001, "Gurren Lagann")

    assert await _run(session_factory) is Verdict.PASSED

    fetch.assert_not_awaited()
    assert _game(session_factory, game_id).title_english == "Gurren Lagann"


def test_refusal_text_names_the_rule() -> None:
    text = season_gate.refusal_text(Verdict.OFF_THEME, "Romance", "EN")
    assert "Romance" in text
    assert text == i18n.t("season.gate.off_theme", "EN", rule="Romance")


@pytest.mark.usefixtures("active_season")
def test_rule_text_is_the_gate_description_in_the_language(session_factory) -> None:
    assert season_gate.rule_text(session_factory, "RU") == "Романтика"
    assert season_gate.rule_text(session_factory, "EN") == "Romance"


@pytest.mark.usefixtures("active_season")
async def test_off_theme_catalogue_pick_is_refused_with_a_way_back(
    session_factory, monkeypatch, background_tasks
) -> None:
    _patch_tags(monkeypatch, GURREN)
    monkeypatch.setattr(search.tenrai, "get_by_id", AsyncMock(return_value=_GURREN_TENRAI))
    game_id = _setup_game(session_factory, step=SetupStep.PICKING_METHOD, source=Provider.TENRAI)
    update = _callback_update("tenrai_pick:2001")
    context = _context(session_factory)

    await search.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    query = update.callback_query
    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_awaited_once_with(
        season_gate.refusal_text(Verdict.OFF_THEME, "Romance", "EN"),
        reply_markup=back_to_methods_keyboard("EN"),
    )
    assert background_tasks == []  # no alias search, no preview
    context.bot.send_media_group.assert_not_awaited()
    game = _game(session_factory, game_id)
    assert game.tenrai_id is None
    assert game.setup_step == SetupStep.PICKING_METHOD


@pytest.mark.usefixtures("active_season")
async def test_unavailable_catalogue_pick_carries_on(
    session_factory, monkeypatch, background_tasks
) -> None:
    _patch_tags(monkeypatch, error=TagsUnavailableError())
    monkeypatch.setattr(search.tenrai, "get_by_id", AsyncMock(return_value=_GURREN_TENRAI))
    game_id = _setup_game(session_factory, step=SetupStep.PICKING_METHOD, source=Provider.TENRAI)
    update = _callback_update("tenrai_pick:2001")

    await search.pick_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
    )

    update.callback_query.answer.assert_awaited_once_with()
    update.callback_query.edit_message_text.assert_awaited_once_with(
        i18n.t("dm_start.preview_sent", "EN")
    )
    assert len(background_tasks) == 1
    assert _game(session_factory, game_id).tenrai_id == 2001


@pytest.mark.usefixtures("active_season")
async def test_confirm_refusal_edits_the_preview_with_back_button(
    session_factory, monkeypatch
) -> None:
    _patch_tags(monkeypatch, GURREN)
    game_id = _tenrai_game(session_factory, 2001, "Gurren Lagann")
    update = _callback_update(PREVIEW_CONFIRM_CALLBACK_DATA)
    context = _context(session_factory)

    await preview.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    query = update.callback_query
    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_awaited_once_with(
        season_gate.refusal_text(Verdict.OFF_THEME, "Romance", "EN"),
        reply_markup=back_to_methods_keyboard("EN"),
    )
    context.bot.send_photo.assert_not_awaited()
    game = _game(session_factory, game_id)
    assert game.status == GameStatus.SETUP
    assert game.setup_step == SetupStep.PICKING_METHOD


@pytest.mark.usefixtures("active_season")
async def test_confirm_while_catalogues_are_down_alerts_and_keeps_the_preview(
    session_factory, monkeypatch
) -> None:
    _patch_tags(monkeypatch, error=TagsUnavailableError())
    game_id = _tenrai_game(session_factory, 4224, "Toradora!")
    update = _callback_update(PREVIEW_CONFIRM_CALLBACK_DATA)
    context = _context(session_factory)

    await preview.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    query = update.callback_query
    query.answer.assert_awaited_once_with(i18n.t("season.gate.unavailable", "EN"), show_alert=True)
    query.edit_message_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()
    game = _game(session_factory, game_id)
    assert game.status == GameStatus.SETUP
    assert game.setup_step == SetupStep.CONFIRMING
    assert game.title_english == "Toradora!"


@pytest.mark.usefixtures("active_season")
async def test_confirm_of_an_on_theme_game_starts_it(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(preview.pixelate_service, "pixelate", lambda *_: b"pixelated")
    _patch_tags(monkeypatch, ROMANCE)
    game_id = _tenrai_game(session_factory, 4224, "Toradora!")
    update = _callback_update(PREVIEW_CONFIRM_CALLBACK_DATA)
    context = _context(session_factory)

    await preview.preview_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.callback_query.answer.assert_awaited_once_with()
    update.callback_query.edit_message_text.assert_awaited_once_with(
        text=i18n.t("dm_start.posted", "EN")
    )
    context.bot.send_photo.assert_awaited_once()
    assert _game(session_factory, game_id).status == GameStatus.ACTIVE
