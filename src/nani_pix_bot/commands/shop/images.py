"""The image clues of the DM shop: the extra pixelated screenshot (fetched
and rendered before anyone is charged) and the revealed-tiles picture."""

import random
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, replace

from loguru import logger
from sqlalchemy.orm import Session
from telegram import User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    IMAGE_DOWNLOAD_ERRORS,
    SEARCH_SERVICE_ERRORS,
    client_for_source,
)
from nani_pix_bot.models.enums import ClueKind, ClueScreenshotMode, PixelAlgorithm, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import mascot, players, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.clues import author_list, handouts, shop
from nani_pix_bot.services.clues.shop import Offer, Refusal, ShopRefusedError
from nani_pix_bot.services.cosmetics import costume
from nani_pix_bot.services.cosmetics import stage as stage_cosmetics
from nani_pix_bot.services.mascot import Pose
from nani_pix_bot.services.pixelate import fingerprint, render
from nani_pix_bot.services.seasons import haunting
from nani_pix_bot.services.settings import stage_config

# How many candidate screenshots one purchase may download before giving
# up, when each turns out to be a frame already seen in the round.
MAX_CANDIDATE_DOWNLOADS = 5
_ROUND_IMAGE = "the round's own image"
_EARLIER_CLUE = "an earlier clue"


@dataclass(frozen=True)
class AuthorCandidate:
    """An unsold entry of the author's list (issue #450), with the bytes it
    is pixelated from: no download needed. `key` is its handout key
    (author_list.handout_key)."""

    key: str
    image: bytes
    fingerprint: int


@dataclass(frozen=True)
class ScreenshotPlan:
    """Everything the network step needs, read in one short session.
    `excluded` are URLs never to sell (the round's own, and every one
    handed out in the game — issue #446); `seen` are fingerprints of frames
    the round already showed, each with what it was, for the same check
    on frames behind another URL or uploaded. `author` are the author's
    list's unsold entries (none in Random mode), tried before any random pick; `mode` says
    whether a random pick may follow (issue #450). `pose` is Nani's on the current stage,
    so the clue looks like the round's own picture (issue #461)."""

    lang: str
    excluded: frozenset[str]
    sources: tuple[tuple[Provider, int], ...]
    width: int
    algorithm: PixelAlgorithm
    seen: tuple[tuple[int, str], ...] = ()
    mode: ClueScreenshotMode = ClueScreenshotMode.RANDOM
    author: tuple[AuthorCandidate, ...] = ()
    pose: Pose = Pose.CAST
    costume: str | None = None


class ScreenshotFetchError(Exception):
    """A provider lookup, the download or the pixelation failed (as opposed
    to there being no unused screenshot): the buyer was not charged."""


@dataclass(frozen=True)
class FetchedScreenshot:
    url: str  # the handout key: a provider URL, or an upload's author:<id>
    pixelated: bytes
    fingerprint: int
    original: bytes
    from_author: bool = False


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
        raise _refused(game_id, kind, Refusal.NO_GAME)
    buyer = players.get_or_create_player(session, user.id, username=user.username)
    if buyer.telegram_user_id == game.starter_id:
        raise _refused(game_id, kind, Refusal.SETTER)
    lang = settings.get_language(session)
    offer = next((o for o in shop.offers(session, game, buyer, lang) if o.kind is kind), None)
    if offer is None:
        raise _refused(game_id, kind, Refusal.UNAVAILABLE)
    return game, lang, offer


def _refused(game_id: int, kind: ClueKind, refusal: Refusal) -> ShopRefusedError:
    """Logs a shop button refused before any charge, and returns the error
    to raise. Refusals at the charge itself are logged by shop.purchase."""
    logger.warning(
        "tapped the {kind} clue button — refused: {reason}",
        kind=kind.value,
        reason=refusal.value,
        game_id=game_id,
    )
    return ShopRefusedError(refusal)


