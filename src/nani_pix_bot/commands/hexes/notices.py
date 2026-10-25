"""The public notice of a cast or a blocked hex: a duel moment card whose caption
is the notice line. Both people are named with `players.display_name`, without
flair or the 💀 marker, so a Nopperabō notice still says who was hexed."""

from nani_pix_bot.commands.hexes.common import kind_name
from nani_pix_bot.jobs.moments import DuelMoment, Person
from nani_pix_bot.seasons.mechanics import HexRule
from nani_pix_bot.services import i18n
from nani_pix_bot.services.seasons import hex_art


def _caption(sides: tuple[Person, Person], rule: HexRule, blocked: bool, lang: str) -> str:
    caster, target = sides[0].display_name, sides[1].display_name
    kind = rule.kind.value
    if blocked:
        hex_name = kind_name(kind, lang)
        return i18n.t("hex.notice.blocked", lang, caster=caster, target=target, hex=hex_name)
    return i18n.t(
        f"hex.notice.cast.{kind}",
        lang,
        caster=caster,
        target=target,
        hours=rule.duration_hours,
        amount=rule.amount,
    )


def _detail(sides: tuple[Person, Person], rule: HexRule, blocked: bool, lang: str) -> str:
    """The effect line on the card: what the hex does to the target."""
    kind = rule.kind.value
    if blocked:
        return i18n.t(
            "hex.card_detail.blocked", lang, caster=sides[0].card_name, hex=kind_name(kind, lang)
        )
    return i18n.t(
        f"hex.card_detail.{kind}",
        lang,
        caster=sides[0].card_name,
        target=sides[1].card_name,
        hours=rule.duration_hours,
        amount=rule.amount,
    )


def moment(sides: tuple[Person, Person], rule: HexRule, blocked: bool, lang: str) -> DuelMoment:
    """The card for a cast at `sides` = (caster, target); `blocked` when an Omamori ate it."""
    actor, target = sides
    if blocked:
        title = i18n.t("hex.card_title.blocked", lang)
    else:
        title = i18n.t("hex.card_title.cast", lang, target=target.card_name)
    return DuelMoment(
        name=f"hex {rule.kind.value}" + (" blocked" if blocked else ""),
        actor=actor,
        target=target,
        art=hex_art.art(rule.kind.value),
        title=title,
        detail=_detail(sides, rule, blocked, lang),
        caption=_caption(sides, rule, blocked, lang),
        shield=hex_art.art(hex_art.OMAMORI) if blocked else None,
        blocked=blocked,
    )
