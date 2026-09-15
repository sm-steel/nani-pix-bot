from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.stage_config import StageConfig
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.jikan import JikanResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tmdb import TMDBResult

_FRIEREN = AniListResult(
    anilist_id=99,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren"],
    year=2023,
)

_FRIEREN_SHIKIMORI = ShikimoriResult(
    shikimori_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_russian="Провожающая в последний путь Фрирен",
    synonyms=["Frieren at the Funeral"],
)

_FRIEREN_JIKAN = JikanResult(
    jikan_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren at the Funeral"],
)

_FRIEREN_TMDB = TMDBResult(
    tmdb_id=209867,
    title_romaji=None,
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=[],
)


def test_prioritized_title_prefers_english_for_a_non_ru_lang() -> None:
    variants = game_service.TitleVariants(
        english="Frieren: Beyond Journey's End",
        romaji="Sousou no Frieren",
        native="葬送のフリーレン",
        russian="Провожающая в последний путь Фрирен",
    )

    assert game_service.prioritized_title(variants, lang="EN") == "Frieren: Beyond Journey's End"


def test_prioritized_title_prefers_russian_for_ru_lang() -> None:
    # This is the exact rule commands/dm_start/keyboards.py's result-
    # picker button labels must also use — the same function backs
    # both, so they can't drift apart (see issue #50's follow-up).
    variants = game_service.TitleVariants(
        english="Frieren: Beyond Journey's End",
        romaji="Sousou no Frieren",
        native="葬送のフリーレン",
        russian="Провожающая в последний путь Фрирен",
    )

    assert (
        game_service.prioritized_title(variants, lang="RU") == "Провожающая в последний путь Фрирен"
    )


def test_prioritized_title_falls_back_when_russian_is_unset_for_ru_lang() -> None:
    variants = game_service.TitleVariants(english="Frieren: Beyond Journey's End")

    assert game_service.prioritized_title(variants, lang="RU") == "Frieren: Beyond Journey's End"


def test_prioritized_title_falls_back_to_a_literal_question_mark() -> None:
    assert game_service.prioritized_title(game_service.TitleVariants(), lang="EN") == "?"


def test_display_title_prefers_english_for_a_non_ru_bot() -> None:
    game = Game(
        starter_id=1,
        original_image=b"f",
        title_english="Frieren: Beyond Journey's End",
        title_romaji="Sousou no Frieren",
        title_native="葬送のフリーレン",
        title_russian="Провожающая в последний путь Фрирен",
    )

    assert game_service.display_title(game, "EN") == "Frieren: Beyond Journey's End"


def test_display_title_prefers_russian_for_a_ru_bot() -> None:
    # A RU-language bot's AniList/Shikimori result-picker buttons already
    # prefer the Russian title first (see commands/dm_start/keyboards.py's
    # _shikimori_label) — display_title() must agree, or the confirmation
    # preview shows a different title than the button the starter tapped
    # (see issue #50's follow-up: a mismatched "Grand Blue"/"Grand Blue
    # Dreaming" case surfaced exactly this).
    game = Game(
        starter_id=1,
        original_image=b"f",
        title_english="Frieren: Beyond Journey's End",
        title_romaji="Sousou no Frieren",
        title_native="葬送のフリーレン",
        title_russian="Провожающая в последний путь Фрирен",
    )

    assert game_service.display_title(game, "RU") == "Провожающая в последний путь Фрирен"


def test_display_title_falls_back_to_english_for_a_ru_bot_with_no_russian_title() -> None:
    # An AniList-sourced game never has title_russian set at all — a
    # RU-language bot must still fall back through the rest of the chain
    # rather than landing on "?".
    game = Game(
        starter_id=1,
        original_image=b"f",
        title_english="Frieren: Beyond Journey's End",
        title_romaji="Sousou no Frieren",
    )

    assert game_service.display_title(game, "RU") == "Frieren: Beyond Journey's End"