def screenshot_plan(session: Session, user: User, game_id: int) -> ScreenshotPlan:
    game, lang, offer = offered_game(session, user, game_id, ClueKind.SCREENSHOT)
    if not offer.affordable:
        raise _refused(game_id, ClueKind.SCREENSHOT, Refusal.INSUFFICIENT)
    given = handouts.for_game(session, game.id)
    excluded = set(game.shown_screenshot_urls or []) | {row.screenshot_url for row in given}
    seen = [(print_, _ROUND_IMAGE) for print_ in _round_fingerprints(game)]
    seen += [(row.fingerprint, _EARLIER_CLUE) for row in given if row.fingerprint is not None]
    sources = tuple(
        (provider, getattr(game, provider.id_attr_name))
        for provider in shop.clue_screenshot_providers(game)
    )
    mode = ClueScreenshotMode(game.clue_screenshot_mode)
    return ScreenshotPlan(
        lang,
        frozenset(excluded),
        sources,
        stage_width(session, game),
        game.pixel_algorithm,
        tuple(seen),
        mode=mode,
        author=_author_candidates(session, game, mode),
        pose=stage_cosmetics.stage_pose(session, game),
        costume=costume.for_game(session, game),
    )


def _author_candidates(
    session: Session, game: Game, mode: ClueScreenshotMode
) -> tuple[AuthorCandidate, ...]:
    """The author's unsold entries, or none in Random mode: Random is the
    random pick alone, and the list stays stored but unused (issue #450)."""
    if mode == ClueScreenshotMode.RANDOM:
        return ()
    return tuple(
        AuthorCandidate(author_list.handout_key(entry), entry.image, entry.fingerprint)
        for entry in author_list.unsold(session, game)
        if entry.image is not None
    )


def _round_fingerprints(game: Game) -> list[int]:
    """Fingerprints of the image(s) the round is played on: the original,
    or HARD MODE's pair. One that can't be read is logged and left out."""
    prints = []
    for image in (game.original_image, game.hard_mode_image_a, game.hard_mode_image_b):
        if image is None:
            continue
        try:
            prints.append(fingerprint.fingerprint(image))
        except ValueError:
            logger.opt(exception=True).warning(
                "extra screenshot: could not fingerprint the round's image"
            )
    return prints


async def _unused_urls(
    context: ContextTypes.DEFAULT_TYPE, plan: ScreenshotPlan, failed: list[Provider]
) -> AsyncGenerator[tuple[Provider, str]]:
    """Every not-excluded screenshot URL, provider by provider (best first),
    in random order within each — so buyers don't all get the same one. A
    provider whose lookup fails is logged and added to `failed`."""
    for provider, provider_id in plan.sources:
        client = client_for_source(context, provider)
        try:
            urls = await provider.screenshot_module.screenshots(client, provider_id)
        except SEARCH_SERVICE_ERRORS:
            logger.opt(exception=True).warning(
                "extra screenshot: {provider} lookup failed", provider=provider.value
            )
            failed.append(provider)
            continue
        unused = [url for url in urls if url not in plan.excluded]
        random.shuffle(unused)
        for url in unused:
            yield provider, url


async def _download(context: ContextTypes.DEFAULT_TYPE, provider: Provider, url: str) -> bytes:
    try:
        response = await client_for_source(context, provider).get(url)
        response.raise_for_status()
    except IMAGE_DOWNLOAD_ERRORS:
        logger.opt(exception=True).warning("extra screenshot: download failed")
        raise ScreenshotFetchError("download failed") from None
    return response.content


def _already_seen(plan: ScreenshotPlan, url: str, print_: int) -> bool:
    """Whether the frame is one the round already showed (logged if so)."""
    for seen_print, seen_as in plan.seen:
        if fingerprint.same_frame(print_, seen_print):
            logger.info(
                "extra screenshot: skipped {screenshot_url} — same frame as {seen_as}"
                " (distance {distance})",
                screenshot_url=url,
                seen_as=seen_as,
                distance=fingerprint.distance(print_, seen_print),
            )
            return True
    return False


