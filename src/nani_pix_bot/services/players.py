"""Everything that touches only the Player table: lookup/creation,
case-insensitive username lookup, and the /leaderboard query. Win
increments themselves live in services/game/state.py's _win() (the
only place that mutates a Game/Player pair together)."""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import CurrencyReason
from nani_pix_bot.models.player import USERNAME_LENGTH, Player
from nani_pix_bot.services.economy import config as economy_config
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.quiet_hours import parse_timezone


def describe_person(user_id: int, *, username: str | None = None, name: str | None = None) -> str:
    """How a person appears in a log line: `5 (@bob)`, else `5 (Bob B)`,
    else the bare id. One format everywhere so an operator can grep for a
    player by either id or handle (see CLAUDE.md's "Logging")."""
    if username:
        return f"{user_id} (@{username})"
    if name:
        return f"{user_id} ({name})"
    return str(user_id)


def describe_player_id(session: Session, telegram_user_id: int) -> str:
    """describe_person() for a player known only by id, using the stored
    username if there is one. Cheap: the row is usually already in the
    session's identity map."""
    player = session.get(Player, telegram_user_id)
    return describe_person(telegram_user_id, username=player.username if player else None)


def get_or_create_player(
    session: Session, telegram_user_id: int, *, username: str | None = None, grant: bool = True
) -> Player:
    """Look up a player, creating the row if this is their first time. On
    an existing row, opportunistically refreshes `username` — unless the
    caller has none to offer, which must not blank out a handle we
    already know (it's what /correct and /skip match against). A new
    player also receives the starting 💠 balance (services/economy/)
    unless `grant=False` (the bot's own row, which has no use for it).

    The creation branch logs at INFO because a person entering the game
    for the first time is a real event; the refresh branch stays silent
    on purpose, since commands/helpers/player_tracking.py calls this on
    *every* update and an unchanged username is not news."""
    player = session.get(Player, telegram_user_id)
    if player is None:
        player = Player(telegram_user_id=telegram_user_id, username=username)
        session.add(player)
        # Flush before the ledger row so its FK target exists.
        session.flush()
        logger.info(
            "first time seeing player {player}",
            player=describe_person(telegram_user_id, username=username),
            player_id=telegram_user_id,
        )
        if grant:
            starting_balance = economy_config.get_amounts(session)[
                economy_config.EconomyKey.STARTING_BALANCE
            ]
            if starting_balance > 0:
                wallet.credit(
                    session, player, starting_balance, wallet.LedgerEntry(CurrencyReason.GRANT)
                )
    elif username is not None:
        player.username = username
    return player


def find_player_by_username(session: Session, username: str) -> Player | None:
    """Case-insensitive lookup by the opportunistically-cached username —
    used by /correct, which takes a plain @username rather than a reply."""
    stmt = select(Player).where(func.lower(Player.username) == username.lower())
    return session.scalars(stmt).first()


@dataclass(frozen=True)
class LeaderRow:
    player_id: int
    wins: int
    currency: int
    points: int  # achievement 🏆 points
    title_key: str | None


def _leaderboard_stmt():
    points = (
        select(AchievementGrant.player_id, func.sum(AchievementGrant.points).label("points"))
        .group_by(AchievementGrant.player_id)
        .subquery()
    )
    total = func.coalesce(points.c.points, 0)
    return (
        select(Player.telegram_user_id, Player.wins, Player.currency, total, Player.title_key)
        .outerjoin(points, points.c.player_id == Player.telegram_user_id)
        .where(Player.wins > 0)
        .order_by(Player.wins.desc(), total.desc(), Player.telegram_user_id)
    )


def leaderboard(session: Session, *, limit: int, offset: int = 0) -> list[LeaderRow]:
    """All-time board: players with a win, by 👑 wins, then 🏆 points."""
    rows = session.execute(_leaderboard_stmt().limit(limit).offset(offset))
    found = [
        LeaderRow(pid, wins, currency, int(pts), title) for pid, wins, currency, pts, title in rows
    ]
    logger.debug(
        "leaderboard query returned {count} row(s) (limit {limit}, offset {offset})",
        count=len(found),
        limit=limit,
        offset=offset,
    )
    return found


def leaderboard_count(session: Session) -> int:
    stmt = select(func.count()).select_from(Player).where(Player.wins > 0)
    return int(session.scalar(stmt) or 0)


def leaderboard_rank(session: Session, player_id: int) -> int | None:
    for rank, row in enumerate(session.execute(_leaderboard_stmt()), start=1):
        if row[0] == player_id:
            return rank
    return None


def get_timezone(session: Session, telegram_user_id: int) -> ZoneInfo | None:
    """The player's saved IANA timezone (see /timezone), or None if never
    set (or no longer a valid zone name)."""
    player = session.get(Player, telegram_user_id)
    if player is None or player.timezone is None:
        return None
    return parse_timezone(player.timezone)


def set_timezone(session: Session, telegram_user_id: int, tz: ZoneInfo) -> None:
    player = get_or_create_player(session, telegram_user_id)
    player.timezone = tz.key
    logger.info(
        "{player} set their timezone to {timezone}",
        player=describe_player_id(session, telegram_user_id),
        player_id=telegram_user_id,
        timezone=tz.key,
    )


def remember_first_name(player: Player, first_name: str | None) -> None:
    """Refresh the stored first name; never blank one we already know."""
    if first_name:
        player.first_name = first_name[:USERNAME_LENGTH]


def display_name(session: Session, telegram_user_id: int) -> str:
    """How a person is named in group-facing text: @username, else their
    first name, else their id."""
    player = session.get(Player, telegram_user_id)
    if player is None:
        return str(telegram_user_id)
    if player.username:
        return f"@{player.username}"
    return player.first_name or str(telegram_user_id)