def test_display_title_falls_back_to_russian_when_only_that_is_set() -> None:
    # A Shikimori-only result has no English/romaji/native title — before
    # consolidating this fallback chain, three of its four copies
    # (commands/guess.py, correct.py, stop.py) skipped straight to "?"
    # here instead of using the Russian title.
    game = Game(
        starter_id=1,
        original_image=b"f",
        title_russian="Провожающая в последний путь Фрирен",
    )

    assert game_service.display_title(game, "EN") == "Провожающая в последний путь Фрирен"


def test_display_title_falls_back_to_a_literal_question_mark() -> None:
    game = Game(starter_id=1, original_image=b"f")

    assert game_service.display_title(game, "EN") == "?"


def test_match_candidates_includes_every_title_variant_and_synonym() -> None:
    game = Game(
        starter_id=1,
        original_image=b"f",
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        title_native="葬送のフリーレン",
        title_russian="Провожающая в последний путь Фрирен",
        synonyms=["Frieren", "Frieren at the Funeral"],
    )

    assert game_service.match_candidates(game) == [
        "Sousou no Frieren",
        "Frieren: Beyond Journey's End",
        "葬送のフリーレン",
        "Провожающая в последний путь Фрирен",
        "Frieren",
        "Frieren at the Funeral",
    ]


def test_match_candidates_omits_unset_fields() -> None:
    # A Shikimori-only result never has title_native set — this is the
    # "Grand Blue" scenario: title_romaji is the only variant besides
    # title_english, and it must still show up.
    game = Game(
        starter_id=1,
        original_image=b"f",
        title_romaji="Grand Blue",
        title_english="Grand Blue Dreaming",
        title_russian="Необъятный океан",
    )

    assert game_service.match_candidates(game) == [
        "Grand Blue",
        "Grand Blue Dreaming",
        "Необъятный океан",
    ]


def test_match_candidates_omits_none_synonyms_list() -> None:
    game = Game(starter_id=1, original_image=b"f", title_english="Some Anime")

    assert game_service.match_candidates(game) == ["Some Anime"]


def test_can_start_is_true_with_no_game_and_no_turn_state(session: Session) -> None:
    assert game_service.can_start(session, user_id=1) is True


