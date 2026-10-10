"""Every guide page ships in both languages (issue #366)."""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs" / "site" / "src" / "content" / "docs"
PAGE_SUFFIXES = {".md", ".mdx"}


def _pages(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.suffix in PAGE_SUFFIXES}


def test_guide_has_pages() -> None:
    assert "index.mdx" in _pages(DOCS)


def test_every_english_page_has_a_russian_twin_and_back() -> None:
    pages = _pages(DOCS)
    english = {p for p in pages if not p.startswith("ru/")}
    russian = {p.removeprefix("ru/") for p in pages if p.startswith("ru/")}
    assert sorted(english - russian) == [], "missing in Russian"
    assert sorted(russian - english) == [], "missing in English"
