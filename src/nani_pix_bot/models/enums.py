import enum


class GameStatus(enum.Enum):
    """A Game's lifecycle — see MECHANICS.md's game-flow section."""

    SETUP = "setup"
    ACTIVE = "active"
    WON = "won"
    UNSOLVED = "unsolved"


class PixelStage(enum.Enum):
    """Pixelation stages, blockiest to clearest — see MECHANICS.md."""

    X10 = "x10"
    X8 = "x8"
    X5 = "x5"
    X2 = "x2"


class SetupStep(enum.Enum):
    """Where a SETUP game's starter currently is in the multi-step DM
    identification flow — see MECHANICS.md's "Starting a game" section.
    Only meaningful while `Game.status` is `SETUP`; derived from the DB,
    never cached in user_data (issue #11)."""

    # Covers everything up to and including picking/typing a candidate
    # title (method choice, search, result pick, manual title+synonym) —
    # the finer-grained state within this step is read off Game.source
    # and title_english, not a separate enum value.
    PICKING_METHOD = "picking_method"
    AWAITING_PHOTO_CHANGE = "awaiting_photo_change"
    AWAITING_SYNONYM = "awaiting_synonym"
    CONFIRMING = "confirming"
