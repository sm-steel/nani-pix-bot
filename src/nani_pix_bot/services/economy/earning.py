"""Applies the currency reward rules (rewards.py) to real games. Called by
the command layer right after record_guess (/guess), force_win
(/correct) and activate_game (DM setup confirm); each call returns what
was earned so the handler can show it in the reply it already sends.
Every fact it needs (the game's guess count, wrong-guess earnings so
far) is read back from the DB."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CurrencyReason
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import players
from nani_pix_bot.services.economy import bounty, config, rewards, wallet
from nani_pix_bot.services.economy.config import EconomyKey
from nani_pix_bot.services.game.hard_mode import HARD_MODE_WIN_AWARD


@dataclass(frozen=True)
class Earnings:
    guess: int = 0  # first-guess bonus + wrong-guess reward, to the guesser
    win: int = 0  # to the winner
    setter: int = 0  # to the game's starter
    bounty: int = 0  # the pot, to the winner — its own line, not part of player_total

    @property
    def player_total(self) -> int:
        return self.guess + self.win


def _player(session: Session, user_id: int) -> Player:
    player = session.get(Player, user_id)
    if player is None:
        msg = f"currency award for unknown player {user_id}"
        raise ValueError(msg)
    return player


def _pay(session: Session, player: Player, amount: int, reason: CurrencyReason, game: Game) -> int:
    """Credit only a positive amount — an admin may set any amount to 0."""
    if amount <= 0:
        logger.debug("{reason} is 0 💠, nothing credited", reason=reason.value, game_id=game.id)
        return 0
    wallet.credit(session, player, amount, wallet.LedgerEntry(reason, game_id=game.id))
    return amount


def _stage_number(game: Game) -> int:
    if game.hard_mode:
        if game.hard_mode_turn is None:
            msg = f"hard-mode game {game.id} has no hard_mode_turn"
            raise ValueError(msg)
        return game.hard_mode_turn
    if game.current_stage is None:
        msg = f"game {game.id} has no current_stage"
        raise ValueError(msg)
    return game_service.STAGE_ORDER.index(game.current_stage) + 1


def _setter_eligible(game: Game, *, winner_id: int, stage: int) -> bool:
    # HARD MODE games are bot-started — no human setter to reward.
    return not game.hard_mode and rewards.setter_rewarded(stage) and game.starter_id != winner_id


def award_win(session: Session, game: Game, *, winner_id: int) -> Earnings:
    amounts = config.get_amounts(session)
    winner = _player(session, winner_id)
    stage = _stage_number(game)
    multiplier = HARD_MODE_WIN_AWARD if game.hard_mode else 1
    win = _pay(
        session,
        winner,
        rewards.win_reward(amounts, stage_number=stage, multiplier=multiplier),
        CurrencyReason.WIN,
        game,
    )
    setter = 0
    if _setter_eligible(game, winner_id=winner_id, stage=stage):
        starter = _player(session, game.starter_id)
        setter = _pay(session, starter, amounts[EconomyKey.SETTER], CurrencyReason.SETTER, game)
    pot = bounty.pay_out(session, game, winner)
    logger.info(
        "win pays {win} 💠 to {winner} (stage {stage}), {setter_pay} 💠 to setter {setter},"
        " {bounty} 💠 bounty",
        win=win,
        winner=players.describe_player_id(session, winner_id),
        winner_id=winner_id,
        stage=stage,
        setter_pay=setter,
        setter=players.describe_player_id(session, game.starter_id),
        setter_id=game.starter_id,
        bounty=pot,
        game_id=game.id,
    )
    return Earnings(win=win, setter=setter, bounty=pot)


def award_guess(session: Session, game: Game, *, guesser_id: int, won: bool) -> Earnings:
    amounts = config.get_amounts(session)
    guesser = _player(session, guesser_id)
    first = 0
    # record_guess has already counted this guess, so the game's first
    # guess is exactly the one that brought the count to 1.
    if game.total_guess_count == 1:
        first = _pay(
            session, guesser, amounts[EconomyKey.FIRST_GUESS], CurrencyReason.FIRST_GUESS, game
        )
    wrong = 0
    if not won:
        earned = wallet.game_total(
            session, player_id=guesser_id, game_id=game.id, reason=CurrencyReason.WRONG_GUESS
        )
        wrong = _pay(
            session,
            guesser,
            rewards.wrong_guess_reward(amounts, earned_this_game=earned),
            CurrencyReason.WRONG_GUESS,
            game,
        )
    if first or wrong:
        # The guesser is the update's own user (only /guess pays this), so
        # the log context already names them.
        logger.info(
            "guess pays {amount} 💠 (first-guess bonus {first} 💠, wrong-guess reward {wrong} 💠)",
            amount=first + wrong,
            first=first,
            wrong=wrong,
            game_id=game.id,
        )
    if not won:
        return Earnings(guess=first + wrong)
    win = award_win(session, game, winner_id=guesser_id)
    return Earnings(guess=first, win=win.win, setter=win.setter, bounty=win.bounty)


def award_prompt_start(session: Session, game: Game) -> int:
    if game.hard_mode or game.turn_received_at is None:
        return 0
    if not rewards.is_prompt_start(
        created_at=game.created_at, turn_received_at=game.turn_received_at
    ):
        logger.debug("not a prompt start, no bonus", game_id=game.id)
        return 0
    amount = config.get_amounts(session)[EconomyKey.PROMPT_TURN]
    paid = _pay(
        session, _player(session, game.starter_id), amount, CurrencyReason.PROMPT_TURN, game
    )
    if paid > 0:
        # Only the starter's own preview confirm pays this, so the log
        # context already names them.
        logger.info("prompt-turn bonus {amount} 💠 to the starter", amount=paid, game_id=game.id)
    return paid