def _fingerprint(data: bytes) -> int:
    try:
        return fingerprint.fingerprint(data)
    except ValueError:
        logger.opt(exception=True).warning("extra screenshot: download is not a readable image")
        raise ScreenshotFetchError("unreadable download") from None


def _render(plan: ScreenshotPlan, url: str, data: bytes, print_: int) -> FetchedScreenshot:
    try:
        pixelated = pixelate_service.pixelate(data, plan.width, plan.algorithm)
    except (OSError, ValueError):
        logger.opt(exception=True).warning(
            "extra screenshot: could not pixelate {screenshot_url}", screenshot_url=url
        )
        raise ScreenshotFetchError("pixelation failed") from None
    return FetchedScreenshot(
        url, mascot.add_to_png(pixelated, plan.pose, plan.costume), print_, data
    )


def _author_pick(plan: ScreenshotPlan) -> FetchedScreenshot | None:
    """A random unsold entry of the author's list the round hasn't shown
    yet, pixelated from its stored bytes; None when none is left."""
    candidates = list(plan.author)
    random.shuffle(candidates)
    for candidate in candidates:
        if not _already_seen(plan, candidate.key, candidate.fingerprint):
            logger.info(
                "extra screenshot: picked {screenshot_url} from the author's list ({left} unsold)",
                screenshot_url=candidate.key,
                left=len(candidates),
            )
            rendered = _render(plan, candidate.key, candidate.image, candidate.fingerprint)
            return replace(rendered, from_author=True)
    return None


async def fetch_extra_screenshot(
    context: ContextTypes.DEFAULT_TYPE, plan: ScreenshotPlan
) -> FetchedScreenshot | None:
    """Find, download and pixelate a screenshot nobody in the round has seen
    yet, or None (logged) if every provider answered but none had one. A
    failed lookup, download or pixelation raises ScreenshotFetchError — the
    caller hasn't charged anyone yet.

    The author's list goes first (issue #450); a random pick follows only
    in Random or Both mode."""
    if (picked := _author_pick(plan)) is not None:
        return picked
    if plan.mode == ClueScreenshotMode.AUTHOR:
        logger.info(
            "extra screenshot: the author's list is used up ({count} entries tried)"
            " — no random pick in Author's mode",
            count=len(plan.author),
        )
        return None
    failed: list[Provider] = []
    tried = 0
    async with aclosing(_unused_urls(context, plan, failed)) as candidates:
        async for provider, url in candidates:
            if tried == MAX_CANDIDATE_DOWNLOADS:
                break
            tried += 1
            data = await _download(context, provider, url)
            print_ = _fingerprint(data)
            if not _already_seen(plan, url, print_):
                logger.info(
                    "extra screenshot: picked {screenshot_url} ({tried} candidate(s) tried)",
                    screenshot_url=url,
                    tried=tried,
                )
                return _render(plan, url, data, print_)
    if failed:
        raise ScreenshotFetchError("provider lookup failed")
    logger.info(
        "extra screenshot: no unseen screenshot among {count} providers ({tried} tried)",
        count=len(plan.sources),
        tried=tried,
    )
    return None


def render_tile_clue(session: Session, game: Game, tile: int) -> bytes:
    """The pixelated screenshot with the round's `tile` shown unpixelated, both
    wearing the current stage's haunted twists."""
    original = game.original_image
    if original is None:
        logger.error("no original image for a tile clue", game_id=game.id)
        raise ShopRefusedError(Refusal.UNAVAILABLE)
    pixelated = pixelate_service.pixelate(
        original, stage_width(session, game), game.pixel_algorithm
    )
    haunt = stage_cosmetics.current_haunt(session, game)
    if haunt is not None:
        # Both images wear the stage's twists, so the tapped tile lines up with
        # what the buyer sees (the mirror included).
        original = haunting.apply_twists(original, haunt.twists)
        pixelated = haunting.apply_twists(pixelated, haunt.twists)
    revealed = render.reveal_tiles(original, pixelated, [tile], shop.TILE_GRID)
    return mascot.add_to_png(
        revealed, stage_cosmetics.stage_pose(session, game), costume.for_game(session, game)
    )
