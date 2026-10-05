"""Test-only ledger audit: the balance a player *should* have according to
the `currency_transfers` ledger, for checking that wallet.py kept the cached
`players.currency` in step with it. Moved out of services/economy/wallet.py
(issue #239), since nothing in the bot itself needs it."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import CurrencyParty


def ledger_balance(session: Session, player_id: int) -> int:
    """Transfers into the player minus transfers out of them."""
    received = select(func.coalesce(func.sum(CurrencyTransfer.amount), 0)).where(
        CurrencyTransfer.to_type == CurrencyParty.PLAYER, CurrencyTransfer.to_player_id == player_id
    )
    sent = select(func.coalesce(func.sum(CurrencyTransfer.amount), 0)).where(
        CurrencyTransfer.from_type == CurrencyParty.PLAYER,
        CurrencyTransfer.from_player_id == player_id,
    )
    return int(session.scalar(received) or 0) - int(session.scalar(sent) or 0)
