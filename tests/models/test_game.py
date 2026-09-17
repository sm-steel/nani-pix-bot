from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import (
    DEFAULT_ALGORITHM,
    GameStatus,
    PixelAlgorithm,
    PixelStage,
    Provider,
    SetupStep,
)
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player


def _make_starter(session: Session, telegram_user_id: int = 1) -> Player:
    starter = Player(telegram_user_id=telegram_user_id)
    session.add(starter)
    session.commit()
    return starter


def test_new_game_defaults_to_setup_with_no_wrong_guesses(session: Session) -> None:
    starter = _make_starter(session)
    game = Game(starter_id=starter.telegram_user_id, original_image=b"file123")
    session.add(game)
    session.commit()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.current_stage is None
    assert fetched.wrong_guess_count == 0
    assert fetched.winner_id is None
    assert fetched.created_at is not None
    assert fetched.setup_step == SetupStep.PICKING_METHOD
    assert fetched.setup_deadline is None
    assert fetched.inactivity_nudge_at is None
    assert fetched.inactivity_advance_at is None
    assert fetched.shikimori_id is None
    assert fetched.jikan_id is None
    assert fetched.tmdb_id is None
    assert fetched.screenshot_source is None
    assert fetched.screenshot_picker_provider is None


def test_game_can_be_created_with_no_image_yet(session: Session) -> None:
    # The screenshot-less /newgame entry point creates a SETUP row before
    # any image exists — original_image must be genuinely optional.
    starter = _make_starter(session)
    game = Game(starter_id=starter.telegram_user_id)
    session.add(game)
    session.commit()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.original_image is None


def test_game_stores_provider_ids_independently(session: Session) -> None:
    starter = _make_starter(session)
    game = Game(
        starter_id=starter.telegram_user_id,
        shikimori_id=52991,
        jikan_id=123,
        tmdb_id=456,
        screenshot_source="tmdb",
    )
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.shikimori_id == 52991
    assert fetched.jikan_id == 123
    assert fetched.tmdb_id == 456
    assert fetched.screenshot_source == "tmdb"


def test_game_picker_provider_does_not_claim_an_image_source(session: Session) -> None:
    # The two meanings live in two columns: the screenshot picker being
    # mid-resolution on a provider says nothing about where the stored
    # image came from — and with no image at all, screenshot_source has
    # to stay None. Before the split, one column meant both, which is
    # what let a genuine upload clear an identification provider id.
    starter = _make_starter(session)
    game = Game(
        starter_id=starter.telegram_user_id,
        shikimori_id=52991,
        screenshot_picker_provider="shikimori",
    )
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.screenshot_picker_provider == "shikimori"
    assert fetched.screenshot_source is None
    assert fetched.original_image is None
    assert fetched.shikimori_id == 52991


def test_provider_columns_store_the_value_not_the_member_name(session: Session) -> None:
    """The three `Provider`-typed columns must stay `String`-backed.

    Typing them as `Mapped[Provider | ...]` without keeping the explicit
    `mapped_column(String(...))` argument would let SQLAlchemy auto-infer
    a native `sa.Enum(Provider)` column instead — which stores each
    member's *name* (`"SHIKIMORI"`) rather than its *value*
    (`"shikimori"`), silently reinterpreting every existing row with no
    migration to match (`GameStatus`/`PixelStage`/`SetupStep` genuinely
    do get native `sa.Enum` columns; these three deliberately must not).

    The assertion has to read the **raw** column, not the ORM attribute:
    `Provider` is a `StrEnum`, so a native-enum column would hand back
    `Provider.SHIKIMORI`, which compares equal to `"shikimori"` anyway —
    an ORM-level round trip would pass either way and prove nothing."""
    starter = _make_starter(session)
    game = Game(
        starter_id=starter.telegram_user_id,
        source=Provider.SHIKIMORI,
        screenshot_source=Provider.JIKAN,
        screenshot_picker_provider=Provider.TMDB,
    )
    session.add(game)
    session.commit()
    session.expire_all()

    stored = session.execute(
        text(
            "SELECT source, screenshot_source, screenshot_picker_provider FROM games WHERE id = :id"
        ),
        {"id": game.id},
    ).one()
    assert stored == ("shikimori", "jikan", "tmdb")

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.source == Provider.SHIKIMORI
    assert fetched.screenshot_source == Provider.JIKAN
    assert fetched.screenshot_picker_provider == Provider.TMDB


