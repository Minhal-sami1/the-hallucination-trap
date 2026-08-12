"""Concurrent SSE orchestration for the two-answer citation courtroom."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from api.app.citations import CitationReference, parse_citations
from api.app.config import get_settings
from api.app.database import SessionLocal
from api.app.generation import GenerationResult, generate_grounded, generate_ungrounded
from api.app.ranking import rank
from api.app.repository import SQLAlchemyArticleRepository
from api.app.retrieval import RetrievalResult, retrieve
from api.app.schemas import AuditRequest
from api.app.seed_data import find_seed
from api.app.verification import LawIdentifier, verify_citation

CURRENT_LAW = LawIdentifier(law_number="25", law_year=2025)
_STREAM_UNITS = re.compile(r"\s+|[^\s]+", re.UNICODE)


class StageFailure(RuntimeError):
    """Carry the exact failed pipeline stage to the SSE error event."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass
class LiveAuditState:
    accumulated: str = ""
    seen: set[tuple[Any, ...]] = field(default_factory=set)
    counts: dict[str, int] = field(
        default_factory=lambda: {"verified": 0, "fabricated": 0, "unverifiable": 0}
    )


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _citation_key(citation: CitationReference) -> tuple[Any, ...]:
    return (
        citation.raw,
        citation.article_number,
        citation.law_number,
        citation.law_year,
        citation.language,
    )


def _citation_is_stable(citation: CitationReference, accumulated: str) -> bool:
    """Wait for citation scope words before issuing a live verdict.

    This prevents a temporary short form such as ``Article 9`` from being
    judged in the loaded-law context while ``of another law`` is still
    streaming.
    """

    start = accumulated.rfind(citation.raw)
    if start < 0:
        return False
    tail = accumulated[start + len(citation.raw) :]
    if not tail.strip():
        return False
    next_text = tail.lstrip().casefold()
    if citation.law_number is not None and citation.law_year is not None:
        return True
    # Provider deltas are not word-aligned. A short citation is therefore not
    # stable until its sentence closes. This prevents prefixes such as "u" or
    # "بم" from being judged before they become "under" or "بموجب" plus an
    # outside-law scope. Cached answers still land quickly because their first
    # citation sentence is short.
    if re.search(r"[.!?؟](?:\s|$)|\n", tail) is None:
        return False
    if next_text.startswith(("(", "[", "{", ",", ":", ";", "—", "–", "-", "،", "؛")):
        return False
    return not (
        next_text == "of"
        or next_text.startswith("of ")
        or next_text == "under"
        or next_text.startswith("under ")
        or next_text == "pursuant"
        or next_text.startswith("pursuant ")
        or next_text == "according"
        or next_text.startswith("according ")
        or next_text == "in"
        or next_text.startswith("in ")
        or next_text == "from"
        or next_text.startswith("from ")
        or next_text == "من"
        or next_text.startswith("من ")
        or next_text == "بموجب"
        or next_text.startswith("بموجب ")
        or next_text in {"وفقا", "وفقًا"}
        or next_text.startswith(("وفقا ", "وفقًا "))
        or next_text == "في"
        or next_text.startswith("في ")
        or next_text == "بحسب"
        or next_text.startswith("بحسب ")
    )


def _verdict_payload(
    side: str,
    citation: CitationReference,
    law_context: LawIdentifier | None = None,
) -> dict[str, Any]:
    with SessionLocal() as session:
        verdict = verify_citation(
            citation,
            SQLAlchemyArticleRepository(session),
            law_context=law_context,
        )
    return {
        "side": side,
        "citation": {
            "raw": citation.raw,
            "article_number": citation.article_number,
            "law_number": citation.law_number or (
                law_context.law_number if law_context is not None else None
            ),
            "law_year": citation.law_year or (
                law_context.law_year if law_context is not None else None
            ),
            "language": citation.language,
            "verdict": verdict.verdict.value,
            "reason": verdict.reason,
            "excerpt": verdict.excerpt,
            "source_url": verdict.source_url,
        },
    }


async def _parse_with_timeout(text: str, timeout: float) -> list[CitationReference]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(parse_citations, text),
            timeout=timeout,
        )
    except Exception as exc:
        raise StageFailure("parse", f"Citation parsing failed: {exc}") from exc


async def _verify_with_timeout(
    side: str,
    citation: CitationReference,
    timeout: float,
    law_context: LawIdentifier | None = None,
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_verdict_payload, side, citation, law_context),
            timeout=timeout,
        )
    except Exception as exc:
        raise StageFailure("verify", f"Citation verification failed: {exc}") from exc


