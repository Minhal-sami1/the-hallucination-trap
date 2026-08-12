"""Constrained and ungrounded generation stages."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from api.app.citations import CitationReference, parse_citations
from api.app.config import Settings, get_settings
from api.app.retrieval import RetrievalResult, RetrievedArticle
from api.app.seed_data import cached_answer
from api.app.verification import law_name_matches


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    provider: str
    cached: bool
    refused: bool = False


def _client(settings: Settings):
    if settings.openai_api_key:
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=settings.openai_api_key)
    if settings.azure_openai_endpoint and settings.azure_openai_key:
        from openai import AsyncAzureOpenAI

        return AsyncAzureOpenAI(
            api_key=settings.azure_openai_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version="2025-04-01-preview",
        )
    return None


async def _chat(
    system: str,
    user: str,
    *,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    settings = get_settings()
    client = _client(settings)
    if client is None:
        raise RuntimeError("Live generation is unavailable because no model provider is configured")
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.75 if "ungrounded" in system.casefold() else 0.0,
        stream=True,
    )
    parts: list[str] = []
    async for chunk in response:
        text_delta = chunk.choices[0].delta.content if chunk.choices else None
        if not text_delta:
            continue
        parts.append(text_delta)
        if on_delta is not None:
            await on_delta(text_delta)
    text = "".join(parts)
    if not text:
        raise RuntimeError("The live model returned an empty answer")
    return text.strip()


async def generate_ungrounded(
    question: str,
    language: str,
    mode: str,
    *,
    seed_id: str | None,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
) -> GenerationResult:
    """Generate without retrieval. Cached mode only replays recorded model output."""

    if mode == "cached":
        if seed_id is None:
            raise RuntimeError(
                "Cached mode has no recorded run for this free-text question. "
                "Select a seed or switch to Live."
            )
        item = cached_answer(seed_id, "ungrounded")
        return GenerationResult(
            text=str(item["text"]),
            provider=str(item.get("provider", "recorded model run")),
            cached=True,
        )

    system = (
        "You are an ungrounded generic legal language model. Answer confidently and "
        "cite specific law articles by number. Do not retrieve sources and do not say "
        "that you lack access. The interface will independently audit every citation. "
        f"Write in {'Arabic' if language == 'ar' else 'English'}."
    )
    text = await _chat(system, question, on_delta=on_delta)
    return GenerationResult(
        text=text,
        provider=get_settings().openai_model,
        cached=False,
    )


_EN_EMBEDDED_CITATION = re.compile(
    r"\b(?:Article|Art\.?|Section|s\.)\s*\(?\d+\)?",
    re.IGNORECASE,
)
_AR_EMBEDDED_CITATION = re.compile(r"المادة\s*\(?[٠-٩۰-۹\d]+\)?")


def _clean_evidence_text(text: str, language: str) -> str:
    pattern = _AR_EMBEDDED_CITATION if language == "ar" else _EN_EMBEDDED_CITATION
    replacement = "النص المشار إليه" if language == "ar" else "the cross-referenced provision"
    return pattern.sub(replacement, text).strip()


def _extractive_grounded_answer(result: RetrievalResult, language: str) -> str:
    # The local fallback cites only the highest-ranked provision. This keeps
    # citation precision high and avoids implying support from weaker matches.
    chosen = result.articles[:1]
    if language == "ar":
        lead = "تستند الإجابة إلى النصوص المسترجعة والموثقة التالية فقط:"
        body = "\n\n".join(
            f"المادة {article.article_number} من مرسوم بقانون اتحادي رقم 25 لسنة 2025: "
            f"{_clean_evidence_text(article.article_text_ar[:420], language)}"
            for article in chosen
        )
        return f"{lead}\n\n{body}"
    lead = "This answer is limited to the following retrieved and verified provisions:"
    body = "\n\n".join(
        f"Article {article.article_number} of Federal Decree by Law No. 25 of 2025: "
        f"{_clean_evidence_text(article.article_text_en[:420], language)}"
        for article in chosen
    )
    return f"{lead}\n\n{body}"


def _grounded_refusal(language: str, provider: str) -> GenerationResult:
    refusal = (
        "لا توجد أدلة كافية في مجموعة القوانين المحملة لإصدار إجابة قابلة للدفاع. "
        "رفض النظام الإجابة بدلاً من اختراع مرجع."
        if language == "ar"
        else "The loaded legal corpus does not contain enough evidence for a defensible "
        "answer. The pipeline refuses instead of inventing a citation."
    )
    return GenerationResult(
        text=refusal,
        provider=provider,
        cached=False,
        refused=True,
    )


def _citation_matches_article(
    citation: CitationReference, article: RetrievedArticle
) -> bool:
    if citation.article_number != article.article_number:
        return False
    if citation.law_number is not None and citation.law_number != article.law_number:
        return False
    if citation.law_year is not None and citation.law_year != article.law_year:
        return False
    return citation.law_name is None or law_name_matches(
        citation.law_name, article.law_name
    )


def _uses_only_retrieved_citations(text: str, retrieval: RetrievalResult) -> bool:
    citations = parse_citations(text)
    if not citations or any(citation.article_number is None for citation in citations):
        return False
    return all(
        any(
            _citation_matches_article(citation, article)
            for article in retrieval.articles
        )
        for citation in citations
    )


async def generate_grounded(
    question: str,
    language: str,
    mode: str,
    retrieval: RetrievalResult,
    *,
    seed_id: str | None,
    confidence_threshold: float,
) -> GenerationResult:
    """Generate only from re-ranked articles, or refuse when evidence is weak."""

    if retrieval.confidence < confidence_threshold or not retrieval.articles:
        return _grounded_refusal(language, "retrieval confidence gate")

    text = _extractive_grounded_answer(retrieval, language)
    if not _uses_only_retrieved_citations(text, retrieval):
        return _grounded_refusal(language, "output citation allow-list")
    return GenerationResult(
        text=text,
        provider="local constrained synthesis",
        cached=False,
    )
