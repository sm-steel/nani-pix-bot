"""Plain-string helpers shared by more than one message builder."""


def cut_at_line(text: str, limit: int, marker: str) -> str:
    """`text` if it fits in `limit` characters; otherwise as many whole
    lines as fit together with `marker` (a single over-long line is cut
    mid-line), then `marker`."""
    if len(text) <= limit:
        return text
    cut = text[: limit - len(marker)]
    last_newline = cut.rfind("\n")
    if last_newline > 0:
        cut = cut[:last_newline]
    return cut + marker
