from __future__ import annotations

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from scripts.corpus_io import EXPECTED_ARTICLE_COUNT, RAW_DIR

_ARTICLE_HEADING = re.compile(r"^(?:Article|المادة)\s*\(([0-9٠-٩۰-۹]+)\)$", re.IGNORECASE)
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


class OfficialArticleParser(HTMLParser):
    """Extract only official `content_` article blocks from a saved portal page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.capture_depth: int | None = None
        self.current_id: str | None = None
        self.in_heading = False
        self.in_text_area = False
        self.heading_parts: list[str] = []
        self.text_parts: list[str] = []
        self.records: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        classes = set((attrs_map.get("class") or "").split())
        void_elements = {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }
        if tag in void_elements:
            if tag == "br" and self.in_text_area:
                self.text_parts.append("\n")
            return
        self.depth += 1
        if self.capture_depth is None and tag == "div" and "content_" in classes:
            self.capture_depth = self.depth
            self.current_id = attrs_map.get("id")
        if self.capture_depth is None:
            return
        if tag == "h4":
            self.in_heading = True
        if tag == "div" and {"text_area", "mm_cnt"}.issubset(classes):
            self.in_text_area = True

    def handle_endtag(self, tag: str) -> None:
        if self.capture_depth is not None:
            if tag == "h4":
                self.in_heading = False
            if self.in_text_area and tag in {"p", "li"}:
                self.text_parts.append("\n")
            if self.depth == self.capture_depth and tag == "div":
                heading = " ".join("".join(self.heading_parts).split())
                text = _normalize_text("".join(self.text_parts))
                if self.current_id and heading and text:
                    self.records.append((self.current_id, heading, text))
                self.capture_depth = None
                self.current_id = None
                self.heading_parts = []
                self.text_parts = []
                self.in_heading = False
                self.in_text_area = False
        self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_heading:
            self.heading_parts.append(data)
        if self.in_text_area:
            self.text_parts.append(data)


def _normalize_text(value: str) -> str:
    lines = [line.replace("\xa0", " ").strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def parse_snapshot(path: Path, text_key: str) -> list[dict[str, object]]:
    parser = OfficialArticleParser()
    parser.feed(path.read_text(encoding="utf-8"))
    records: dict[int, dict[str, object]] = {}
    for dom_id, heading, text in parser.records:
        match = _ARTICLE_HEADING.fullmatch(heading)
        if match is None:
            continue
        number = int(match.group(1).translate(_DIGITS))
        if number not in range(1, EXPECTED_ARTICLE_COUNT + 1):
            continue
        if number in records:
            raise ValueError(f"Duplicate Article {number} in {path}")
        records[number] = {
            "article_number": number,
            text_key: text,
            "source_dom_id": dom_id,
        }
    expected = set(range(1, EXPECTED_ARTICLE_COUNT + 1))
    if set(records) != expected:
        missing = sorted(expected - set(records))
        raise ValueError(f"{path} does not contain the complete 1-1422 range: {missing[:10]}")
    return [records[number] for number in sorted(records)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild structured UAE article extracts")
    parser.add_argument("--check", action="store_true", help="Compare with committed extracts")
    args = parser.parse_args()
    pairs = (
        (
            RAW_DIR / "official_article_dom_2025_en.html",
            RAW_DIR / "official_structured_articles_en.json",
            "article_text_en",
        ),
        (
            RAW_DIR / "official_article_dom_2025_ar.html",
            RAW_DIR / "official_structured_articles_ar.json",
            "article_text_ar",
        ),
    )
    for snapshot, output, key in pairs:
        parsed = parse_snapshot(snapshot, key)
        rendered = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            committed = json.loads(output.read_text(encoding="utf-8"))
            if parsed != committed:
                raise ValueError(f"Parsed output differs from {output}")
            print(f"matched {len(parsed)} articles: {output}")
        else:
            output.write_text(rendered, encoding="utf-8")
            print(f"wrote {len(parsed)} articles: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
