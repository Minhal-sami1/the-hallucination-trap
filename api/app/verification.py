"""Verify parsed citations against a traceable article repository."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .citations import CitationReference


class Verdict(StrEnum):
    """The three audit outcomes shown by the product."""

    VERIFIED = "VERIFIED"
    FABRICATED = "FABRICATED"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True, slots=True)
class LawRecord:
    law_name: str
    law_number: str
    law_year: int
    article_count: int
    law_name_ar: str | None = None


@dataclass(frozen=True, slots=True)
class LawIdentifier:
    """A law scope supplied by the answer or retrieval pipeline."""

    law_number: str
    law_year: int


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    law_name: str
    law_number: str
    law_year: int
    article_number: str
    article_text_en: str
    article_text_ar: str | None
    source_url: str
    source_url_ar: str | None = None


@runtime_checkable
class ArticleRepository(Protocol):
    """Storage seam implemented by memory and SQLAlchemy repositories."""

    def find_laws(
        self,
        *,
        law_number: str | None = None,
        law_year: int | None = None,
        law_name: str | None = None,
    ) -> Sequence[LawRecord]: ...

    def get_article(
        self, *, law_number: str, law_year: int, article_number: str
    ) -> ArticleRecord | None: ...


def _law_kind(value: str) -> str | None:
    """Return the enactment type without depending on title punctuation."""

    normalized = " ".join(value.casefold().replace("-", " ").split())
    if "decree" in normalized or "مرسوم بقانون" in normalized:
        return "federal_decree_law"
    if "federal law" in normalized or "القانون" in normalized:
        return "federal_law"
    return None


def law_name_matches(
    cited_name: str, official_name: str, official_name_ar: str | None = None
) -> bool:
    """Match law titles while keeping different enactment types separate."""

    cited_kind = _law_kind(cited_name)
    official_kinds = {
        kind
        for kind in (
            _law_kind(official_name),
            _law_kind(official_name_ar) if official_name_ar else None,
        )
        if kind is not None
    }
    if cited_kind is not None and official_kinds:
        return cited_kind in official_kinds

    normalized_cited = " ".join(cited_name.casefold().split())
    return any(
        normalized_cited in " ".join(name.casefold().split())
        for name in (official_name, official_name_ar)
        if name
    )


class InMemoryArticleRepository:
    """Deterministic repository used by unit tests and offline checks."""

    def __init__(self, *, laws: Sequence[LawRecord], articles: Sequence[ArticleRecord]) -> None:
        self._laws = tuple(laws)
        self._articles = {
            (article.law_number, article.law_year, article.article_number): article
            for article in articles
        }

    def find_laws(
        self,
        *,
        law_number: str | None = None,
        law_year: int | None = None,
        law_name: str | None = None,
    ) -> Sequence[LawRecord]:
        matches = self._laws
        if law_number is not None:
            matches = tuple(law for law in matches if law.law_number == law_number)
        if law_year is not None:
            matches = tuple(law for law in matches if law.law_year == law_year)
        if law_name is not None:
            matches = tuple(
                law
                for law in matches
                if law_name_matches(law_name, law.law_name, law.law_name_ar)
            )
        return matches

    def get_article(
        self, *, law_number: str, law_year: int, article_number: str
    ) -> ArticleRecord | None:
        return self._articles.get((law_number, law_year, article_number))


@dataclass(frozen=True, slots=True)
class CitationVerdict:
    verdict: Verdict
    citation: CitationReference
    reason: str
    article: ArticleRecord | None = None

    @property
    def excerpt(self) -> str | None:
        if self.article is None:
            return None
        text = (
            self.article.article_text_ar
            if self.citation.language == "ar" and self.article.article_text_ar
            else self.article.article_text_en
        )
        return text[:200]

    @property
    def source_url(self) -> str | None:
        if self.article is None:
            return None
        if self.citation.language == "ar" and self.article.source_url_ar:
            return self.article.source_url_ar
        return self.article.source_url


def verify_citation(
    citation: CitationReference,
    repository: ArticleRepository,
    *,
    law_context: LawIdentifier | None = None,
) -> CitationVerdict:
    """Verify one citation without depending on a database implementation."""

    if citation.article_number is None:
        return CitationVerdict(
            verdict=Verdict.UNVERIFIABLE,
            citation=citation,
            reason="Citation is incomplete; no article number is available.",
        )

    if citation.is_partial:
        return CitationVerdict(
            verdict=Verdict.UNVERIFIABLE,
            citation=citation,
            reason="Citation is incomplete; its law scope could not be resolved.",
        )

    law_number = citation.law_number
    law_year = citation.law_year
    if law_context is not None and law_number is None and citation.is_partial is False:
        law_number = law_context.law_number
        law_year = law_context.law_year
    elif law_context is not None and law_number == law_context.law_number and law_year is None:
        law_year = law_context.law_year

    if law_number is None and law_year is None and citation.law_name is None:
        return CitationVerdict(
            verdict=Verdict.UNVERIFIABLE,
            citation=citation,
            reason=("Citation does not identify a law; verification needs a law context."),
        )

    laws = repository.find_laws(
        law_number=law_number,
        law_year=law_year,
        law_name=citation.law_name,
    )
    if not laws:
        if law_number is not None and law_year is not None:
            law_type = citation.law_name or "Federal Law"
            scope = f"{law_type} No. {law_number} of {law_year}"
        else:
            scope = citation.raw
        return CitationVerdict(
            verdict=Verdict.UNVERIFIABLE,
            citation=citation,
            reason=f"{scope} is outside the loaded corpus.",
        )

    if len(laws) != 1:
        return CitationVerdict(
            verdict=Verdict.UNVERIFIABLE,
            citation=citation,
            reason="The citation cannot be resolved to one law in the loaded corpus.",
        )

    law = laws[0]
    article = repository.get_article(
        law_number=law.law_number,
        law_year=law.law_year,
        article_number=citation.article_number,
    )
    if article is None:
        return CitationVerdict(
            verdict=Verdict.FABRICATED,
            citation=citation,
            reason=(
                f"{law.law_name} contains {law.article_count:,} articles; "
                f"Article {citation.article_number} does not exist."
            ),
        )

    return CitationVerdict(
        verdict=Verdict.VERIFIED,
        citation=citation,
        article=article,
        reason="Exact article match in the loaded corpus.",
    )
