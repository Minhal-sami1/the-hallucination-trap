import pytest

from api.app.citations import parse_citations


def test_parses_a_full_english_federal_law_citation() -> None:
    citations = parse_citations(
        "The rule appears in Article 261 of Federal Law No. 5 of 1985."
    )

    assert len(citations) == 1
    assert citations[0].article_number == "261"
    assert citations[0].law_number == "5"
    assert citations[0].law_year == 1985
    assert citations[0].language == "en"
    assert citations[0].raw == "Article 261 of Federal Law No. 5 of 1985"


@pytest.mark.parametrize(
    "text, expected_law_name",
    [
        (
            "Article 261 of Federal Decree by Law No. 25 of 2025",
            "Federal Decree by Law",
        ),
        (
            "Art. 261 of Federal Decree-Law No. 25 of 2025",
            "Federal Decree-Law",
        ),
    ],
)
def test_parses_the_loaded_federal_decree_law_forms(
    text: str, expected_law_name: str
) -> None:
    citations = parse_citations(text)

    assert len(citations) == 1
    assert citations[0].article_number == "261"
    assert citations[0].law_number == "25"
    assert citations[0].law_year == 2025
    assert citations[0].law_name == expected_law_name
    assert citations[0].raw == text


@pytest.mark.parametrize(
    "text, expected_raw",
    [
        ("Article 261", "Article 261"),
        ("Art. 261", "Art. 261"),
        ("Art 261", "Art 261"),
        ("s.261", "s.261"),
        ("Section 261", "Section 261"),
    ],
)
def test_parses_short_english_article_forms(text: str, expected_raw: str) -> None:
    citations = parse_citations(text)

    assert len(citations) == 1
    assert citations[0].article_number == "261"
    assert citations[0].law_number is None
    assert citations[0].law_year is None
    assert citations[0].raw == expected_raw


def test_parses_an_arabic_federal_law_citation() -> None:
    citations = parse_citations("تنص المادة 261 من القانون الاتحادي رقم 5 على ذلك.")

    assert len(citations) == 1
    assert citations[0].article_number == "261"
    assert citations[0].law_number == "5"
    assert citations[0].law_year is None
    assert citations[0].law_name == "القانون الاتحادي"
    assert citations[0].language == "ar"
    assert citations[0].raw == "المادة 261 من القانون الاتحادي رقم 5"


def test_parses_the_loaded_arabic_federal_decree_law_form() -> None:
    text = "المادة ٢٦١ من مرسوم بقانون اتحادي رقم (٢٥) لسنة ٢٠٢٥"

    citations = parse_citations(text)

    assert len(citations) == 1
    assert citations[0].article_number == "261"
    assert citations[0].law_number == "25"
    assert citations[0].law_year == 2025
    assert citations[0].law_name == "مرسوم بقانون اتحادي"
    assert citations[0].language == "ar"
    assert citations[0].raw == text


def test_normalizes_arabic_indic_numerals_and_parentheses() -> None:
    citations = parse_citations(
        "راجع المادة (٢٦١) من القانون الاتحادي رقم (٥) لسنة ١٩٨٥."
    )

    assert len(citations) == 1
    assert citations[0].article_number == "261"
    assert citations[0].law_number == "5"
    assert citations[0].law_year == 1985
    assert citations[0].raw == ("المادة (٢٦١) من القانون الاتحادي رقم (٥) لسنة ١٩٨٥")


@pytest.mark.parametrize(
    "text, expected_raw",
    [
        ("تطبيقًا للمادة (١٤٨٦)", "للمادة (١٤٨٦)"),
        ("وبالمادة ٢٦١", "وبالمادة ٢٦١"),
        ("فالمادة ٢٦١", "فالمادة ٢٦١"),
    ],
)
def test_parses_arabic_article_with_joined_prepositions(
    text: str, expected_raw: str
) -> None:
    citations = parse_citations(text)

    assert len(citations) == 1
    assert citations[0].article_number in {"1486", "261"}
    assert citations[0].raw == expected_raw
    assert citations[0].language == "ar"


@pytest.mark.parametrize(
    "text, expected_raw, expected_language",
    [
        ("Article #261", "Article #261", "en"),
        ("Art-261", "Art-261", "en"),
        ("Section: 261", "Section: 261", "en"),
        ("المادة: ٢٦١", "المادة: ٢٦١", "ar"),
    ],
)
def test_recovers_common_malformed_separators(
    text: str, expected_raw: str, expected_language: str
) -> None:
    citations = parse_citations(text)

    assert len(citations) == 1
    assert citations[0].article_number == "261"
    assert citations[0].raw == expected_raw
    assert citations[0].language == expected_language


@pytest.mark.parametrize(
    "text, expected_raw, expected_language",
    [
        ("The model is still writing: Article", "Article", "en"),
        ("The model produced Article ???", "Article ???", "en"),
        ("لا تزال الإجابة تكتب: المادة ...", "المادة ...", "ar"),
    ],
)
def test_marks_trailing_incomplete_references_as_partial(
    text: str, expected_raw: str, expected_language: str
) -> None:
    citations = parse_citations(text)

    assert len(citations) == 1
    assert citations[0].article_number is None
    assert citations[0].is_partial is True
    assert citations[0].raw == expected_raw
    assert citations[0].language == expected_language


def test_keeps_a_malformed_reference_when_more_text_follows() -> None:
    citations = parse_citations("Article ??? applies.")

    assert len(citations) == 1
    assert citations[0].article_number is None
    assert citations[0].is_partial is True
    assert citations[0].raw == "Article ???"
    assert citations[0].language == "en"


@pytest.mark.parametrize("text", ["المادة ؟؟؟ تنطبق.", "وبالمادة ... نتمسك."])
def test_keeps_malformed_arabic_reference_when_more_text_follows(text: str) -> None:
    citation = parse_citations(text)[0]

    assert citation.article_number is None
    assert citation.language == "ar"
    assert citation.is_partial is True


def test_preserves_source_order_in_mixed_language_text() -> None:
    citations = parse_citations("المادة ٢٦١ من القانون الاتحادي رقم ٥، then Article 7.")

    assert [citation.article_number for citation in citations] == ["261", "7"]
    assert [citation.language for citation in citations] == ["ar", "en"]


def test_does_not_treat_an_ordinary_lowercase_word_as_a_partial_citation() -> None:
    assert parse_citations("This is a newspaper article") == []
