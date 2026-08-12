from __future__ import annotations

import asyncio
import json
import math
from dataclasses import replace

import pytest
from fastapi import HTTPException

from api.app import generation, ranking, streaming
from api.app.citations import parse_citations
from api.app.config import Settings
from api.app.generation import generate_grounded
from api.app.main import audit_stream
from api.app.retrieval import (
    RetrievalResult,
    RetrievedArticle,
    _bm25_scores,
    query_tokens,
    tokenize,
)
from api.app.schemas import AuditRequest
from api.app.streaming import _citation_is_stable, _emit_answer, _sse
from api.app.verification import LawIdentifier


def article(number: str, en: str, ar: str) -> RetrievedArticle:
    return RetrievedArticle(
        article_number=number,
        law_name="Federal Decree by Law No. 25 of 2025",
        law_number="25",
        law_year=2025,
        article_text_en=en,
        article_text_ar=ar,
        source_url=f"https://uaelegislation.gov.ae/en/legislations/4011#article-{number}",
        source_url_ar=f"https://uaelegislation.gov.ae/ar/legislations/4011#article-{number}",
        hybrid_score=0.8,
        rerank_score=0.9,
    )


def test_bm25_prefers_document_with_query_terms() -> None:
    scores = _bm25_scores(
        "contract compensation damage",
        [
            "A contract may require compensation for damage.",
            "The court appoints a guardian for a minor.",
        ],
    )
    assert scores[0] > scores[1]


