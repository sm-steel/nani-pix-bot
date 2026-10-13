"""stage_result keeps tags a Tenrai result already carries (#426), so the
season gate needn't fetch them again."""

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.search import tags as anime_tags
from nani_pix_bot.services.search.tags import AnimeTag
from nani_pix_bot.services.search.tenrai import TenraiResult

ROMANCE = (AnimeTag("genre", "Romance", 22),)


def _result(tags) -> TenraiResult:
    return TenraiResult(
        tenrai_id=4224,
        title_romaji="Toradora!",
        title_english=None,
        title_native=None,
        synonyms=[],
        tags=tags,
    )


def test_a_tenrai_result_with_tags_stores_them() -> None:
    game = Game(starter_id=1)
    game_service.stage_result(game, _result(ROMANCE), source=Provider.TENRAI)
    assert anime_tags.from_json(game.anime_tags) == list(ROMANCE)


def test_a_result_without_tags_leaves_them_unknown() -> None:
    game = Game(starter_id=1, anime_tags=anime_tags.to_json(list(ROMANCE)))
    game_service.stage_result(game, _result(None), source=Provider.TENRAI)
    assert game.anime_tags is None
