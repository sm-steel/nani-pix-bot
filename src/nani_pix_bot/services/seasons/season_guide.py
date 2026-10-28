"""The season guide: rich-message markdown that follows the season's teaser
and start banners, so players meet every mechanic the run switches on before
it happens to them, each section linking to its page in the user's guide.
Every number is read from the run (pure; no Telegram)."""

from datetime import timedelta

from nani_pix_bot.models.enums import ConsumableKind
from nani_pix_bot.seasons.definition import Reward, RewardKind, SeasonRun
from nani_pix_bot.seasons.mechanics import HEX_ICONS, GroupGoal, OmikujiOutcome
from nani_pix_bot.services import i18n
from nani_pix_bot.services.seasons.season_pass import describe
from nani_pix_bot.services.text import duration, md_escape

GUIDE_URL = "https://sm-steel.github.io/nani-pix-bot/"


def _t(key: str, lang: str, **kwargs: object) -> str:
    return md_escape(i18n.t(f"season_guide.{key}", lang, **kwargs))


def _url(page: str, lang: str) -> str:
    return f"{GUIDE_URL}{'ru/' if lang == 'RU' else ''}seasons/{page}/"


def _section(title: str, blocks: list[str], page: str, lang: str) -> list[str]:
    link = f"📖 [{_t('more', lang)}]({_url(page, lang)})"
    return [f"## {title}", *blocks, link]


