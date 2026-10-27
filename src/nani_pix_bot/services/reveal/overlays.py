"""Win overlays for the reveal ending (seasons spec §3 `win_effect`): a key →
a renderer giving a qtrle/argb .mov clip for a badge height. Runs in the
reveal worker, so every renderer is a plain module-level function.
"confetti" is the default every winner gets; season effects are added here."""

from collections.abc import Callable, Mapping
from types import MappingProxyType

from nani_pix_bot.services.reveal import bone_rattle, celebration, petals, spirits

DEFAULT_OVERLAY = "confetti"
WIN_OVERLAYS: Mapping[str, Callable[[int], tuple[bytes, tuple[int, int]]]] = MappingProxyType(
    {
        DEFAULT_OVERLAY: celebration.confetti_clip,
        "petal_drift": petals.petal_drift_clip,
        "sakura_storm": petals.sakura_storm_clip,
        "bat_burst": spirits.bat_burst_clip,
        "hitodama": spirits.hitodama_clip,
        "bone_rattle": bone_rattle.bone_rattle_clip,
    }
)
