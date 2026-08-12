from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
EN_STRUCTURED_PATH = RAW_DIR / "official_structured_articles_en.json"
AR_STRUCTURED_PATH = RAW_DIR / "official_structured_articles_ar.json"
MANIFEST_PATH = RAW_DIR / "source_manifest.json"
OFFICIAL_PAGE_EN = "https://uaelegislation.gov.ae/en/legislations/4011"
OFFICIAL_PAGE_AR = "https://uaelegislation.gov.ae/ar/legislations/4011"
EXPECTED_ARTICLE_COUNT = 1422


@dataclass(frozen=True, slots=True)
class CorpusArticle:
    article_number: int
    article_text_en: str
    article_text_ar: str
    source_dom_id_en: str
    source_dom_id_ar: str

    @property
    def source_url(self) -> str:
        return f"{OFFICIAL_PAGE_EN}#{self.source_dom_id_en}"

    @property
    def source_url_ar(self) -> str:
        return f"{OFFICIAL_PAGE_AR}#{self.source_dom_id_ar}"


class CorpusFormatError(ValueError):
    """Raised when committed source data is incomplete or inconsistent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def combined_source_hash() -> str:
    digest = hashlib.sha256()
    for path in (EN_STRUCTURED_PATH, AR_STRUCTURED_PATH):
        digest.update(path.name.encode("utf-8"))
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def _load_records(path: Path, text_key: str) -> dict[int, dict[str, Any]]:
    if not path.exists():
        raise CorpusFormatError(f"Required official source extract is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise CorpusFormatError(f"Expected a JSON array in {path}")

    records: dict[int, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise CorpusFormatError(f"Non-object record in {path}")
        number = int(item.get("article_number", 0))
        text = str(item.get(text_key, "")).strip()
        dom_id = str(item.get("source_dom_id", "")).strip()
        if number < 1 or not text or not dom_id:
            raise CorpusFormatError(
                f"Invalid Article {number or '?'} in {path.name}: text and DOM id are required"
            )
        if number in records:
            raise CorpusFormatError(f"Duplicate Article {number} in {path.name}")
        records[number] = {text_key: text, "source_dom_id": dom_id}
    return records


def load_corpus() -> list[CorpusArticle]:
    english = _load_records(EN_STRUCTURED_PATH, "article_text_en")
    arabic = _load_records(AR_STRUCTURED_PATH, "article_text_ar")
    expected = set(range(1, EXPECTED_ARTICLE_COUNT + 1))

    for language, records in (("English", english), ("Arabic", arabic)):
        actual = set(records)
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing or unexpected:
            raise CorpusFormatError(
                f"{language} source is not complete. Missing={missing[:10]}, "
                f"unexpected={unexpected[:10]}"
            )

    return [
        CorpusArticle(
            article_number=number,
            article_text_en=english[number]["article_text_en"],
            article_text_ar=arabic[number]["article_text_ar"],
            source_dom_id_en=english[number]["source_dom_id"],
            source_dom_id_ar=arabic[number]["source_dom_id"],
        )
        for number in range(1, EXPECTED_ARTICLE_COUNT + 1)
    ]


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        raise CorpusFormatError(f"Source manifest is missing: {MANIFEST_PATH}")
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CorpusFormatError("Source manifest must be a JSON object")
    return payload