def test_game_source_defaults_to_the_anilist_value(session: Session) -> None:
    # The column default is `Provider.ANILIST`, but the migration wrote
    # the bare string "anilist" as this column's server_default — so the
    # member has to still land as its lowercase value, the same
    # name-vs-value trap as the round trip above, reached without any
    # caller passing a `Provider` at all. Asserted on the raw column for
    # the same reason: `fetched.source == Provider.ANILIST` alone would
    # hold even if the member name had been stored.
    starter = _make_starter(session)
    game = Game(starter_id=starter.telegram_user_id)
    session.add(game)
    session.commit()
    session.expire_all()

    stored = session.execute(
        text("SELECT source FROM games WHERE id = :id"), {"id": game.id}
    ).scalar_one()
    assert stored == "anilist"

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.source == Provider.ANILIST


def test_game_source_still_accepts_manual(session: Session) -> None:
    # "manual" means "no automatic provider", not a provider identity, so
    # it is deliberately outside `Provider` — and still a legal value of
    # this column (services/game/state.py's stage_manual_entry writes it).
    starter = _make_starter(session)
    game = Game(starter_id=starter.telegram_user_id, source="manual")
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.source == "manual"


def test_game_original_image_round_trips_binary_data(session: Session) -> None:
    starter = _make_starter(session)
    image_bytes = bytes(range(256))  # exercise every byte value, not just ASCII
    game = Game(starter_id=starter.telegram_user_id, original_image=image_bytes)
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.original_image == image_bytes


def test_game_original_image_is_deferred_loaded(session: Session) -> None:
    # Routine queries (status checks, the /guess hot path) shouldn't pull
    # a multi-hundred-KB blob every time — see models/game.py's docstring
    # on original_image.
    starter = _make_starter(session)
    game = Game(starter_id=starter.telegram_user_id, original_image=b"some bytes")
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert "original_image" in inspect(fetched).unloaded

    # Accessing it still works — deferred just means "not loaded eagerly."
    assert fetched.original_image == b"some bytes"
    assert "original_image" not in inspect(fetched).unloaded


def test_game_stores_synonyms_as_a_json_list(session: Session) -> None:
    starter = _make_starter(session)
    game = Game(
        starter_id=starter.telegram_user_id,
        original_image=b"file123",
        anilist_id=99,
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        synonyms=["Frieren", "Frieren at the Funeral"],
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
    )
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.synonyms == ["Frieren", "Frieren at the Funeral"]
    assert fetched.current_stage == PixelStage.STAGE_1


def test_game_winner_references_a_different_player(session: Session) -> None:
    starter = _make_starter(session, telegram_user_id=1)
    winner = _make_starter(session, telegram_user_id=2)
    game = Game(
        starter_id=starter.telegram_user_id,
        original_image=b"file123",
        status=GameStatus.WON,
        winner_id=winner.telegram_user_id,
    )
    session.add(game)
    session.commit()
    session.expire_all()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.winner_id == winner.telegram_user_id
    assert fetched.starter_id == starter.telegram_user_id


def test_new_game_defaults_to_the_default_pixel_algorithm(session: Session) -> None:
    """The starter can override this from the confirmation preview, but a
    game that never touches the picker must still render with the
    project-wide default rather than whatever the enum declares first."""
    starter = _make_starter(session)
    game = Game(starter_id=starter.telegram_user_id, original_image=b"file123")
    session.add(game)
    session.commit()

    fetched = session.get(Game, game.id)

    assert fetched is not None
    assert fetched.pixel_algorithm is DEFAULT_ALGORITHM
    assert fetched.pixel_algorithm is PixelAlgorithm.MEDIAN


def test_game_remembers_a_chosen_pixel_algorithm(session: Session) -> None:
    """Every stage after the first is rendered later, from a fresh
    session — so the starter's pick has to survive on the row rather than
    live in the handler that took it."""
    starter = _make_starter(session)
    game = Game(
        starter_id=starter.telegram_user_id,
        original_image=b"file123",
        pixel_algorithm=PixelAlgorithm.LANCZOS,
    )
    session.add(game)
    session.commit()
    game_id = game.id
    session.expunge_all()

    fetched = session.get(Game, game_id)

    assert fetched is not None
    assert fetched.pixel_algorithm is PixelAlgorithm.LANCZOS
