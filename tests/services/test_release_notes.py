import re
from pathlib import Path

import pytest

from nani_pix_bot.services import release_notes
from nani_pix_bot.services.release_notes import Release

LANGS = ("en", "ru")
SECTIONS = {
    "en": ("### ✨ New", "### 🛠 Fixed"),
    "ru": ("### ✨ Новое", "### 🛠 Исправлено"),
}
# Telegram's rich-message size limit (Bot API): a transport ceiling, not a content rule.
RICH_MESSAGE_LIMIT = 32768
HEADING = re.compile(r"^## v\d+\.\d+\.\d+ · \d{4}-\d{2}-\d{2}$")
FORBIDDEN = {
    "issue or PR number": re.compile(r"#\d+"),
    "commit hash": re.compile(r"\b[0-9a-f]{7,40}\b"),
    "file name": re.compile(r"\.py\b"),
}
CALL = re.compile(r"\b\w+\(\)")
CODE_SPAN = re.compile(r"`[^`]*`")


def _write(directory: Path, name: str, text: str) -> None:
    (directory / name).write_text(text, encoding="utf-8", newline="\n")


# --- parsing ---------------------------------------------------------------


def test_current_notes_are_page_zero_without_a_version(tmp_path: Path) -> None:
    _write(tmp_path, "release-notes-en.md", "### ✨ New\n- A thing.\n")

    releases = release_notes.load("en", tmp_path)

    assert releases == [Release(version=None, date=None, markdown="### ✨ New\n- A thing.")]


def test_previous_releases_are_split_on_their_headings(tmp_path: Path) -> None:
    _write(tmp_path, "release-notes-en.md", "### ✨ New\n- Now.\n")
    _write(
        tmp_path,
        "previous-releases-en.md",
        "## v1.2.0 · 2026-10-08\n\n### ✨ New\n- Newer.\n\n"
        "## v1.1.0 · 2026-10-01\n\n### 🛠 Fixed\n- Older.\n",
    )

    releases = release_notes.load("en", tmp_path)

    assert releases[1:] == [
        Release(version="1.2.0", date="2026-10-08", markdown="### ✨ New\n- Newer."),
        Release(version="1.1.0", date="2026-10-01", markdown="### 🛠 Fixed\n- Older."),
    ]


def test_an_empty_current_file_still_gives_page_zero(tmp_path: Path) -> None:
    _write(tmp_path, "release-notes-en.md", "\n")

    assert release_notes.load("en", tmp_path) == [Release(None, None, "")]


def test_missing_files_mean_no_notes_and_no_history(tmp_path: Path) -> None:
    assert release_notes.load("en", tmp_path) == [Release(None, None, "")]


def test_a_missing_file_logs_a_warning_with_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log_records
) -> None:
    monkeypatch.setattr(release_notes, "NOTES_DIR", tmp_path)
    _write(tmp_path, "release-notes-en.md", "- Now.")

    assert release_notes.load("en") == [Release(None, None, "- Now.")]

    warnings = [r for r in log_records if r.level == "WARNING"]
    assert len(warnings) == 1
    assert warnings[0].extra["path"] == str(tmp_path / "previous-releases-en.md")


@pytest.mark.parametrize("content", [b"\xff\xfe broken", None])
def test_an_unreadable_file_logs_an_error_and_reads_as_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log_records, content: bytes | None
) -> None:
    monkeypatch.setattr(release_notes, "NOTES_DIR", tmp_path)
    current = tmp_path / "release-notes-en.md"
    if content is None:
        current.mkdir()  # a directory where the file should be: OSError on read
    else:
        current.write_bytes(content)  # not UTF-8
    _write(tmp_path, "previous-releases-en.md", "## v1.0.0 · 2026-01-01\n- One.\n")

    releases = release_notes.load("en")

    assert releases == [Release(None, None, ""), Release("1.0.0", "2026-01-01", "- One.")]
    errors = [r for r in log_records if r.level == "ERROR"]
    assert len(errors) == 1
    assert errors[0].extra["path"] == str(current)


def test_text_before_the_first_heading_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "previous-releases-en.md", "stray\n## v1.0.0 · 2026-01-01\n- One.\n")

    assert release_notes.load("en", tmp_path)[1:] == [Release("1.0.0", "2026-01-01", "- One.")]