async def _emit_live_delta(
    side: str,
    delta: str,
    state: LiveAuditState,
    queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    *,
    parse_timeout: float,
    verify_timeout: float,
    law_context: LawIdentifier | None = None,
) -> None:
    """Forward one real provider delta and audit stable citations immediately."""

    state.accumulated += delta
    await queue.put(("answer_token", {"side": side, "token": delta}))
    citations = await _parse_with_timeout(state.accumulated, parse_timeout)
    for citation in citations:
        key = _citation_key(citation)
        if citation.is_partial or key in state.seen:
            continue
        if not _citation_is_stable(citation, state.accumulated):
            continue
        state.seen.add(key)
        payload = await _verify_with_timeout(
            side,
            citation,
            verify_timeout,
            law_context,
        )
        verdict_name = str(payload["citation"]["verdict"]).casefold()
        state.counts[verdict_name] += 1
        await queue.put(("citation", payload))


async def _emit_answer(
    side: str,
    generated: GenerationResult,
    queue: asyncio.Queue[tuple[str, dict[str, Any]]],
    *,
    parse_timeout: float,
    verify_timeout: float,
    live_state: LiveAuditState | None = None,
    law_context: LawIdentifier | None = None,
) -> dict[str, Any]:
    await queue.put(
        (
            "stage",
            {
                "side": side,
                "stage": "generate",
                "status": "complete",
                "provider": generated.provider,
                "cached": generated.cached,
                "refused": generated.refused,
            },
        )
    )
    if generated.refused:
        await queue.put(("refusal", {"side": side, "message": generated.text}))
    state = live_state or LiveAuditState()
    if live_state is None:
        for unit in _STREAM_UNITS.findall(generated.text):
            state.accumulated += unit
            await queue.put(("answer_token", {"side": side, "token": unit}))
            await asyncio.sleep(0.018 if generated.cached else 0.006)
            citations = await _parse_with_timeout(state.accumulated, parse_timeout)
            for citation in citations:
                key = _citation_key(citation)
                if (
                    citation.is_partial
                    or key in state.seen
                    or not _citation_is_stable(citation, state.accumulated)
                ):
                    continue
                state.seen.add(key)
                payload = await _verify_with_timeout(
                    side,
                    citation,
                    verify_timeout,
                    law_context,
                )
                verdict_name = str(payload["citation"]["verdict"]).casefold()
                state.counts[verdict_name] += 1
                await queue.put(("citation", payload))

    # A final parse catches references that ended at the last character.
    await queue.put(("stage", {"side": side, "stage": "parse", "status": "running"}))
    citations = await _parse_with_timeout(state.accumulated, parse_timeout)
    await queue.put(("stage", {"side": side, "stage": "parse", "status": "complete"}))
    await queue.put(("stage", {"side": side, "stage": "verify", "status": "running"}))
    for citation in citations:
        key = _citation_key(citation)
        if key in state.seen:
            continue
        state.seen.add(key)
        payload = await _verify_with_timeout(
            side,
            citation,
            verify_timeout,
            law_context,
        )
        verdict_name = str(payload["citation"]["verdict"]).casefold()
        state.counts[verdict_name] += 1
        await queue.put(("citation", payload))

    total = sum(state.counts.values())
    score = round(state.counts["verified"] / total, 4) if total else 1.0
    await queue.put(("stage", {"side": side, "stage": "verify", "status": "complete"}))
    return {**state.counts, "total": total, "defensibility_score": score}


async def _run_ungrounded(
    request: AuditRequest,
    seed_id: str | None,
    queue: asyncio.Queue[tuple[str, dict[str, Any]]],
) -> None:
    settings = get_settings()
    side = "ungrounded"
    scores: dict[str, Any] | None = None
    try:
        await queue.put(("stage", {"side": side, "stage": "generate", "status": "running"}))
        live_state = LiveAuditState() if request.mode == "live" else None
        # A short-form citation can inherit the loaded law only when the answer
        # comes from an exact committed seed. Free-text and live provider output
        # must identify the law itself before the verifier can issue a red
        # verdict. This keeps real provisions from an outside law out of the
        # FABRICATED class.
        trusted_law_context = (
            CURRENT_LAW if request.mode == "cached" and seed_id is not None else None
        )

        async def on_delta(delta: str) -> None:
            if live_state is None:
                return
            await _emit_live_delta(
                side,
                delta,
                live_state,
                queue,
                parse_timeout=settings.parse_timeout_seconds,
                verify_timeout=settings.verify_timeout_seconds,
                law_context=None,
            )

        generated = await asyncio.wait_for(
            generate_ungrounded(
                request.question,
                request.language,
                request.mode,
                seed_id=seed_id,
                on_delta=on_delta if live_state is not None else None,
            ),
            timeout=settings.generate_timeout_seconds,
        )
        scores = await _emit_answer(
            side,
            generated,
            queue,
            parse_timeout=settings.parse_timeout_seconds,
            verify_timeout=settings.verify_timeout_seconds,
            live_state=live_state,
            law_context=trusted_law_context,
        )
    except StageFailure as exc:
        await queue.put(
            (
                "error",
                {"side": side, "stage": exc.stage, "message": str(exc), "recoverable": True},
            )
        )
    except Exception as exc:
        await queue.put(
            (
                "error",
                {"side": side, "stage": "generate", "message": str(exc), "recoverable": True},
            )
        )
    finally:
        await queue.put(("complete", {"side": side, "scores": scores}))


