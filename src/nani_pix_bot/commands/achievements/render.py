"""Status -> rich-message markdown (spec §5). Every piece of text that
isn't markdown syntax goes through md_escape — names and titles included."""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import MappingProxyType

from sqlalchemy.orm import Session

from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.models.enums import Rarity
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.achievements import names, status, titles
from nani_pix_bot.services.achievements.definitions import Kind, rarity_of
from nani_pix_bot.services.achievements.status import State, Status

MEDALS: Mapping[Rarity, str] = MappingProxyType(
    {Rarity.BRONZE: "🥉", Rarity.SILVER: "🥈", Rarity.GOLD: "🥇", Rarity.PLATINUM: "💎"}
)
_LATEST = 3
# TAKEN shares the "not started" heading.
_HEADING: Mapping[State, State] = MappingProxyType({State.TAKEN: State.UNTOUCHED})


def _t(key: str, lang: str, **kwargs: object) -> str:
    return md_escape(i18n.t(key, lang, **kwargs))


def heading(state: State, lang: str) -> str:
    return "## " + _t(f"achievements.section.{state.value}", lang)


def _earned_row(item: Status, lang: str, _holder: str | None) -> str:
    defn = item.defn
    medal = MEDALS[rarity_of(defn, max(item.tier, 1))]
    if defn.kind is Kind.PERIOD:
        latest = md_escape(names.title(defn, 1, item.period_key, lang))
        times = _t("achievements.times", lang, count=item.tier)
        if item.rank is not None:
            times += " · " + _t("achievements.race_now", lang, rank=item.rank)
        return f"- [x] {medal} **{md_escape(names.name(defn, 0, lang))}** {times} — {latest}"
    line = (
        f"- [x] {medal} **{md_escape(names.name(defn, item.tier, lang))}**"
        f" — {md_escape(names.description(defn, item.tier, lang))}"
    )
    if item.granted_at is not None:
        line += f" · {item.granted_at:%Y-%m-%d}"
    if item.target is not None:
        line += " · _" + _t("achievements.next", lang, value=item.value, target=item.target) + "_"
    return line


def _open_row(item: Status, lang: str, _holder: str | None) -> str:
    name = md_escape(names.name(item.defn, 0, lang))
    line = f"- [ ] **{name}** — {md_escape(names.description(item.defn, 1, lang))}"
    if item.state is State.PARTIAL and item.target is not None:
        line += f" · {item.value}/{item.target}"
    return line


def _race_row(item: Status, lang: str, _holder: str | None) -> str:
    name = md_escape(names.name(item.defn, 0, lang))
    return f"- [ ] ⏳ **{name}** — " + _t("achievements.race", lang, rank=item.rank)


def _taken_row(item: Status, lang: str, holder: str | None) -> str:
    name = md_escape(names.name(item.defn, 0, lang))
    return f"- [ ] 🔒 **{name}** — " + _t("achievements.taken_by", lang, player=holder or "?")


def _hidden_row(_item: Status, lang: str, _holder: str | None) -> str:
    return "- [ ] ❔ " + _t("achievement.hidden", lang)


_ROWS: Mapping[State, Callable[[Status, str, str | None], str]] = MappingProxyType(
    {
        State.EARNED: _earned_row,
        State.RACE: _race_row,
        State.PARTIAL: _open_row,
        State.UNTOUCHED: _open_row,
        State.TAKEN: _taken_row,
        State.HIDDEN: _hidden_row,
    }
)


def row(item: Status, lang: str, holder: str | None) -> str:
    return _ROWS[item.state](item, lang, holder)


def _holder(session: Session, item: Status) -> str | None:
    return None if item.holder_id is None else players.display_name(session, item.holder_id)


def page(session: Session, header: str, items: Sequence[Status], lang: str) -> str:
    """`items` already ordered (status.ordered); a heading starts each section."""
    lines = [header]
    current: State | None = None
    for item in items:
        group = _HEADING.get(item.state, item.state)
        if group is not current:
            lines.append(heading(group, lang))
            current = group
        lines.append(row(item, lang, _holder(session, item)))
    return "\n".join(lines)


def header_line(session: Session, owner_id: int, items: Sequence[Status], lang: str) -> str:
    earned = sum(1 for s in items if s.state is State.EARNED)
    rank = status.rank_of(session, owner_id)
    return _t(
        "achievements.summary.line",
        lang,
        earned=earned,
        total=len(items),
        points=status.points_of(session, owner_id),
        rank=rank if rank is not None else "—",
    )


def summary(session: Session, owner_id: int, lang: str, now: datetime) -> str:
    items = status.build(session, owner_id, now)
    lines = [
        "## 🏅 " + md_escape(players.display_name(session, owner_id)),
        header_line(session, owner_id, items, lang),
    ]
    owner = session.get(Player, owner_id)
    owner_title = titles.text(owner.title_key, lang) if owner else None
    if owner_title:
        lines.append(_t("achievements.summary.title", lang, title=owner_title))
    latest = status.newest_first(s for s in items if s.state is State.EARNED)[:_LATEST]
    if latest:
        labels = ", ".join(names.title(s.defn, s.tier, s.period_key, lang) for s in latest)
        lines.append(_t("achievements.summary.latest", lang, names=labels))
    else:
        lines.append(_t("achievements.summary.none", lang))
    return "\n\n".join(lines)


def top_table(session: Session, rows: Sequence[status.TopRow], lang: str, offset: int) -> str:
    if not rows:
        return _t("achievements.top.empty", lang)
    lines = [
        "## " + _t("achievements.top.header", lang),
        "",
        "| # | "
        + _t("achievements.top.player", lang)
        + " | 🏅 | "
        + _t("achievements.top.points", lang)
        + " |",
        "|---|---|---|---|",
    ]
    for rank, top_row in enumerate(rows, start=offset + 1):
        name = md_escape(players.display_name(session, top_row.player_id))
        holder = session.get(Player, top_row.player_id)
        title = titles.text(holder.title_key, lang) if holder else None
        if title:
            name += f" «{md_escape(title)}»"
        lines.append(f"| {rank} | {name} | {top_row.count} | {top_row.points} |")
    return "\n".join(lines)