def test_multilingual_ranker_uses_lexical_support_to_resolve_primary_arabic_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observed scores must keep the directly supported rule above a near semantic hit."""

    frozen_probabilities = [0.651272, 0.671042]

    class FrozenReranker:
        @staticmethod
        def rerank(query: str, documents: list[str]) -> list[float]:
            assert query == (
                "هل يحق لمالك عقار أن يطلب إزالة بناء جاره إذا حجب الضوء عن "
                "نوافذ منزله، وهل تغيّر الرخصة الإدارية هذا الحق؟"
            )
            assert len(documents) == 2
            return [math.log(value / (1.0 - value)) for value in frozen_probabilities]

    settings = Settings(
        reranker_model=(
            "onnx-community/gte-multilingual-reranker-base"
            "@ee64367e35a2db0da46bb6497e13a18f8bd585cb"
        )
    )
    monkeypatch.setattr(ranking, "get_settings", lambda: settings)
    monkeypatch.setattr(ranking, "_load_reranker", lambda *args: FrozenReranker())
    result = RetrievalResult(
        articles=(
            replace(
                article("1042", "Article 1042.", "المادة 1042."),
                bm25_score=25.4848,
                hybrid_score=0.992258,
            ),
            replace(
                article("1164", "Article 1164.", "المادة 1164."),
                bm25_score=7.4182,
                hybrid_score=0.945368,
            ),
        ),
        confidence=0.8,
        query_language="ar",
    )

    ranked = ranking.rank(
        "هل يحق لمالك عقار أن يطلب إزالة بناء جاره إذا حجب الضوء عن "
        "نوافذ منزله، وهل تغيّر الرخصة الإدارية هذا الحق؟",
        result,
        limit=2,
    )

    assert [item.article_number for item in ranked.articles] == ["1042", "1164"]


def test_fallback_ranker_does_not_use_multilingual_bm25_weight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_probabilities = [0.60, 0.61]

    class FrozenReranker:
        @staticmethod
        def rerank(query: str, documents: list[str]) -> list[float]:
            return [math.log(value / (1.0 - value)) for value in frozen_probabilities]

    settings = Settings(reranker_model="BAAI/bge-reranker-base")
    monkeypatch.setattr(ranking, "get_settings", lambda: settings)
    monkeypatch.setattr(ranking, "_load_reranker", lambda *args: FrozenReranker())
    result = RetrievalResult(
        articles=(
            replace(
                article("10", "Article 10.", "المادة 10."),
                bm25_score=100.0,
                hybrid_score=0.60,
            ),
            replace(
                article("11", "Article 11.", "المادة 11."),
                bm25_score=0.0,
                hybrid_score=0.61,
            ),
        ),
        confidence=0.8,
        query_language="en",
    )

    ranked = ranking.rank("Which article applies?", result, limit=2)

    assert [item.article_number for item in ranked.articles] == ["11", "10"]


def test_tokenizer_preserves_arabic_terms() -> None:
    assert tokenize("ما شروط التعويض عن الضرر؟") == ["ما", "شروط", "التعويض", "عن", "الضرر"]


def test_tokenizer_normalizes_arabic_diacritics_and_alef_forms() -> None:
    assert tokenize("إِزالة الأضرار إلى الأرض") == ["ازالة", "الاضرار", "الي", "الارض"]


def test_query_tokens_remove_generic_question_words() -> None:
    assert query_tokens("ما تعريف الشفعة، ومن هو صاحب الحق الأصلي فيها؟") == [
        "تعريف",
        "الشفعة",
        "صاحب",
        "الحق",
        "الاصلي",
    ]


@pytest.mark.asyncio
async def test_low_confidence_grounded_pipeline_refuses_as_correct_outcome() -> None:
    retrieval = RetrievalResult(articles=(), confidence=0.1, query_language="en")
    answer = await generate_grounded(
        "What is the legal rule?",
        "en",
        "cached",
        retrieval,
        seed_id=None,
        confidence_threshold=0.44,
    )
    assert answer.refused is True
    assert "refuses" in answer.text
    assert "inventing" in answer.text


@pytest.mark.asyncio
async def test_local_grounded_generation_cites_only_retrieved_articles() -> None:
    retrieval = RetrievalResult(
        articles=(
            article("17", "The rule in Article 17 applies.", "تطبق القاعدة."),
            article("44", "The rule in Article 44 applies.", "تطبق القاعدة الثانية."),
        ),
        confidence=0.91,
        query_language="en",
    )
    answer = await generate_grounded(
        "Which rules apply?",
        "en",
        "cached",
        retrieval,
        seed_id=None,
        confidence_threshold=0.44,
    )
    assert answer.provider == "local constrained synthesis"
    assert "Article 17" in answer.text
    assert "Article 44" not in answer.text
    assert "Article 45" not in answer.text


@pytest.mark.asyncio
async def test_cached_mode_never_bypasses_grounded_retrieval() -> None:
    retrieval = RetrievalResult(
        articles=(article("17", "The retrieved rule applies.", "تطبق القاعدة."),),
        confidence=0.91,
        query_language="en",
    )
    answer = await generate_grounded(
        "Which rule applies?",
        "en",
        "cached",
        retrieval,
        seed_id="seed-en-child-sale",
        confidence_threshold=0.44,
    )
    assert answer.cached is False
    assert answer.provider == "local constrained synthesis"
    assert [citation.article_number for citation in parse_citations(answer.text)] == ["17"]


@pytest.mark.asyncio
async def test_local_grounded_generation_does_not_leak_nested_citations() -> None:
    retrieval = RetrievalResult(
        articles=(
            article(
                "17",
                "This rule is subject to Article 45 and Section 52.",
                "تخضع هذه القاعدة إلى المادة ٤٥.",
            ),
        ),
        confidence=0.91,
        query_language="en",
    )
    answer = await generate_grounded(
        "Which rule applies?",
        "en",
        "cached",
        retrieval,
        seed_id=None,
        confidence_threshold=0.44,
    )
    citations = parse_citations(answer.text)
    assert [citation.article_number for citation in citations] == ["17"]


@pytest.mark.asyncio
async def test_grounded_generation_refuses_an_unretrieved_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retrieval = RetrievalResult(
        articles=(article("17", "The retrieved rule applies.", "تطبق القاعدة."),),
        confidence=0.91,
        query_language="en",
    )

    monkeypatch.setattr(
        generation,
        "_extractive_grounded_answer",
        lambda result, language: "Article 999 is not in the retrieved allow-list.",
    )

    answer = await generate_grounded(
        "Which rule applies?",
        "en",
        "live",
        retrieval,
        seed_id=None,
        confidence_threshold=0.44,
    )

    assert answer.refused is True
    assert answer.cached is False
    assert "refuses" in answer.text
    assert "inventing" in answer.text


@pytest.mark.asyncio
async def test_live_ungrounded_generation_forwards_provider_deltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed: list[str] = []

    async def provider_answer(
        system: str,
        user: str,
        *,
        on_delta,
    ) -> str:
        assert "ungrounded" in system
        assert user == "Which rule applies?"
        for delta in ("Article ", "1529", " applies."):
            await on_delta(delta)
        return "Article 1529 applies."

    settings = Settings(openai_api_key="test-key")
    monkeypatch.setattr(generation, "get_settings", lambda: settings)
    monkeypatch.setattr(generation, "_chat", provider_answer)

    async def collect(delta: str) -> None:
        streamed.append(delta)

    answer = await generation.generate_ungrounded(
        "Which rule applies?",
        "en",
        "live",
        seed_id=None,
        on_delta=collect,
    )

    assert streamed == ["Article ", "1529", " applies."]
    assert answer.text == "Article 1529 applies."
    assert answer.cached is False


@pytest.mark.asyncio
async def test_cached_http_request_rejects_a_seed_id_question_mismatch() -> None:
    request = AuditRequest(
        question="This question is not the committed Arabic seed.",
        language="ar",
        mode="cached",
        seed_id="seed-ar-neighbour-light",
    )

    with pytest.raises(HTTPException) as exc_info:
        await audit_stream(request)

    assert exc_info.value.status_code == 422


def test_sse_frame_is_unicode_json_and_has_event_boundary() -> None:
    frame = _sse("citation", {"text": "المادة ٢٦١"}).decode("utf-8")
    assert frame.startswith("event: citation\ndata: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame.split("data: ", 1)[1])
    assert payload["text"] == "المادة ٢٦١"


def test_stream_waits_for_law_scope_before_verdict() -> None:
    partial_text = "Article 2610 of "
    citation = parse_citations(partial_text)[0]
    assert _citation_is_stable(citation, partial_text) is False

    complete_text = "Article 2610 of Federal Decree by Law No. 25 of 2025."
    citation = parse_citations(complete_text)[0]
    assert _citation_is_stable(citation, complete_text) is True


@pytest.mark.parametrize(
    "partial_text",
    [
        "Article 1500 (",
        "Article 1500,",
        "Article 1500 —",
        "المادة ١٥٠٠ (",
        "المادة ١٥٠٠،",
    ],
)
def test_stream_holds_a_short_citation_before_possible_law_scope(
    partial_text: str,
) -> None:
    citation = parse_citations(partial_text)[0]

    assert _citation_is_stable(citation, partial_text) is False


@pytest.mark.parametrize(
    "partial_text",
    [
        "Article 1500 under",
        "Article 1500 pursuant to",
        "Article 1500 according to",
        "Article 1500 in",
        "Article 1500 from",
        "المادة ١٥٠٠ بموجب",
        "المادة ١٥٠٠ وفقًا",
        "المادة ١٥٠٠ في",
        "المادة ١٥٠٠ بحسب",
    ],
)
def test_stream_holds_scope_introducers_until_law_identity_is_complete(
    partial_text: str,
) -> None:
    citation = parse_citations(partial_text)[0]

    assert citation.law_number is None
    assert _citation_is_stable(citation, partial_text) is False


def test_stream_releases_citation_after_contextual_law_identity_is_complete() -> None:
    complete_text = "Article 1500 under Federal Law No. 5 of 1985 applies."
    citation = parse_citations(complete_text)[0]

    assert citation.law_number == "5"
    assert citation.law_year == 1985
    assert _citation_is_stable(citation, complete_text) is True


@pytest.mark.parametrize(
    "partial_text",
    [
        "Article 1500 u",
        "Article 1500 und",
        "Article 1500 purs",
        "Article 1500 acc",
        "المادة ١٥٠٠ بم",
        "المادة ١٥٠٠ وفق",
    ],
)
def test_stream_holds_fragmented_provider_scope_prefixes(partial_text: str) -> None:
    citation = parse_citations(partial_text)[0]

    assert _citation_is_stable(citation, partial_text) is False


def test_stream_releases_an_unscoped_citation_when_its_sentence_closes() -> None:
    complete_text = "Article 1529 creates the rule. A second sentence follows."
    citation = parse_citations(complete_text)[0]

    assert _citation_is_stable(citation, complete_text) is True


@pytest.mark.asyncio
async def test_final_stream_pass_emits_unverifiable_for_malformed_citation() -> None:
    queue: asyncio.Queue = asyncio.Queue()
    generated = generation.GenerationResult(
        text="Article ??? applies.",
        provider="test",
        cached=False,
    )

    scores = await _emit_answer(
        "ungrounded",
        generated,
        queue,
        parse_timeout=1,
        verify_timeout=1,
    )
    events = []
    while not queue.empty():
        events.append(await queue.get())

    citations = [payload for event, payload in events if event == "citation"]
    assert citations[0]["citation"]["verdict"] == "UNVERIFIABLE"
    assert scores["unverifiable"] == 1
    stage_events = [payload for event, payload in events if event == "stage"]
    assert {payload["stage"] for payload in stage_events} >= {"parse", "verify"}
    assert stage_events[-1] == {
        "side": "ungrounded",
        "stage": "verify",
        "status": "complete",
    }


@pytest.mark.asyncio
async def test_answer_audit_does_not_infer_a_law_for_free_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_contexts: list[LawIdentifier | None] = []

    async def fake_verify(
        side: str,
        citation,
        timeout: float,
        law_context: LawIdentifier | None = None,
    ) -> dict:
        received_contexts.append(law_context)
        return {
            "side": side,
            "citation": {
                "verdict": "UNVERIFIABLE",
                "article_number": citation.article_number,
            },
        }

    monkeypatch.setattr(streaming, "_verify_with_timeout", fake_verify)
    queue: asyncio.Queue = asyncio.Queue()
    generated = generation.GenerationResult(
        text="Article 1500 applies.",
        provider="test",
        cached=False,
    )

    scores = await _emit_answer(
        "ungrounded",
        generated,
        queue,
        parse_timeout=1,
        verify_timeout=1,
    )

    assert received_contexts == [None]
    assert scores["unverifiable"] == 1


@pytest.mark.asyncio
async def test_exact_cached_seed_can_supply_the_loaded_law_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_contexts: list[LawIdentifier | None] = []

    async def fake_verify(
        side: str,
        citation,
        timeout: float,
        law_context: LawIdentifier | None = None,
    ) -> dict:
        received_contexts.append(law_context)
        return {
            "side": side,
            "citation": {
                "verdict": "FABRICATED",
                "article_number": citation.article_number,
            },
        }

    monkeypatch.setattr(streaming, "_verify_with_timeout", fake_verify)
    queue: asyncio.Queue = asyncio.Queue()
    generated = generation.GenerationResult(
        text="Article 1529 applies.",
        provider="committed seed",
        cached=True,
    )
    loaded_law = LawIdentifier(law_number="25", law_year=2025)

    scores = await _emit_answer(
        "ungrounded",
        generated,
        queue,
        parse_timeout=1,
        verify_timeout=1,
        law_context=loaded_law,
    )

    assert received_contexts == [loaded_law]
    assert scores["fabricated"] == 1