def _table(head: list[str], rows: list[list[str]]) -> str:
    """One block: a table's rows must touch, with no blank line between them."""

    def row(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    return "\n".join([row(head), "|" + "---|" * len(head), *(row(cells) for cells in rows)])


def _intro(run: SeasonRun, lang: str, *, started: bool) -> list[str]:
    phase = "start" if started else "teaser"
    name = run.name(lang)
    lines = [f"## {_t(f'heading.{phase}', lang, emoji=run.emoji, name=name)}"]
    lines.append(_t(f"intro.{phase}", lang))
    if run.gate is not None and not started:  # the start caption already states it
        rule = run.gate.description.get(lang) or run.gate.description["EN"]
        lines.append(md_escape(i18n.t("season.post.gate", lang, rule=rule)))
    if run.guide_page is not None:
        lines.append(f"📖 [{_t('overview', lang, name=name)}]({_url(run.guide_page, lang)})")
    return lines


def _earning(run: SeasonRun, lang: str) -> list[str]:
    xp, coins = run.xp, run.coins
    head = [_t("earn.col_what", lang), "XP", "🪙"]
    wins = f"{xp.win_by_stage[0]} → {xp.win_by_stage[-1]}"
    rows = [
        [_t("earn.win", lang), md_escape(wins), str(coins.win)],
        [_t("earn.host_solved", lang), str(xp.host_solved), str(coins.host_solved)],
        [_t("earn.host_unsolved", lang), str(xp.host_unsolved), str(coins.host_unsolved)],
        [_t("earn.first_guess", lang), str(xp.first_guess), str(coins.first_guess)],
    ]
    lines = [_table(head, rows)]
    if run.tiers:
        lines.append(_t("earn.pass", lang, tiers=len(run.tiers)))
    if any(tier.premium for tier in run.tiers):
        lines.append(_t("earn.premium", lang, price=run.premium_price))
    if run.objectives:
        lines.append(_t("earn.objectives", lang, count=len(run.objectives)))
    if run.shop:
        lines.append(_t("earn.shop", lang))
    lines.append(_t("earn.conversion", lang, rate=run.coins_per_currency))
    return _section(_t("earn.title", lang), lines, "season-pass", lang)


def _haunt(run: SeasonRun, lang: str) -> list[str]:
    if run.haunt is None:
        return []
    twists = ", ".join(i18n.t(f"haunt.twist.{twist.value}", lang) for twist in run.haunt.twists)
    body = _t(
        "haunt.body",
        lang,
        chance=run.haunt.chance_pct,
        twists=twists,
        multiplier=run.haunt.coin_multiplier,
    )
    return _section(_t("haunt.title", lang), [body], "haunted-rounds", lang)


def _event_day(run: SeasonRun, lang: str) -> list[str]:
    event = run.event_day
    if event is None:
        return []
    name = event.names.get(lang) or event.names["EN"]
    lines = []
    if event.all_haunted and run.haunt is not None:
        lines.append(_t("event.haunted", lang))
    if event.gate is not None:
        rule = event.gate.description.get(lang) or event.gate.description["EN"]
        lines.append(_t("event.gate", lang, rule=rule))
    multiplier = event.coin_multiplier
    if event.all_haunted and run.haunt is not None:
        multiplier *= run.haunt.coin_multiplier  # stacks, as in multipliers.coin_multiplier
    if multiplier > 1:
        lines.append(_t("event.coins", lang, multiplier=multiplier))
    title = _t("event.title", lang, name=name, date=f"{event.day:02d}.{event.month:02d}")
    return _section(title, lines, "haunted-rounds", lang)


def _goal_reward(run: SeasonRun, goal: GroupGoal, lang: str) -> str:
    parts = [f"{goal.contributor_coins} 🪙"] if goal.contributor_coins else []
    if goal.contributor_item_key is not None:
        parts.append(describe(run, Reward.item(goal.contributor_item_key), lang))
    return i18n.t("season_guide.and", lang).join(parts)


def _group_goal(run: SeasonRun, lang: str) -> list[str]:
    goal = run.group_goal
    if goal is None:
        return []
    weights = {"game": goal.per_game, "haunted": goal.per_haunted}
    if run.event_day is not None:
        event = run.event_day.names.get(lang) or run.event_day.names["EN"]
        body = _t("goal.body_event", lang, **weights, day=goal.per_event_day, event=event)
    else:
        body = _t("goal.body", lang, **weights)
    boss_coins = goal.boss_coin_multiplier * (run.haunt.coin_multiplier if run.haunt else 1)
    reward = _t(
        "goal.reward",
        lang,
        target=goal.target,
        reward=_goal_reward(run, goal, lang),
        xp=goal.boss_xp_multiplier,
        coins=boss_coins,
    )
    title = _t("goal.title", lang, target=goal.target)
    return _section(title, [body, reward], "hundred-candles", lang)


def _hexes(run: SeasonRun, lang: str) -> list[str]:
    if not run.hexes:
        return []
    head = [_t(f"hexes.col_{col}", lang) for col in ("hex", "price", "cooldown", "effect")]
    rows = []
    for rule in run.hexes:
        effect = i18n.t(
            f"hex.kind.{rule.kind.value}.effect",
            lang,
            hours=rule.duration_hours,
            amount=rule.amount,
        )
        name = i18n.t(f"hex.kind.{rule.kind.value}.name", lang)
        cooldown = duration(timedelta(minutes=rule.cooldown_minutes), lang)
        cells = [f"{HEX_ICONS[rule.kind]} {name}", str(rule.price), cooldown, effect]
        rows.append([md_escape(cell) for cell in cells])
    lines = [_t("hexes.body", lang), _table(head, rows), _t("hexes.defence", lang)]
    return _section(_t("hexes.title", lang), lines, "hexes", lang)


def _prize(outcome: OmikujiOutcome, lang: str) -> str:
    parts = [f"{outcome.coins} 🪙"] if outcome.coins else []
    if outcome.consumable is not None:
        parts.append(i18n.t(f"season_guide.charm.{outcome.consumable.value}", lang))
    if outcome.prank:
        parts.append(i18n.t("season_guide.omikuji.prank", lang))
    return md_escape(i18n.t("season_guide.and", lang).join(parts) or "—")


def _omikuji(run: SeasonRun, lang: str) -> list[str]:
    if not run.omikuji:
        return []
    total = sum(outcome.weight for outcome in run.omikuji)
    head = [_t(f"omikuji.col_{col}", lang) for col in ("slip", "chance", "prize")]
    rows = [
        [
            _t(f"slip.{outcome.kind.value}", lang),
            f"{outcome.weight * 100 // total}%",
            _prize(outcome, lang),
        ]
        for outcome in run.omikuji
    ]
    lines = [_t("omikuji.body", lang), _table(head, rows)]
    return _section(_t("omikuji.title", lang), lines, "omikuji-and-charms", lang)


def _omamori_sources(run: SeasonRun, lang: str) -> list[str]:
    sources = [
        i18n.t("season_guide.charms.from_tier", lang, tier=number)
        for number, tier in enumerate(run.tiers, start=1)
        if tier.free is not None and tier.free.kind is RewardKind.OMAMORI
    ]
    sources += [
        i18n.t(
            "season_guide.charms.from_slip",
            lang,
            slip=i18n.t(f"season_guide.slip.{o.kind.value}", lang),
        )
        for o in run.omikuji
        if o.consumable is ConsumableKind.OMAMORI
    ]
    return sources


def _charms(run: SeasonRun, lang: str) -> list[str]:
    lines = [
        _t("charms.ofuda", lang, price=offer.price, limit=offer.limit)
        for offer in run.shop
        if offer.kind is RewardKind.OFUDA
    ]
    sources = _omamori_sources(run, lang)
    if run.hexes and sources:
        lines.append(
            _t("charms.omamori", lang, sources=i18n.t("season_guide.or", lang).join(sources))
        )
    if not lines:
        return []
    lines.append(_t("charms.inventory", lang))
    return _section(_t("charms.title", lang), lines, "omikuji-and-charms", lang)


def _bones(run: SeasonRun, lang: str) -> list[str]:
    if run.bone_set is None:
        return []
    item = describe(run, Reward.item(run.bone_set.item_key), lang)
    body = _t("bones.body", lang, bones=run.bone_set.bones, item=item)
    return _section(_t("bones.title", lang), [body], "hundred-candles", lang)


def _commands(run: SeasonRun, lang: str) -> list[str]:
    commands = ["/seasonpass"]
    commands += ["/seasonshop"] if run.shop else []
    commands += ["/candles"] if run.group_goal else []
    commands += ["/hex", "/hexes"] if run.hexes else []
    commands += ["/omikuji"] if run.omikuji else []
    if run.omikuji or any(o.kind in (RewardKind.OFUDA, RewardKind.OMAMORI) for o in run.shop):
        commands.append("/inventory")
    return [_t("commands", lang, commands=" · ".join(commands))]


def markdown(run: SeasonRun, lang: str, *, started: bool) -> str:
    """The guide under the teaser (`started` False) or the start banner. Every
    block (heading, paragraph, table, link) stands apart with a blank line:
    lines that touch run into one paragraph in a rich message, and a table
    can't start inside one (issue #487)."""
    lines = [
        *_intro(run, lang, started=started),
        *_earning(run, lang),
        *_haunt(run, lang),
        *_event_day(run, lang),
        *_group_goal(run, lang),
        *_hexes(run, lang),
        *_omikuji(run, lang),
        *_charms(run, lang),
        *_bones(run, lang),
        *_commands(run, lang),
    ]
    return "\n\n".join(lines)
