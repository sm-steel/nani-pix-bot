"""The season gate on DM setup (seasons spec §5): checked when a catalogue
result is picked, and again — authoritatively — at Confirm, which every
way of identifying a game passes through."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import client_for_source
from nani_pix_bot.commands.dm_start.aliases import identity_of, lookup_for
from nani_pix_bot.commands.dm_start.screenshots import clear_screenshot_selection
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import Provider, SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.seasons.definition import Gate
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.search import aliases
from nani_pix_bot.services.search import tags as anime_tags
from nani_pix_bot.services.search.aliases import AliasLookup
from nani_pix_bot.services.seasons import gate as gate_service
from nani_pix_bot.services.seasons.gate import Verdict
from nani_pix_bot.services.seasons.tags import TAG_ERRORS, TagClients


@dataclass(frozen=True)
class _Pending:
    game_id: int
    identity: tuple  # identity_of(game) when read: what the verdict is about
    gate: Gate
    lookup: AliasLookup
    known: list[anime_tags.AnimeTag] | None


def _pending(session: Session, starter_id: int) -> _Pending | None:
    game = game_service.get_setup_game_for_starter(session, starter_id)
    gate = gate_service.active_gate(session) if game is not None else None
    if game is None or gate is None:
        return None
    known = anime_tags.from_json(game.anime_tags) if game.anime_tags is not None else None
    return _Pending(game.id, identity_of(game), gate, lookup_for(game), known)


async def _verdict(
    context: ContextTypes.DEFAULT_TYPE, pending: _Pending
) -> gate_service.GateResult:
    alias_clients = aliases.AliasClients(
        anilist=client_for_source(context, Provider.ANILIST),
        shikimori=client_for_source(context, Provider.SHIKIMORI),
        tenrai=client_for_source(context, Provider.TENRAI),
    )
    mal_id = None
    if pending.known is None:
        try:
            mal_id = await aliases.mal_id(alias_clients, pending.lookup)
        except TAG_ERRORS as exc:
            logger.warning(
                "couldn't resolve a MAL id for the season gate — unavailable: {error!r}",
                error=exc,
                game_id=pending.game_id,
            )
            return gate_service.GateResult(Verdict.UNAVAILABLE)
    clients = TagClients(shikimori=alias_clients.shikimori, tenrai=alias_clients.tenrai)
    return await gate_service.check(
        pending.gate, mal_id=mal_id, clients=clients, known=pending.known
    )


def _apply(session: Session, pending: _Pending, result: gate_service.GateResult) -> bool:
    """Keep fetched tags (so Confirm doesn't refetch after a pick passed);
    on a refusal, forget the identification — and a catalogue screenshot
    of the refused anime, as the preview's Re-search does — and send the
    setup back to picking a method, where the refusal's "different
    method" button works. An uploaded photo stays.

    False, writing nothing, when the game was re-identified while the
    check was in flight: the verdict is about an anime it no longer is."""
    game = session.get(Game, pending.game_id)
    if game is None:
        return True
    if identity_of(game) != pending.identity:
        return False
    if result.tags:
        game.anime_tags = anime_tags.to_json(result.tags)
    if result.verdict in gate_service.REFUSED:
        game_service.clear_identification(game)
        clear_screenshot_selection(game)
        game.setup_step = SetupStep.PICKING_METHOD
    return True


async def run_gate(
    context: ContextTypes.DEFAULT_TYPE, session_factory: sessionmaker[Session], starter_id: int
) -> Verdict:
    """The active season's verdict on the starter's SETUP game — PASSED when
    there's no season running (or no SETUP game). Already-known tags are
    re-checked against the current gate: the season may have started since."""
    with session_scope(session_factory) as session:
        pending = _pending(session, starter_id)
    if pending is None:
        return Verdict.PASSED
    result = await _verdict(context, pending)
    with session_scope(session_factory) as session:
        applied = _apply(session, pending, result)
    if not applied:
        # Neither refuse nor store: Confirm can be tapped again, and the
        # new pick runs its own gate.
        logger.info(
            "season gate result for {title!r} is stale — identification changed",
            title=pending.lookup.title,
            game_id=pending.game_id,
        )
        return Verdict.UNAVAILABLE
    logger.info(
        "season gate: {verdict} for {title!r}",
        verdict=result.verdict.value,
        title=pending.lookup.title,
        tags=[t.name for t in result.tags],
        game_id=pending.game_id,
    )
    return result.verdict


def rule_text(session_factory: sessionmaker[Session], lang: str) -> str:
    """The running season's rule in `lang` (English when that's missing)."""
    with session_scope(session_factory) as session:
        gate = gate_service.active_gate(session)
    if gate is None:
        return ""
    return gate.description.get(lang) or gate.description.get("EN", "")


def refusal_text(verdict: Verdict, gate_rule: str, lang: str) -> str:
    return i18n.t(f"season.gate.{verdict.value}", lang, rule=gate_rule)