def test_a_heading_without_a_date_keeps_its_text_as_the_version(tmp_path: Path) -> None:
    _write(tmp_path, "previous-releases-en.md", "## v1.0.0\n- One.\n")

    assert release_notes.load("en", tmp_path)[1:] == [Release("1.0.0", None, "- One.")]


def test_at_most_four_previous_releases_are_loaded(tmp_path: Path) -> None:
    text = "".join(f"## v1.{n}.0 · 2026-01-0{n}\n- R{n}.\n" for n in range(1, 7))
    _write(tmp_path, "previous-releases-en.md", text)

    releases = release_notes.load("en", tmp_path)

    assert len(releases) == 1 + release_notes.MAX_PREVIOUS
    assert [r.version for r in releases[1:]] == ["1.1.0", "1.2.0", "1.3.0", "1.4.0"]


def test_the_language_code_is_matched_case_insensitively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The /language picker stores 'RU'; i18n.t lowercases it, so load must too.
    Asserts the file names asked for, since Windows filesystems ignore case."""
    _write(tmp_path, "release-notes-ru.md", "- Сейчас.\n")
    _write(tmp_path, "previous-releases-ru.md", "## v1.0.0 · 2026-01-01\n- Раньше.\n")
    read: list[str] = []
    original = release_notes._read

    def spy(path: Path) -> str:
        read.append(path.name)
        return original(path)

    monkeypatch.setattr(release_notes, "_read", spy)

    assert release_notes.load("RU", tmp_path) == [
        Release(None, None, "- Сейчас."),
        Release("1.0.0", "2026-01-01", "- Раньше."),
    ]
    assert read == ["release-notes-ru.md", "previous-releases-ru.md"]


# --- the real files ----------------------------------------------------------


def _sections(markdown: str) -> list[tuple[str, int]]:
    """(heading, bullet count) per section; fails on anything else."""
    sections: list[tuple[str, int]] = []
    for line in markdown.splitlines():
        if not line.strip():
            continue
        if line.startswith("### "):
            sections.append((line, 0))
        else:
            assert line.startswith("- "), f"not a bullet or section heading: {line!r}"
            assert sections, f"bullet before any section: {line!r}"
            heading, count = sections[-1]
            sections[-1] = (heading, count + 1)
    return sections


@pytest.mark.parametrize("lang", LANGS)
def test_real_notes_have_only_the_two_sections_each_with_bullets(lang: str) -> None:
    for release in release_notes.load(lang):
        sections = _sections(release.markdown)
        headings = [heading for heading, _ in sections]
        assert headings in ([], *([s] for s in SECTIONS[lang]), list(SECTIONS[lang])), headings
        for heading, count in sections:
            assert count >= 1, (release.version, heading)


@pytest.mark.parametrize("lang", LANGS)
def test_real_previous_releases_have_proper_headings(lang: str) -> None:
    path = release_notes.NOTES_DIR / f"previous-releases-{lang}.md"
    text = path.read_text(encoding="utf-8")
    headings = [line for line in text.splitlines() if line.startswith("## ")]

    assert text.startswith("## ")
    assert all(HEADING.match(h) for h in headings), headings
    assert len(headings) <= release_notes.MAX_PREVIOUS


def test_real_notes_are_in_step_between_languages() -> None:
    en, ru = release_notes.load("en"), release_notes.load("ru")

    assert [(r.version, r.date) for r in en] == [(r.version, r.date) for r in ru]
    for en_release, ru_release in zip(en, ru, strict=True):
        en_shape = [(SECTIONS["en"].index(h), n) for h, n in _sections(en_release.markdown)]
        ru_shape = [(SECTIONS["ru"].index(h), n) for h, n in _sections(ru_release.markdown)]
        assert en_shape == ru_shape, en_release.version


@pytest.mark.parametrize("lang", LANGS)
def test_real_notes_carry_no_internals(lang: str) -> None:
    for release in release_notes.load(lang):
        for name, pattern in FORBIDDEN.items():
            assert not pattern.search(release.markdown), (name, release.version)
        outside_code = CODE_SPAN.sub("", release.markdown)
        assert not CALL.search(outside_code), release.version


@pytest.mark.parametrize("lang", LANGS)
def test_every_real_release_fits_in_one_rich_message(lang: str) -> None:
    for release in release_notes.load(lang):
        assert len(release.markdown) <= RICH_MESSAGE_LIMIT, release.version
