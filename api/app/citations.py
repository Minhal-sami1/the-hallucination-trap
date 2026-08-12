"""Parse legal article references from generated text.

The parser returns normalized values and preserves the source text.  It does
not decide whether a reference is legally valid; verification is a separate
stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class CitationReference:
    """A citation candidate found in answer text."""

    raw: str
    article_number: str | None
    law_number: str | None = None
    law_year: int | None = None
    law_name: str | None = None
    language: str = "en"
    is_partial: bool = False


_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)


def _normalize_number(value: str | None) -> str | None:
    if value is None:
        return None
    translated = value.translate(_DIGIT_TRANSLATION)
    return translated.lstrip("0") or "0"


_ENGLISH_CITATION = re.compile(
    r"(?P<raw>"
    r"(?<!\w)(?P<label>Article|Art\.?|Section|s\.)\s*"
    r"(?:[#:\-\(\[]\s*)?"
    r"(?P<article>\d+)(?:\s*[\)\]])?"
    r"(?:(?:\s+of\s+|,\s*)"
    r"(?P<law_name>Federal\s+(?:Law|Decree\s+by\s+Law|Decree[-\s]?Law))\s+"
    r"No\.?\s*[\(\[]?\s*(?P<law_number>\d+)(?:\s*[\)\]])?"
    r"(?:\s+of\s+(?P<law_year>\d{4}))?"
    r")?"
    r")",
    re.IGNORECASE,
)

_ENGLISH_LAW_SCOPE = re.compile(
    r"(?P<law_name>Federal\s+(?:Law|Decree\s+by\s+Law|Decree[-\s]?Law))\s+"
    r"No\.?\s*[\(\[]?\s*(?P<law_number>\d+)(?:\s*[\)\]])?"
    r"\s+of\s+(?P<law_year>\d{4})",
    re.IGNORECASE,
)

_ARABIC_LAW_SCOPE = re.compile(
    r"(?P<law_name>"
    r"(?:القانون|للقانون)(?:\s+الاتحادي)?|"
    r"(?:المرسوم|مرسوم)\s+بقانون\s+اتحادي"
    r")"
    r"(?:\s+رقم)?\s*[\(\[\{]?\s*(?P<law_number>\d+)\s*[\)\]\}]?"
    r"\s+(?:لسنة|سنة)\s*(?P<law_year>\d{4})"
)

_UNRESOLVED_SCOPE_EN = re.compile(
    r"\b(?:(?:former|archived|previous|repealed)\s+"
    r"(?:Civil\s+Transactions\s+)?Law|that\s+former\s+law)\b|"
    r"\bFederal\s+(?:Law|Decree\s+by\s+Law|Decree[-\s]?Law)\b",
    re.IGNORECASE,
)
_UNRESOLVED_SCOPE_AR = re.compile(
    r"(?:قانون\s+المعاملات\s+المدنية|القانون|قانون)\s+"
    r"(?:السابق|القديم|الملغى|المؤرشف)|"
    r"(?:القانون|للقانون)(?:\s+الاتحادي)?|"
    r"(?:المرسوم|مرسوم)\s+بقانون\s+اتحادي"
)

_ARTICLE_MENTION = re.compile(
    r"(?<!\w)(?:Article|Art\.?|Section|s\.)\s*[#:\-\(\[]*\s*\d+|"
    r"(?:[وف]?(?:للمادة|[بك]?المادة))\s*(?:رقم\s*)?[#:\-\(\[\{]*\s*\d+",
    re.IGNORECASE,
)
_SHARED_SCOPE_GAP = re.compile(
    r"\s*(?:(?:[,،]\s*)|(?:,?\s*(?:and|or)\s*)|(?:[,،]?\s*و\s*))?",
    re.IGNORECASE,
)

_ARABIC_CITATION = re.compile(
    r"(?P<raw>"
    r"(?:[وف]?(?:للمادة|[بك]?المادة))\s*(?:رقم\s*)?"
    r"(?:[#:\-\(\[\{]\s*)?(?P<article>\d+)(?:\s*[\)\]\}])?"
    r"(?:\s+من\s+"
    r"(?P<law_name>"
    r"القانون(?:\s+الاتحادي)?|(?:المرسوم|مرسوم)\s+بقانون\s+اتحادي"
    r")"
    r"(?:\s+رقم)?\s*[\(\[\{]?\s*(?P<law_number>\d+)(?:\s*[\)\]\}])?"
    r"(?:\s+(?:لسنة|سنة)\s*(?P<law_year>\d{4}))?"
    r")?"
    r")"
)

_TRAILING_PARTIAL_ENGLISH = re.compile(
    r"(?P<raw>"
    r"(?<!\w)(?:Article|Art\.?|Section|s\.)\s*"
    r"(?:(?:[#:\-\(\[]\s*)?(?:\?{1,3}|\.{3}|…))?"
    r")\s*$",
)

_MALFORMED_ENGLISH = re.compile(
    r"(?P<raw>"
    r"(?<!\w)(?:Article|Art\.?|Section|s\.)\s*"
    r"(?:[#:\-\(\[]\s*)?(?:\?{1,3}|\.{3}|…)"
    r")"
)

_MALFORMED_ARABIC = re.compile(
    r"(?P<raw>"
    r"(?:[وف]?(?:للمادة|[بك]?المادة))\s*(?:رقم\s*)?"
    r"(?:[#:\-\(\[\{]\s*)?(?:[؟?]{1,3}|\.{3}|…)"
    r")"
)

_TRAILING_PARTIAL_ARABIC = re.compile(
    r"(?P<raw>"
    r"(?:[وف]?(?:للمادة|[بك]?المادة))\s*(?:رقم\s*)?"
    r"(?:(?:[#:\-\(\[\{]\s*)?(?:\?{1,3}|\.{3}|…))?"
    r")\s*$"
)


def _bounded_clause(text: str, start: int, end: int) -> tuple[int, str]:
    """Return one short sentence-like window and its absolute offset."""

    left = max(0, start - 300)
    right = min(len(text), end + 300)
    sentence_breaks = re.finditer(
        r"[!?؟؛;](?:\s+|$)|(?<!No)\.(?:\s+|$)",
        text,
        flags=re.IGNORECASE,
    )
    for boundary in sentence_breaks:
        if boundary.end() <= start:
            left = max(left, boundary.end())
        elif boundary.start() >= end:
            right = min(right, boundary.start())
            break

    return left, text[left:right]


def _direct_to_citation(
    clause: str,
    law_start: int,
    law_end: int,
    citation_start: int,
    citation_end: int,
) -> bool:
    if law_end <= citation_start:
        between = clause[law_end:citation_start]
    elif law_start >= citation_end:
        between = clause[citation_end:law_start]
    else:
        return True
    return _ARTICLE_MENTION.search(between) is None


def _contextual_law_scope(
    text: str,
    start: int,
    end: int,
    language: str,
) -> tuple[str | None, str | None, int | None, bool]:
    """Resolve one nearby law identity or mark an unresolved scope as partial."""

    offset, clause = _bounded_clause(text, start, end)
    relative_start = start - offset
    relative_end = end - offset
    pattern = _ARABIC_LAW_SCOPE if language == "ar" else _ENGLISH_LAW_SCOPE
    matches = [
        match
        for match in pattern.finditer(clause)
        if _direct_to_citation(
            clause, match.start(), match.end(), relative_start, relative_end
        )
    ]
    unresolved_pattern = _UNRESOLVED_SCOPE_AR if language == "ar" else _UNRESOLVED_SCOPE_EN
    unresolved_matches = [
        match
        for match in unresolved_pattern.finditer(clause)
        if _direct_to_citation(
            clause, match.start(), match.end(), relative_start, relative_end
        )
        and not any(
            scope.start() <= match.start() and match.end() <= scope.end()
            for scope in matches
        )
    ]
    if len(matches) == 1 and not unresolved_matches:
        match = matches[0]
        year = _normalize_number(match.group("law_year"))
        return (
            match.group("law_name"),
            _normalize_number(match.group("law_number")),
            int(year) if year else None,
            False,
        )
    return None, None, None, bool(unresolved_matches or len(matches) > 1)


def parse_citations(text: str) -> list[CitationReference]:
    """Return citation candidates in their source order."""

    references: list[tuple[int, CitationReference]] = []
    for match in _ENGLISH_CITATION.finditer(text):
        law_year = match.group("law_year")
        law_name = match.group("law_name")
        law_number = _normalize_number(match.group("law_number"))
        unresolved_scope = False
        if law_number is None:
            law_name, law_number, contextual_year, unresolved_scope = (
                _contextual_law_scope(text, match.start(), match.end(), "en")
            )
            law_year = str(contextual_year) if contextual_year is not None else None
        references.append(
            (
                match.start(),
                CitationReference(
                    raw=match.group("raw"),
                    article_number=_normalize_number(match.group("article")),
                    law_number=law_number,
                    law_year=int(law_year) if law_year else None,
                    law_name=law_name,
                    language="en",
                    is_partial=unresolved_scope,
                ),
            )
        )
    for match in _ARABIC_CITATION.finditer(text):
        law_year = _normalize_number(match.group("law_year"))
        law_name = match.group("law_name")
        law_number = _normalize_number(match.group("law_number"))
        unresolved_scope = False
        if law_number is None:
            law_name, law_number, contextual_year, unresolved_scope = (
                _contextual_law_scope(text, match.start(), match.end(), "ar")
            )
            law_year = str(contextual_year) if contextual_year is not None else None
        references.append(
            (
                match.start(),
                CitationReference(
                    raw=match.group("raw"),
                    article_number=_normalize_number(match.group("article")),
                    law_number=law_number,
                    law_year=int(law_year) if law_year else None,
                    law_name=law_name,
                    language="ar",
                    is_partial=unresolved_scope,
                ),
            )
        )
    for match in _MALFORMED_ENGLISH.finditer(text):
        references.append(
            (
                match.start(),
                CitationReference(
                    raw=match.group("raw"),
                    article_number=None,
                    language="en",
                    is_partial=True,
                ),
            )
        )
    for match in _MALFORMED_ARABIC.finditer(text):
        references.append(
            (
                match.start(),
                CitationReference(
                    raw=match.group("raw"),
                    article_number=None,
                    language="ar",
                    is_partial=True,
                ),
            )
        )
    partial_match = _TRAILING_PARTIAL_ENGLISH.search(text)
    if partial_match:
        references.append(
            (
                partial_match.start(),
                CitationReference(
                    raw=partial_match.group("raw"),
                    article_number=None,
                    language="en",
                    is_partial=True,
                ),
            )
        )
    partial_match = _TRAILING_PARTIAL_ARABIC.search(text)
    if partial_match:
        references.append(
            (
                partial_match.start(),
                CitationReference(
                    raw=partial_match.group("raw"),
                    article_number=None,
                    language="ar",
                    is_partial=True,
                ),
            )
        )
    ordered_references = sorted(references, key=lambda item: item[0])
    groups: list[list[int]] = []
    current_group: list[int] = []
    for index, (position, reference) in enumerate(ordered_references):
        if reference.article_number is None:
            if current_group:
                groups.append(current_group)
                current_group = []
            continue
        if current_group:
            previous_index = current_group[-1]
            previous_position, previous = ordered_references[previous_index]
            gap_start = previous_position + len(previous.raw)
            gap = text[gap_start:position]
            joined_arabic = reference.raw.startswith(("والمادة", "وبالمادة"))
            if not (_SHARED_SCOPE_GAP.fullmatch(gap) and (gap.strip() or joined_arabic)):
                groups.append(current_group)
                current_group = []
        current_group.append(index)
    if current_group:
        groups.append(current_group)

    for group in groups:
        explicit_scopes = {
            (reference.law_name, reference.law_number, reference.law_year)
            for index in group
            for _, reference in [ordered_references[index]]
            if reference.law_number is not None and reference.law_year is not None
        }
        if len(explicit_scopes) == 1:
            law_name, law_number, law_year = next(iter(explicit_scopes))
            for index in group:
                position, reference = ordered_references[index]
                if reference.law_number is None:
                    ordered_references[index] = (
                        position,
                        replace(
                            reference,
                            law_number=law_number,
                            law_year=law_year,
                            law_name=law_name,
                            is_partial=False,
                        ),
                    )
        elif len(explicit_scopes) > 1:
            for index in group:
                position, reference = ordered_references[index]
                ordered_references[index] = (
                    position,
                    replace(reference, law_number=None, law_year=None, is_partial=True),
                )

    unique_references: dict[tuple[int, str, str], CitationReference] = {}
    for position, reference in ordered_references:
        unique_references[(position, reference.raw, reference.language)] = reference
    return [
        reference
        for (_, _, _), reference in sorted(
            unique_references.items(), key=lambda item: item[0][0]
        )
    ]
