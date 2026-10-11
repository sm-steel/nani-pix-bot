"""An anime's MAL genres and themes (seasons spec §5): what a season gate
checks. Tenrai reports MAL ids; Shikimori's ids differ for some tags, so
its tags carry mal_id=None and are matched by kind + name."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnimeTag:
    kind: str  # "genre" | "theme" (anything else a provider reports is kept)
    name: str
    mal_id: int | None


def to_json(tags: Iterable[AnimeTag]) -> list[dict[str, Any]]:
    return [{"kind": t.kind, "name": t.name, "mal_id": t.mal_id} for t in tags]


def from_json(raw: Sequence[Any] | None) -> list[AnimeTag]:
    found = []
    for item in raw or []:
        if (
            isinstance(item, dict)
            and isinstance(item.get("kind"), str)
            and isinstance(item.get("name"), str)
        ):
            mal_id = item.get("mal_id")
            found.append(
                AnimeTag(item["kind"], item["name"], mal_id if isinstance(mal_id, int) else None)
            )
    return found
