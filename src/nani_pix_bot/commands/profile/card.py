"""One player's profile card as a photo and caption, shared by /profile and
the card's ◀ ▶ / player-list buttons."""

import asyncio

from sqlalchemy.orm import Session
from telegram.ext import ContextTypes

from nani_pix_bot.jobs.avatars import fetch_avatar
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cards import (
    BadgeEntry,
    ProfileCard,
    StampEntry,
    render_profile_card,
)
from nani_pix_bot.services.cosmetics import flair, profile


def _card(view: profile.ProfileView, lang: str) -> ProfileCard:
    return ProfileCard(
        name=view.name,
        title=view.title,
        stamps=tuple(StampEntry(s.label, s.gold) for s in view.stamps),
        stats=profile.stats(view.seasons_played, view.cosmetics_owned, lang),
        seed=view.player_id,
        badges=tuple(BadgeEntry(b.label, b.rarity) for b in view.badges),
    )


def _caption(view: profile.ProfileView, headline: str, lang: str) -> str:
    if view.title:
        headline = f"{headline} · {view.title}"
    return i18n.t(
        "profile.caption",
        lang,
        headline=headline,
        seasons=view.seasons_played,
        owned=view.cosmetics_owned,
    )


def load(session: Session, player_id: int, lang: str) -> tuple[profile.ProfileView, str]:
    """The profile and its caption headline (the name with flair)."""
    view = profile.build(session, player_id, lang)
    return view, flair.named(session, player_id, view.name)


async def photo(
    context: ContextTypes.DEFAULT_TYPE, view: profile.ProfileView, headline: str, lang: str
) -> tuple[bytes, str]:
    """The rendered card and its caption."""
    avatar = await fetch_avatar(context.bot, view.player_id)
    png = await asyncio.to_thread(render_profile_card, _card(view, lang), avatar, view.banner)
    return png, _caption(view, headline, lang)
