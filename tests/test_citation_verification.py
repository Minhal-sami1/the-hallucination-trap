import pytest

from api.app.citations import parse_citations
from api.app.verification import (
    ArticleRecord,
    InMemoryArticleRepository,
    LawIdentifier,
    LawRecord,
    Verdict,
    verify_citation,
)

SOURCE_URL = (
    "https://uaelegislation.gov.ae/en/legislations/4011?keyword=Article%20%28261%29"
)
SOURCE_URL_AR = "https://uaelegislation.gov.ae/ar/legislations/4011"
LAW = LawRecord(
    law_name="Federal Decree by Law No. 25 of 2025",
    law_name_ar="مرسوم بقانون اتحادي رقم (25) لسنة 2025",
    law_number="25",
    law_year=2025,
    article_count=1422,
)
ARTICLE_261 = ArticleRecord(
    law_name=LAW.law_name,
    law_number=LAW.law_number,
    law_year=LAW.law_year,
    article_number="261",
    article_text_en="Liability attaches to the possession of property taken until it is returned.",
    article_text_ar=None,
    source_url=SOURCE_URL,
    source_url_ar=SOURCE_URL_AR,
)


def test_a_known_real_citation_is_verified_with_official_proof() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("Article 261 of Federal Decree by Law No. 25 of 2025")[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.VERIFIED
    assert verdict.article == ARTICLE_261
    assert verdict.excerpt == ARTICLE_261.article_text_en
    assert verdict.source_url == SOURCE_URL
    assert verdict.reason == "Exact article match in the loaded corpus."


def test_a_real_citation_with_leading_zeroes_is_verified() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations(
        "Article 0261 of Federal Decree by Law No. 025 of 2025"
    )[0]

    verdict = verify_citation(citation, repository)

    assert citation.article_number == "261"
    assert citation.law_number == "25"
    assert verdict.verdict is Verdict.VERIFIED


def test_a_different_enactment_type_with_same_number_and_year_is_unverifiable() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("Article 261 of Federal Law No. 25 of 2025")[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == (
        "Federal Law No. 25 of 2025 is outside the loaded corpus."
    )


def test_the_known_real_citation_is_verified_in_arabic_form() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("المادة ٢٦١ من مرسوم بقانون اتحادي رقم (٢٥) لسنة ٢٠٢٥")[
        0
    ]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.VERIFIED
    assert verdict.article == ARTICLE_261
    assert verdict.source_url == SOURCE_URL_AR
    assert verdict.reason == (
        "تطابق المرجع تماماً مع المادة في مجموعة القوانين المحمّلة."
    )


def test_a_known_fake_article_in_a_loaded_law_is_fabricated() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("Article 2610 of Federal Decree by Law No. 25 of 2025")[
        0
    ]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.FABRICATED
    assert verdict.article is None
    assert verdict.excerpt is None
    assert verdict.source_url is None
    assert verdict.reason == (
        "Federal Decree by Law No. 25 of 2025 contains 1,422 articles; "
        "Article 2610 does not exist."
    )


def test_a_known_fake_arabic_citation_explains_the_fabrication_in_arabic() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations(
        "المادة ٢٦١٠ من مرسوم بقانون اتحادي رقم (٢٥) لسنة ٢٠٢٥"
    )[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.FABRICATED
    assert verdict.reason == (
        "يحتوي مرسوم بقانون اتحادي رقم (25) لسنة 2025 على 1,422 مادة؛ "
        "المادة 2610 غير موجودة."
    )


def test_a_citation_outside_the_loaded_corpus_is_unverifiable() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("Article 7 of Federal Law No. 99 of 2030")[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == (
        "Federal Law No. 99 of 2030 is outside the loaded corpus."
    )


def test_an_arabic_citation_outside_the_corpus_explains_the_scope_in_arabic() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations(
        "المادة ٧ من القانون الاتحادي رقم ٩٩ لسنة ٢٠٣٠"
    )[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == (
        "القانون الاتحادي رقم 99 لسنة 2030 خارج مجموعة القوانين المحمّلة."
    )


def test_an_ambiguous_arabic_law_scope_has_an_arabic_reason() -> None:
    older_law = LawRecord(
        law_name="Federal Decree by Law No. 25 of 2024",
        law_name_ar="مرسوم بقانون اتحادي رقم (25) لسنة 2024",
        law_number="25",
        law_year=2024,
        article_count=900,
    )
    repository = InMemoryArticleRepository(
        laws=[LAW, older_law],
        articles=[ARTICLE_261],
    )
    citation = parse_citations("المادة ٢٦١ من مرسوم بقانون اتحادي رقم ٢٥")[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == (
        "تعذر ربط المرجع بقانون واحد في مجموعة القوانين المحمّلة."
    )


def test_a_comma_scoped_outside_law_cannot_fall_back_to_the_current_law() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("Article 261, Federal Law No. 99 of 2030")[0]

    verdict = verify_citation(
        citation,
        repository,
        law_context=LawIdentifier(law_number="25", law_year=2025),
    )

    assert citation.law_number == "99"
    assert citation.law_year == 2030
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == (
        "Federal Law No. 99 of 2030 is outside the loaded corpus."
    )


def test_a_real_archived_article_with_unparsed_title_scope_is_not_called_fake() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations(
        "Article 1500 of the Civil Transactions Law (Federal Law No. 5 of 1985)"
    )[0]

    verdict = verify_citation(
        citation,
        repository,
        law_context=LawIdentifier(law_number="25", law_year=2025),
    )

    assert citation.law_number == "5"
    assert citation.law_year == 1985
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "outside the loaded corpus" in verdict.reason


@pytest.mark.parametrize(
    "text",
    [
        "Under the former Civil Transactions Law, Article 1500 states the rule.",
        "Federal Law No. 5 of 1985, Article 1500 states the rule.",
        "Pursuant to Federal Law No. 5 of 1985, Art. 1500 states the rule.",
        "Article 1500 (Federal Law No. 5 of 1985)",
        "Article 1500 — Federal Law No. 5 of 1985",
        "بموجب القانون الاتحادي رقم 5 لسنة 1985، تنص المادة 1500 على القاعدة.",
        "المادة ١٥٠٠ (القانون الاتحادي رقم ٥ لسنة ١٩٨٥)",
        "المادة ١٥٠٠ وفقًا للقانون الاتحادي رقم ٥ لسنة ١٩٨٥",
        "المادة ١٥٠٠ بموجب القانون الاتحادي رقم ٥ لسنة ١٩٨٥",
    ],
)
def test_archived_real_scope_variants_never_receive_a_red_verdict(text: str) -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations(text)[0]

    verdict = verify_citation(
        citation,
        repository,
        law_context=LawIdentifier(law_number="25", law_year=2025),
    )

    assert verdict.verdict is Verdict.UNVERIFIABLE


@pytest.mark.parametrize(
    "text",
    [
        "Article (0261) of Federal Decree by Law No. (025) of 2025",
        "Article 261 of the Civil Transactions Law "
        "(Federal Decree by Law No. 25 of 2025)",
        "المادة ٢٦١ من قانون المعاملات المدنية الصادر "
        "بمرسوم بقانون اتحادي رقم ٢٥ لسنة ٢٠٢٥",
    ],
)
def test_current_real_titled_scope_variants_are_verified(text: str) -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations(text)[0]

    verdict = verify_citation(
        citation,
        repository,
        law_context=LawIdentifier(law_number="25", law_year=2025),
    )

    assert citation.law_number == "25"
    assert citation.law_year == 2025
    assert verdict.verdict is Verdict.VERIFIED


@pytest.mark.parametrize(
    "text",
    [
        "Federal Decree by Law No. 25 of 2025 governs Article 261. "
        "Under the former Civil Transactions Law, Article 1500 applied.",
        "Article 261 of Federal Decree by Law No. 25 of 2025 replaced "
        "Article 1500 under the former Civil Transactions Law.",
        "مرسوم بقانون اتحادي رقم ٢٥ لسنة ٢٠٢٥ يحكم المادة ٢٦١. "
        "أما قانون المعاملات المدنية السابق فكانت فيه المادة ١٥٠٠.",
    ],
)
def test_mixed_current_and_archived_scopes_never_make_archived_real_red(
    text: str,
) -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citations = parse_citations(text)
    verdicts = [
        verify_citation(
            citation,
            repository,
            law_context=LawIdentifier(law_number="25", law_year=2025),
        )
        for citation in citations
    ]
    by_article = {
        verdict.citation.article_number: verdict.verdict for verdict in verdicts
    }

    assert by_article["261"] is Verdict.VERIFIED
    assert by_article["1500"] is Verdict.UNVERIFIABLE


def test_two_coordinated_articles_share_one_explicit_archived_law_scope() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citations = parse_citations(
        "Federal Law No. 5 of 1985 provides in Article 1500 and Article 1528."
    )
    verdicts = [
        verify_citation(
            citation,
            repository,
            law_context=LawIdentifier(law_number="25", law_year=2025),
        )
        for citation in citations
    ]

    assert [citation.law_number for citation in citations] == ["5", "5"]
    assert [citation.law_year for citation in citations] == [1985, 1985]
    assert all(verdict.verdict is Verdict.UNVERIFIABLE for verdict in verdicts)


@pytest.mark.parametrize(
    "text, expected_numbers",
    [
        (
            "Article 1500 and Article 1528 of Federal Law No. 5 of 1985",
            ["1500", "1528"],
        ),
        (
            "Article 1500, Article 1528, and Article 1529 of Federal Law No. 5 of 1985",
            ["1500", "1528", "1529"],
        ),
        (
            "المادة ١٥٠٠ والمادة ١٥٢٨ من القانون الاتحادي رقم ٥ لسنة ١٩٨٥",
            ["1500", "1528"],
        ),
        (
            "القانون الاتحادي رقم ٥ لسنة ١٩٨٥ ينص في المادة ١٥٠٠ والمادة ١٥٢٨.",
            ["1500", "1528"],
        ),
    ],
)
def test_coordinated_archived_articles_inherit_scope_in_both_directions(
    text: str,
    expected_numbers: list[str],
) -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citations = parse_citations(text)
    verdicts = [
        verify_citation(
            citation,
            repository,
            law_context=LawIdentifier(law_number="25", law_year=2025),
        )
        for citation in citations
    ]

    assert [citation.article_number for citation in citations] == expected_numbers
    assert all(citation.law_number == "5" for citation in citations)
    assert all(citation.law_year == 1985 for citation in citations)
    assert all(verdict.verdict is Verdict.UNVERIFIABLE for verdict in verdicts)


def test_an_outside_decree_law_keeps_its_cited_law_type_in_the_reason() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("Article 7 of Federal Decree-Law No. 99 of 2030")[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == (
        "Federal Decree-Law No. 99 of 2030 is outside the loaded corpus."
    )


def test_an_incomplete_stream_fragment_is_not_called_fabricated() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("Article ??? applies.")[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == ("Citation is incomplete; no article number is available.")


def test_an_incomplete_arabic_fragment_has_an_arabic_reason() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("المادة ؟؟؟ تنطبق.")[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == "المرجع غير مكتمل؛ لا يتضمن رقم المادة."


def test_an_arabic_citation_with_unresolved_law_scope_has_an_arabic_reason() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations(
        "بموجب قانون المعاملات المدنية السابق، تنص المادة ١٥٠٠ على القاعدة."
    )[0]

    verdict = verify_citation(citation, repository)

    assert citation.is_partial is True
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == "المرجع غير مكتمل؛ تعذر تحديد نطاق القانون."


def test_an_unscoped_short_citation_is_unverifiable() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("The answer relies on Art. 261.")[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == (
        "Citation does not identify a law; verification needs a law context."
    )


def test_an_unscoped_arabic_citation_has_an_arabic_reason() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("تعتمد الإجابة على المادة ٢٦١.")[0]

    verdict = verify_citation(citation, repository)

    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert verdict.reason == (
        "لا يحدد المرجع قانوناً؛ يلزم تحديد سياق القانون للتحقق منه."
    )


def test_a_short_citation_can_use_an_explicit_law_context() -> None:
    repository = InMemoryArticleRepository(laws=[LAW], articles=[ARTICLE_261])
    citation = parse_citations("The answer relies on s.261.")[0]

    verdict = verify_citation(
        citation,
        repository,
        law_context=LawIdentifier(law_number="25", law_year=2025),
    )

    assert verdict.verdict is Verdict.VERIFIED
    assert verdict.article == ARTICLE_261
