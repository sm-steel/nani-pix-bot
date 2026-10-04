"""The image clues of the DM shop: the extra pixelated screenshot (fetched
and rendered before anyone is charged) and the revealed-tiles picture."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram import User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _IMAGE_DOWNLOAD_ERRORS,
    _SEARCH_SERVICE_ERRORS,
    client_for_source,
)
from nani_pix_bot.models.enums import ClueKind, PixelAlgorithm, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services import players, settings
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.clues.shop import Offer, Refusal, ShopRefusedError
from nani_pix_bot.services.pixelate import render
from nani_pix_bot.services.settings import stage_config


@dataclass(frozen=True)
class ScreenshotPlan:
    """Everything the network step needs, read in one short session."""

    lang: str
    excluded: frozenset[str]
    sources: tuple[tuple[Provider, int], ...]
    width: int
    algorithm: PixelAlgorithm


@dataclass(frozen=True)
class FetchedScreenshot:
    url: str
    pixelated: bytes


def stage_width(session: Session, game: Game) -> int:
    if game.hard_mode:
        return game_service.hard_mode_turn_width(game)
    if game.current_stage is None:
        raise RuntimeError("game.current_stage is None on an active non-hard-mode game")
    return stage_config.get_stage_config(session, game.current_stage).target_width


def offered_game(
    session: Session, user: User, game_id: int, kind: ClueKind
) -> tuple[Game, str, Offer]:
    """The active game, the language and the offer, iff the shop still
    offers `kind` to this player; otherwise raises the refusal a purchase
    would. Affordability is left to the caller (`offer.affordable`)."""
    game = shop.active_game_for(session, game_id)
    if game is None:
        raise ShopRefusedError(Refusal.NO_GAME)
    buyer = players.get_or_create_player(session, user.id, username=user.username)
    if buyer.telegram_user_id == game.starter_id:
        raise ShopRefusedError(Refusal.SETTER)
    lang = settings.get_language(session)
    offer = next((o for o in shop.offers(session, game, buyer, lang) if o.kind is kind), None)
    if offer is None:
        raise ShopRefusedError(Refusal.UNAVAILABLE)
    return game, lang, offer


def screenshot_plan(session: Session, user: User, game_id: int) -> ScreenshotPlan:
    game, lang, offer = offered_game(session, user, game_id, ClueKind.SCREENSHOT)
    if not offer.affordable:
        raise ShopRefusedError(Refusal.INSUFFICIENT)
    excluded = set(game.shown_screenshot_urls or []) | shop.owned_screenshot_urls(
        session, game.id, user.id
    )
    sources = tuple(
        (provider, getattr(game, provider.id_attr_name))
        for provider in game_service.screenshot_capable_providers(game)
        if getattr(game, provider.id_attr_name)
    )
    width = stage_width(session, game)
    return ScreenshotPlan(lang, frozenset(excluded), sources, width, game.pixel_algorithm)


async def _first_unused_url(
    context: ContextTypes.DEFAULT_TYPE, plan: ScreenshotPlan
) -> tuple[Provider, str] | None:
    for provider, provider_id in plan.sources:
        client = client_for_source(context, provider)
        try:
            urls = await provider.screenshot_module.screenshots(client, provider_id)
        except _SEARCH_SERVICE_ERRORS:
            logger.warning("Extra screenshot: {} lookup failed", provider, exc_info=True)
            continue
        unused = next((url for url in urls if url not in plan.excluded), None)
        if unused is not None:
            return provider, unused
    logger.info("Extra screenshot: no unused screenshot among {} providers", len(plan.sources))
    return None


async def fetch_extra_screenshot(
    context: ContextTypes.DEFAULT_TYPE, plan: ScreenshotPlan
) -> FetchedScreenshot | None:
    """Find, download and pixelate an unused screenshot, or None (logged)
    if any step fails — the caller hasn't charged anyone yet."""
    found = await _first_unused_url(context, plan)
    if found is None:
        return None
    provider, url = found
    try:
        response = await client_for_source(context, provider).get(url)
        response.raise_for_status()
    except _IMAGE_DOWNLOAD_ERRORS:
        logger.warning("Extra screenshot: download failed", exc_info=True)
        return None
    try:
        pixelated = pixelate_service.pixelate(response.content, plan.width, plan.algorithm)
    except (OSError, ValueError):
        logger.warning("Extra screenshot: could not pixelate the download", exc_info=True)
        return None
    return FetchedScreenshot(url, pixelated)


def render_tile_clue(session: Session, game: Game, tiles: set[int]) -> bytes:
    """The pixelated screenshot with every tile in `tiles` shown
    unpixelated."""
    original = game.original_image
    if original is None:
        logger.error("Game {} has no original image for a tile clue", game.id)
        raise ShopRefusedError(Refusal.UNAVAILABLE)
    pixelated = pixelate_service.pixelate(
        original, stage_width(session, game), game.pixel_algorithm
    )
    return render.reveal_tiles(original, pixelated, tiles, shop.TILE_GRID)
