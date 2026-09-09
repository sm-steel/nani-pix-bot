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
