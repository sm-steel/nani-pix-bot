import enum


class GameStatus(enum.Enum):
    """A Game's lifecycle — see MECHANICS.md's "Game lifecycle" section
    for the full state diagram."""

    SETUP = "setup"  # starter is picking/confirming the anime in DM
    ACTIVE = "active"  # posted to the group, guessing open
    WON = "won"  # a /guess matched, or /correct forced it (terminal)
    UNSOLVED = "unsolved"  # stage exhaustion or 2-day timeout (terminal)


class PixelStage(enum.Enum):
    """Pixelation stages, blockiest to clearest — see MECHANICS.md's
    "Pixelation stages" table. Named ordinally (not by a scale factor —
    that was tried before and the digits stopped meaning anything real).
    Only the 5 members and their order are fixed here; each stage's
    actual target width and wrong-guess limit are admin-configurable at
    runtime, not baked into this enum — see services/stage_config.py."""

    STAGE_1 = "stage_1"  # shown first — blockiest/hardest
    STAGE_2 = "stage_2"
    STAGE_3 = "stage_3"
    STAGE_4 = "stage_4"
    STAGE_5 = "stage_5"  # clearest stage before reveal


class SetupStep(enum.Enum):
    """Where a SETUP game's starter currently is in the multi-step DM
    identification flow — see MECHANICS.md's "Starting a game" section.
    Only meaningful while `Game.status` is `SETUP`; derived from the DB,
    never cached in user_data (issue #11)."""

    # Covers everything up to and including picking/typing a candidate
    # title (method choice, search, result pick, manual title+synonym) —
    # the finer-grained state within this step is read off Game.source
    # and title_english, not a separate enum value.
    PICKING_METHOD = "picking_method"  # method choice, search, or manual title/synonym entry
    AWAITING_PHOTO_CHANGE = "awaiting_photo_change"  # preview's "Change image" tapped
    AWAITING_SYNONYM = "awaiting_synonym"  # preview's "Add a synonym" tapped
    CONFIRMING = "confirming"  # showing the preview, waiting for a button tap