def _retrieve_sync(question: str, language: str) -> RetrievalResult:
    with SessionLocal() as session:
        return retrieve(question, language, session)


async def _run_grounded(
    request: AuditRequest,
    seed_id: str | None,
    queue: asyncio.Queue[tuple[str, dict[str, Any]]],
) -> None:
    settings = get_settings()
    side = "grounded"
    scores: dict[str, Any] | None = None
    try:
        await queue.put(("stage", {"side": side, "stage": "retrieve", "status": "running"}))
        try:
            retrieval = await asyncio.wait_for(
                asyncio.to_thread(_retrieve_sync, request.question, request.language),
                timeout=settings.retrieve_timeout_seconds,
            )
        except Exception as exc:
            raise StageFailure("retrieve", f"Retrieval failed: {exc}") from exc
        await queue.put(
            (
                "stage",
                {
                    "side": side,
                    "stage": "retrieve",
                    "status": "complete",
                    "candidates": len(retrieval.articles),
                    "confidence": retrieval.confidence,
                },
            )
        )
        await queue.put(("stage", {"side": side, "stage": "rank", "status": "running"}))
        try:
            ranked = await asyncio.wait_for(
                asyncio.to_thread(rank, request.question, retrieval),
                timeout=settings.rank_timeout_seconds,
            )
            await queue.put(
                (
                    "stage",
                    {
                        "side": side,
                        "stage": "rank",
                        "status": "complete",
                        "articles": [item.article_number for item in ranked.articles],
                        "confidence": ranked.confidence,
                    },
                )
            )
        except Exception as exc:
            ranked = retrieval
            await queue.put(
                (
                    "stage",
                    {
                        "side": side,
                        "stage": "rank",
                        "status": "degraded",
                        "message": str(exc),
                        "fallback": "hybrid retrieval order",
                        "articles": [item.article_number for item in ranked.articles],
                        "confidence": ranked.confidence,
                    },
                )
            )
        await queue.put(("stage", {"side": side, "stage": "generate", "status": "running"}))
        try:
            generated = await asyncio.wait_for(
                generate_grounded(
                    request.question,
                    request.language,
                    request.mode,
                    ranked,
                    seed_id=seed_id,
                    confidence_threshold=settings.retrieval_confidence_threshold,
                ),
                timeout=settings.generate_timeout_seconds,
            )
        except Exception as exc:
            raise StageFailure("generate", f"Grounded generation failed: {exc}") from exc
        scores = await _emit_answer(
            side,
            generated,
            queue,
            parse_timeout=settings.parse_timeout_seconds,
            verify_timeout=settings.verify_timeout_seconds,
        )
    except StageFailure as exc:
        await queue.put(
            (
                "error",
                {"side": side, "stage": exc.stage, "message": str(exc), "recoverable": True},
            )
        )
    except Exception as exc:
        await queue.put(
            (
                "error",
                {
                    "side": side,
                    "stage": "generate",
                    "message": str(exc),
                    "recoverable": True,
                },
            )
        )
    finally:
        await queue.put(("complete", {"side": side, "scores": scores}))


async def audit_event_stream(request: AuditRequest) -> AsyncIterator[bytes]:
    """Run both answer paths concurrently and yield browser-safe SSE frames."""

    seed = find_seed(seed_id=request.seed_id, question=request.question)
    seed_id = str(seed["id"]) if seed else None
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    await queue.put(
        (
            "meta",
            {
                "mode": request.mode,
                "cached": request.mode == "cached",
                "seed_id": seed_id,
                "law": {
                    "name": "Federal Decree by Law No. 25 of 2025 Civil Transactions Law",
                    "law_number": "25",
                    "law_year": 2025,
                    "articles": 1422,
                },
                "pipeline": ["retrieve", "rank", "generate", "parse", "verify"],
            },
        )
    )
    tasks = [
        asyncio.create_task(_run_ungrounded(request, seed_id, queue)),
        asyncio.create_task(_run_grounded(request, seed_id, queue)),
    ]
    complete_count = 0
    while complete_count < 2:
        event, data = await queue.get()
        if event == "complete":
            complete_count += 1
        yield _sse(event, data)
    await asyncio.gather(*tasks, return_exceptions=True)
    yield _sse("done", {"status": "complete"})