def test_can_start_is_false_when_a_game_is_active(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    session.add(Game(starter_id=1, original_image=b"f", status=GameStatus.ACTIVE))
    session.commit()

    assert game_service.can_start(session, user_id=2) is False


def test_can_start_is_false_when_turn_designated_to_someone_else(session: Session) -> None:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
    session.add(TurnState(id=1, next_starter_id=2))
    session.commit()

    assert game_service.can_start(session, user_id=1) is False


def test_can_start_is_true_for_the_designated_starter(session: Session) -> None:
    session.add(Player(telegram_user_id=2))
    session.add(TurnState(id=1, next_starter_id=2))
    session.commit()

    assert game_service.can_start(session, user_id=2) is True


def test_create_setup_game_persists_a_setup_row(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()

    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.original_image == b"file123"
    assert fetched.starter_id == 1


def test_create_setup_game_allows_no_image_yet(session: Session) -> None:
    # The screenshot-less /newgame entry point creates the row before any
    # image exists — it's filled in once a screenshot is picked, later.
    session.add(Player(telegram_user_id=1))
    session.commit()

    game = game_service.create_setup_game(session, starter_id=1)
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.original_image is None


def test_create_setup_game_sets_a_one_hour_setup_deadline(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    before = datetime.now(UTC).replace(tzinfo=None)

    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    assert game.setup_deadline is not None
    delta_seconds = (game.setup_deadline - before).total_seconds()
    assert delta_seconds == pytest.approx(game_service.SETUP_ABANDON_DELAY.total_seconds(), abs=5)


def test_stage_result_assigns_anilist_fields_without_changing_status(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    game_service.stage_result(game, _FRIEREN, source=Provider.ANILIST)
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.anilist_id == 99
    assert fetched.title_romaji == "Sousou no Frieren"
    assert fetched.synonyms == ["Frieren"]
    assert fetched.source == "anilist"
    assert fetched.title_russian is None


def test_stage_result_assigns_shikimori_fields_including_russian_title(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    game_service.stage_result(game, _FRIEREN_SHIKIMORI, source=Provider.SHIKIMORI)
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.anilist_id is None
    assert fetched.shikimori_id == 52991
    assert fetched.title_romaji == "Sousou no Frieren"
    assert fetched.title_russian == "Провожающая в последний путь Фрирен"
    assert fetched.synonyms == ["Frieren at the Funeral"]
    assert fetched.source == "shikimori"


def test_stage_result_assigns_jikan_fields_including_native_title(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    game_service.stage_result(game, _FRIEREN_JIKAN, source=Provider.JIKAN)
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.anilist_id is None
    assert fetched.jikan_id == 52991
    assert fetched.title_romaji == "Sousou no Frieren"
    assert fetched.title_native == "葬送のフリーレン"
    assert fetched.synonyms == ["Frieren at the Funeral"]
    assert fetched.source == "jikan"


def test_stage_result_assigns_tmdb_fields_including_native_title(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    game_service.stage_result(game, _FRIEREN_TMDB, source=Provider.TMDB)
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.anilist_id is None
    assert fetched.tmdb_id == 209867
    assert fetched.title_romaji is None
    assert fetched.title_english == "Frieren: Beyond Journey's End"
    assert fetched.title_native == "葬送のフリーレン"
    assert fetched.synonyms == []
    assert fetched.source == "tmdb"


def test_set_screenshot_provider_id_sets_only_the_ids_column(session: Session) -> None:
    """Cross-provider screenshot resolution (ticket 8): identifying via
    Shikimori, then resolving a TMDB screenshot, must not touch the
    identification fields stage_result() sets — only tmdb_id changes."""
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    game_service.stage_result(game, _FRIEREN_SHIKIMORI, source=Provider.SHIKIMORI)
    session.commit()

    game_service.set_screenshot_provider_id(game, _FRIEREN_TMDB)
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.tmdb_id == 209867
    assert fetched.shikimori_id == 52991
    assert fetched.source == "shikimori"
    assert fetched.title_romaji == "Sousou no Frieren"
    assert fetched.title_russian == "Провожающая в последний путь Фрирен"
    assert fetched.synonyms == ["Frieren at the Funeral"]


def test_set_screenshot_provider_id_handles_each_provider_type(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    game_service.set_screenshot_provider_id(game, _FRIEREN_SHIKIMORI)
    assert game.shikimori_id == 52991

    game_service.set_screenshot_provider_id(game, _FRIEREN_JIKAN)
    assert game.jikan_id == 52991

    game_service.set_screenshot_provider_id(game, _FRIEREN_TMDB)
    assert game.tmdb_id == 209867


def test_stage_manual_entry_assigns_the_typed_title_and_synonyms(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    game_service.stage_manual_entry(
        game, title="Sousou no Frieren", synonyms=["Frieren", "Frieren at the Funeral"]
    )
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.source == "manual"
    assert fetched.title_english == "Sousou no Frieren"
    assert fetched.synonyms == ["Frieren", "Frieren at the Funeral"]
    assert fetched.anilist_id is None


def test_activate_game_sets_active_state_and_opens_the_turn(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.add(TurnState(id=1, next_starter_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    game_service.stage_result(game, _FRIEREN, source=Provider.ANILIST)
    game_service.activate_game(session, game)
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.ACTIVE
    assert fetched.current_stage == PixelStage.STAGE_1
    assert fetched.wrong_guess_count == 0
    assert fetched.anilist_id == 99
    assert fetched.synonyms == ["Frieren"]

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id is None


def test_activate_game_creates_turn_state_row_if_missing(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    game_service.stage_result(game, _FRIEREN, source=Provider.ANILIST)
    game_service.activate_game(session, game)
    session.commit()

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id is None


def test_activate_game_schedules_the_timeout_two_days_out(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()
    before = datetime.now(UTC).replace(tzinfo=None)  # DATETIME columns round-trip as naive UTC

    game_service.stage_result(game, _FRIEREN, source=Provider.ANILIST)
    game_service.activate_game(session, game)
    session.commit()

    assert game.scheduled_end_at is not None
    delta_seconds = (game.scheduled_end_at - before).total_seconds()
    assert delta_seconds == pytest.approx(game_service.TIMEOUT_DURATION.total_seconds(), abs=5)


def test_activate_game_sets_the_initial_inactivity_deadlines(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()
    before = datetime.now(UTC).replace(tzinfo=None)  # DATETIME columns round-trip as naive UTC

    game_service.stage_result(game, _FRIEREN, source=Provider.ANILIST)
    game_service.activate_game(session, game)
    session.commit()

    assert game.inactivity_nudge_at is not None
    assert game.inactivity_advance_at is not None
    nudge_delta = (game.inactivity_nudge_at - before).total_seconds()
    advance_delta = (game.inactivity_advance_at - before).total_seconds()
    assert nudge_delta == pytest.approx(game_service.INACTIVITY_NUDGE_DELAY.total_seconds(), abs=5)
    assert advance_delta == pytest.approx(
        game_service.INACTIVITY_ADVANCE_DELAY.total_seconds(), abs=5
    )


def _active_game(
    session: Session,
    *,
    stage: PixelStage = PixelStage.STAGE_1,
    wrong_guess_count: int = 0,
    total_guess_count: int = 0,
) -> Game:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = Game(
        starter_id=1,
        original_image=b"file123",
        status=GameStatus.ACTIVE,
        current_stage=stage,
        wrong_guess_count=wrong_guess_count,
        total_guess_count=total_guess_count,
        anilist_id=99,
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        synonyms=["Frieren"],
    )
    session.add(game)
    session.commit()
    return game


def _seed_stage_limit(session: Session, stage: PixelStage, wrong_guess_limit: int) -> None:
    """Seeds an explicit stage_config row so a test's expected threshold
    doesn't depend on whatever services/settings/stage_config.py's current
    DEFAULT_STAGE_CONFIG happens to be (which gets retuned often)."""
    session.add(StageConfig(stage=stage, target_width=100, wrong_guess_limit=wrong_guess_limit))
    session.commit()


def test_record_guess_correct_marks_the_game_won(session: Session) -> None:
    game = _active_game(session)
    session.add(Player(telegram_user_id=2))
    session.commit()

    outcome = game_service.record_guess(session, game, guesser_id=2, guess_text="frieren")
    session.commit()

    assert outcome is game_service.GuessOutcome.WON
    assert game.status == GameStatus.WON
    assert game.winner_id == 2

    winner = session.get(Player, 2)
    assert winner is not None
    assert winner.wins == 1

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id == 2


def test_record_guess_matches_the_russian_title(session: Session) -> None:
    game = _active_game(session)
    game.title_russian = "Провожающая в последний путь Фрирен"
    session.add(Player(telegram_user_id=2))
    session.commit()

    outcome = game_service.record_guess(
        session, game, guesser_id=2, guess_text="Провожающая в последний путь Фрирен"
    )
    session.commit()

    assert outcome is game_service.GuessOutcome.WON


def test_record_guess_wrong_increments_the_counter_without_advancing(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_4, wrong_guess_count=3)
    _seed_stage_limit(session, PixelStage.STAGE_4, wrong_guess_limit=5)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.WRONG
    assert game.status == GameStatus.ACTIVE
    assert game.current_stage == PixelStage.STAGE_4
    assert game.wrong_guess_count == 4


def test_record_guess_advances_stage_when_a_stages_threshold_is_reached(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_4, wrong_guess_count=4)
    _seed_stage_limit(session, PixelStage.STAGE_4, wrong_guess_limit=5)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.status == GameStatus.ACTIVE
    assert game.current_stage == PixelStage.STAGE_5
    assert game.wrong_guess_count == 0


def test_record_guess_ends_unsolved_after_final_stage_exhaustion(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_5, wrong_guess_count=7)
    _seed_stage_limit(session, PixelStage.STAGE_5, wrong_guess_limit=8)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.UNSOLVED
    assert game.status == GameStatus.UNSOLVED


def test_record_guess_stage_1_advances_immediately_on_first_wrong_guess(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_1, wrong_guess_count=0)
    _seed_stage_limit(session, PixelStage.STAGE_1, wrong_guess_limit=1)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.current_stage == PixelStage.STAGE_2
    assert game.wrong_guess_count == 0


def test_record_guess_stage_2_advances_immediately_on_first_wrong_guess(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_2, wrong_guess_count=0)
    _seed_stage_limit(session, PixelStage.STAGE_2, wrong_guess_limit=1)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.current_stage == PixelStage.STAGE_3
    assert game.wrong_guess_count == 0


def test_record_guess_stage_3_stays_below_its_three_guess_threshold(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_3, wrong_guess_count=1)
    _seed_stage_limit(session, PixelStage.STAGE_3, wrong_guess_limit=3)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.WRONG
    assert game.current_stage == PixelStage.STAGE_3
    assert game.wrong_guess_count == 2


def test_record_guess_stage_3_advances_on_its_third_wrong_guess(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_3, wrong_guess_count=2)
    _seed_stage_limit(session, PixelStage.STAGE_3, wrong_guess_limit=3)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.current_stage == PixelStage.STAGE_4
    assert game.wrong_guess_count == 0


def test_clear_original_screenshot_nulls_the_file_id(session: Session) -> None:
    game = _active_game(session)

    game_service.clear_original_screenshot(game)
    session.commit()

    assert game.original_image is None


def test_record_guess_rejects_a_game_with_no_current_stage(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = Game(
        starter_id=1,
        original_image=b"file123",
        status=GameStatus.SETUP,
        current_stage=None,
    )
    session.add(game)
    session.commit()

    with pytest.raises(ValueError, match="current_stage"):
        game_service.record_guess(session, game, guesser_id=1, guess_text="anything")


def test_stage_progress_reports_stage_number_total_and_remaining(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_3, wrong_guess_count=1)
    _seed_stage_limit(session, PixelStage.STAGE_3, wrong_guess_limit=3)

    progress = game_service.stage_progress(session, game)

    assert progress.number == 3
    assert progress.total == 5
    assert progress.remaining == 2  # STAGE_3's limit is 3, minus 1 wrong guess so far
    assert progress.limit == 3


def test_stage_progress_reports_the_full_limit_on_a_freshly_entered_stage(
    session: Session,
) -> None:
    """remaining == limit right after an advance — what the stage-advance
    caption shows as "2/2"."""
    game = _active_game(session, stage=PixelStage.STAGE_3, wrong_guess_count=0)
    _seed_stage_limit(session, PixelStage.STAGE_3, wrong_guess_limit=2)

    progress = game_service.stage_progress(session, game)

    assert (progress.remaining, progress.limit) == (2, 2)


def test_stage_progress_rejects_a_game_with_no_current_stage(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = Game(
        starter_id=1,
        original_image=b"file123",
        status=GameStatus.SETUP,
        current_stage=None,
    )
    session.add(game)
    session.commit()

    with pytest.raises(ValueError, match="current_stage"):
        game_service.stage_progress(session, game)


def test_has_answer_to_reveal_is_true_for_an_active_game_with_its_image(session: Session) -> None:
    game = _active_game(session)

    assert game_service.has_answer_to_reveal(game) is True


def test_has_answer_to_reveal_is_false_for_a_setup_game(session: Session) -> None:
    """A SETUP round has never posted anything to the group topic, so
    there's no answer anyone is waiting on."""
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = Game(starter_id=1, original_image=b"file123", status=GameStatus.SETUP)
    session.add(game)
    session.commit()

    assert game_service.has_answer_to_reveal(game) is False


def test_has_answer_to_reveal_is_false_once_the_image_is_cleared(session: Session) -> None:
    """clear_original_screenshot() only ever runs on a game that has just
    reached a terminal outcome — every caller (guess.py, correct.py,
    jobs/timers.py) posts the reveal and drops the bytes in the same
    breath — so "the image is gone" and "the round is over" are one
    state, and the status half alone answers for both."""
    game = _active_game(session)
    game_service.force_unsolved(game)
    game_service.clear_original_screenshot(game)

    assert game_service.has_answer_to_reveal(game) is False


def test_has_answer_to_reveal_does_not_load_the_deferred_image(session: Session) -> None:
    """models/game.py defers original_image so routine queries don't drag
    a multi-hundred-KB blob along; a bare `is not None` on it would
    force-load exactly that, to learn one bit. Same discipline
    _validate_guess applies on the /guess hot path.

    `unloaded` is the proof rather than a mock: the attribute is only
    absent from it once SQLAlchemy has actually fetched the column."""
    game_id = _active_game(session).id
    session.expunge_all()
    game = session.get(Game, game_id)
    assert game is not None
    assert "original_image" in inspect(game).unloaded  # deferred, as declared

    assert game_service.has_answer_to_reveal(game) is True

    assert "original_image" in inspect(game).unloaded


def test_force_win_sets_winner_and_hands_over_the_turn(session: Session) -> None:
    game = _active_game(session)
    session.add(Player(telegram_user_id=2))
    session.commit()

    game_service.force_win(session, game, winner_id=2)
    session.commit()

    assert game.status == GameStatus.WON
    assert game.winner_id == 2

    winner = session.get(Player, 2)
    assert winner is not None
    assert winner.wins == 1

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id == 2


def test_force_unsolved_sets_the_status(session: Session) -> None:
    game = _active_game(session)

    game_service.force_unsolved(game)
    session.commit()

    assert game.status == GameStatus.UNSOLVED


def test_win_schedules_turn_timers_for_the_winner(session: Session) -> None:
    game = _active_game(session)
    session.add(Player(telegram_user_id=2))
    session.commit()

    game_service.record_guess(session, game, guesser_id=2, guess_text="frieren")
    session.commit()

    turn_state = game_service.get_turn_state(session)
    assert turn_state is not None
    assert turn_state.next_starter_id == 2
    assert turn_state.reminder_at is not None
    assert turn_state.expiry_at is not None


def test_active_games_returns_only_active_status_games(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    session.add_all(
        [
            Game(starter_id=1, original_image=b"f", status=GameStatus.ACTIVE),
            Game(starter_id=1, original_image=b"f", status=GameStatus.WON),
            Game(starter_id=1, original_image=b"f", status=GameStatus.SETUP),
        ]
    )
    session.commit()

    active = game_service.active_games(session)

    assert len(active) == 1
    assert active[0].status == GameStatus.ACTIVE


def test_get_setup_game_for_starter_finds_the_pending_row(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    found = game_service.get_setup_game_for_starter(session, 1)

    assert found is not None
    assert found.id == game.id


def test_get_setup_game_for_starter_ignores_other_starters(session: Session) -> None:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
    session.commit()
    game_service.create_setup_game(session, starter_id=1, original_image=b"file123")
    session.commit()

    assert game_service.get_setup_game_for_starter(session, 2) is None


def test_get_setup_game_for_starter_ignores_active_games(session: Session) -> None:
    game = _active_game(session)

    assert game_service.get_setup_game_for_starter(session, game.starter_id) is None


def test_record_guess_increments_total_guess_count_on_win(session: Session) -> None:
    game = _active_game(session)
    session.add(Player(telegram_user_id=2))
    session.commit()

    game_service.record_guess(session, game, guesser_id=2, guess_text="frieren")
    session.commit()

    assert game.total_guess_count == 1


def test_record_guess_increments_total_guess_count_on_wrong_guess(session: Session) -> None:
    game = _active_game(session)

    game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert game.total_guess_count == 1


def test_record_guess_total_guess_count_survives_a_stage_advance(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_4, wrong_guess_count=4, total_guess_count=4)
    _seed_stage_limit(session, PixelStage.STAGE_4, wrong_guess_limit=5)

    game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert game.wrong_guess_count == 0
    assert game.total_guess_count == 5


def test_reset_inactivity_clock_sets_both_deadlines_from_now(session: Session) -> None:
    game = _active_game(session)
    before = datetime.now(UTC)  # not committed/refetched, so still tz-aware unlike scheduled_end_at

    game_service.reset_inactivity_clock(game)

    assert game.inactivity_nudge_at is not None
    assert game.inactivity_advance_at is not None
    nudge_delta = (game.inactivity_nudge_at - before).total_seconds()
    advance_delta = (game.inactivity_advance_at - before).total_seconds()
    assert nudge_delta == pytest.approx(game_service.INACTIVITY_NUDGE_DELAY.total_seconds(), abs=5)
    assert advance_delta == pytest.approx(
        game_service.INACTIVITY_ADVANCE_DELAY.total_seconds(), abs=5
    )


def test_clear_inactivity_nudge_clears_only_the_nudge_deadline(session: Session) -> None:
    game = _active_game(session)
    game_service.reset_inactivity_clock(game)
    advance_at_before = game.inactivity_advance_at

    game_service.clear_inactivity_nudge(game)

    assert game.inactivity_nudge_at is None
    # Untouched — the auto-advance timer keeps counting toward its own
    # 6h deadline independently of the one-shot nudge.
    assert game.inactivity_advance_at == advance_at_before


def test_advance_stage_moves_to_the_next_stage_and_resets_wrong_guess_count(
    session: Session,
) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_2, wrong_guess_count=3)

    outcome = game_service.advance_stage(game)

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.current_stage == PixelStage.STAGE_3
    assert game.wrong_guess_count == 0
    assert game.status == GameStatus.ACTIVE


def test_advance_stage_ends_unsolved_when_already_on_the_final_stage(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_5, wrong_guess_count=7)

    outcome = game_service.advance_stage(game)

    assert outcome is game_service.GuessOutcome.UNSOLVED
    assert game.status == GameStatus.UNSOLVED
    assert game.current_stage == PixelStage.STAGE_5  # left as-is, only status changes


def test_advance_stage_raises_runtime_error_when_current_stage_is_none(
    session: Session,
) -> None:
    """Genuine internal-invariant guard (issue #117): every real caller
    (record_guess, jobs/timers.py's inactivity-advance callback) already
    checks current_stage before calling in, so this path is never
    reachable by a player — but it used to be a bare `assert`, silently
    stripped under `python -O`. Now it's a real RuntimeError."""
    game = _active_game(session)
    game.current_stage = None

    with pytest.raises(RuntimeError, match="no current_stage"):
        game_service.advance_stage(game)


def test_record_guess_wrong_guess_limit_hit_delegates_to_advance_stage(session: Session) -> None:
    # record_guess's own stage-exhaustion tests above already cover the
    # observable behavior; this just pins that it's the same advance_stage()
    # a future inactivity-advance job callback will call, not a second copy.
    game = _active_game(session, stage=PixelStage.STAGE_4, wrong_guess_count=4)
    _seed_stage_limit(session, PixelStage.STAGE_4, wrong_guess_limit=5)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.current_stage == PixelStage.STAGE_5
