"""Run the reproducible 50-question citation evaluation."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from api.app.citations import parse_citations
from api.app.config import get_settings
from api.app.database import SessionLocal
from api.app.generation import _extractive_grounded_answer
from api.app.ranking import rank
from api.app.repository import SQLAlchemyArticleRepository
from api.app.retrieval import retrieve
from api.app.verification import LawIdentifier, Verdict, verify_citation

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = ROOT / "data" / "eval_questions.json"
SEED_RUNS_PATH = ROOT / "results" / "seed_runs.json"
VERIFIER_CONTROLS_PATH = ROOT / "data" / "verifier_real_controls.json"
RESULT_PATH = ROOT / "results" / "eval.json"
LAW_CONTEXT = LawIdentifier(law_number="25", law_year=2025)


def _load_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Required evaluation input is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object in {path}")
    return value


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _fabrication_summary(
    payload: dict[str, Any], repository: SQLAlchemyArticleRepository
) -> dict[str, Any]:
    """Re-run the product parser and verifier over every ungrounded answer.

    The primary detected rate must not trust the committed boolean label. The
    label is retained only as a ground-truth comparison so parser misses and
    stale metadata are visible.
    """

    total = 0
    declared_fabricated = 0
    detected_fabricated = 0
    parsed_citations = 0
    verifier_fabricated = 0
    verifier_verified = 0
    verifier_unverifiable = 0
    label_mismatches: list[dict[str, Any]] = []
    seeds = payload.get("seeds") or payload.get("questions") or payload.get("runs")
    if isinstance(seeds, dict):
        seed_values = seeds.values()
    elif isinstance(seeds, list):
        seed_values = seeds
    else:
        raise RuntimeError("seed_runs.json has no seed run collection")
    for seed in seed_values:
        if not isinstance(seed, dict):
            continue
        runs = seed.get("runs", [])
        for run in runs:
            if not isinstance(run, dict):
                continue
            total += 1
            declared = run.get("has_fabricated_article") is True
            declared_fabricated += int(declared)
            answer_text = str(run.get("answer_text", ""))
            citations = [
                citation
                for citation in parse_citations(answer_text)
                if citation.article_number is not None
            ]
            parsed_citations += len(citations)
            verdicts = [
                verify_citation(citation, repository, law_context=LAW_CONTEXT)
                for citation in citations
            ]
            verifier_fabricated += sum(
                verdict.verdict == Verdict.FABRICATED for verdict in verdicts
            )
            verifier_verified += sum(
                verdict.verdict == Verdict.VERIFIED for verdict in verdicts
            )
            verifier_unverifiable += sum(
                verdict.verdict == Verdict.UNVERIFIABLE for verdict in verdicts
            )
            detected = any(
                verdict.verdict == Verdict.FABRICATED for verdict in verdicts
            )
            detected_fabricated += int(detected)
            if declared != detected:
                label_mismatches.append(
                    {
                        "run_id": run.get("run_id"),
                        "declared_has_fabricated_article": declared,
                        "runtime_detected_fabricated_article": detected,
                        "parsed_citations": [
                            citation.raw for citation in citations
                        ],
                    }
                )
    if total != 80:
        raise RuntimeError(f"Expected 80 evaluated ungrounded runs, found {total}")
    return {
        "total_runs": total,
        "declared_runs_with_fabrication": declared_fabricated,
        "runtime_detected_runs_with_fabrication": detected_fabricated,
        "parsed_citations": parsed_citations,
        "verifier_fabricated_citations": verifier_fabricated,
        "verifier_verified_citations": verifier_verified,
        "verifier_unverifiable_citations": verifier_unverifiable,
        "label_mismatches": label_mismatches,
    }


def main() -> int:
    try:
        questions_payload = _load_object(QUESTIONS_PATH)
        seed_runs = _load_object(SEED_RUNS_PATH)
        verifier_controls_payload = _load_object(VERIFIER_CONTROLS_PATH)
    except Exception as exc:
        print(f"Evaluation input failed validation: {exc}", file=sys.stderr)
        return 1

    questions = questions_payload.get("questions")
    if not isinstance(questions, list) or len(questions) != 50:
        print("Evaluation set must contain exactly 50 questions", file=sys.stderr)
        return 1

    true_positive = 0
    predicted_total = 0
    expected_total = 0
    exact_match_questions = 0
    answered_questions = 0
    retrieval_top_1_hits = 0
    retrieval_top_k_hits = 0
    verifier_real_total = 0
    verifier_false_positive = 0
    verifier_real_unverifiable = 0
    grounded_verifier_fabricated = 0
    grounded_verifier_unverifiable = 0
    records: list[dict[str, Any]] = []
    settings = get_settings()
    verifier_control_records: list[dict[str, str]] = []

    with SessionLocal() as session:
        repository = SQLAlchemyArticleRepository(session)
        for item in questions:
            expected = {str(number) for number in item["expected_articles"]}
            language = "ar" if item.get("language") == "ar" else "en"
            retrieval = retrieve(str(item["question"]), language, session)
            ranked = rank(str(item["question"]), retrieval)
            ranked_numbers = [article.article_number for article in ranked.articles]
            if ranked_numbers and ranked_numbers[0] in expected:
                retrieval_top_1_hits += 1
            retrieval_top_k_hits += len(set(ranked_numbers) & expected)

            refused = (
                ranked.confidence < settings.retrieval_confidence_threshold
                or not ranked.articles
            )
            answer = "" if refused else _extractive_grounded_answer(ranked, language)
            answered_questions += int(not refused)
            citations = parse_citations(answer)
            predicted = {
                citation.article_number for citation in citations if citation.article_number
            }
            matched = predicted & expected
            true_positive += len(matched)
            predicted_total += len(predicted)
            expected_total += len(expected)
            exact_match_questions += int(predicted == expected)

            answer_verdicts: list[dict[str, str]] = []
            for citation in citations:
                if citation.article_number is None:
                    continue
                verdict = verify_citation(
                    citation, repository, law_context=LAW_CONTEXT
                )
                grounded_verifier_fabricated += int(
                    verdict.verdict == Verdict.FABRICATED
                )
                grounded_verifier_unverifiable += int(
                    verdict.verdict == Verdict.UNVERIFIABLE
                )
                answer_verdicts.append(
                    {
                        "raw": citation.raw,
                        "article_number": citation.article_number,
                        "verdict": verdict.verdict.value,
                    }
                )

            expected_verdicts: list[dict[str, str]] = []
            for number in sorted(expected, key=int):
                reference_text = (
                    f"المادة {number}" if language == "ar" else f"Article {number}"
                )
                reference = parse_citations(reference_text)[0]
                verdict = verify_citation(reference, repository, law_context=LAW_CONTEXT)
                verifier_real_total += 1
                if verdict.verdict == Verdict.FABRICATED:
                    verifier_false_positive += 1
                if verdict.verdict == Verdict.UNVERIFIABLE:
                    verifier_real_unverifiable += 1
                expected_verdicts.append(
                    {"article_number": number, "verdict": verdict.verdict.value}
                )

            records.append(
                {
                    "id": item["id"],
                    "language": language,
                    "question": item["question"],
                    "expected_articles": sorted(expected, key=int),
                    "ranked_articles": ranked_numbers,
                    "predicted_articles": sorted(predicted, key=int),
                    "matched_articles": sorted(matched, key=int),
                    "retrieval_confidence": ranked.confidence,
                    "confidence_threshold": settings.retrieval_confidence_threshold,
                    "refused": refused,
                    "answer_citation_verdicts": answer_verdicts,
                    "known_real_citation_checks": expected_verdicts,
                }
            )

        controls = verifier_controls_payload.get("controls")
        if not isinstance(controls, list) or len(controls) < 20:
            print("Verifier controls must contain at least 20 real citations", file=sys.stderr)
            return 1
        for control in controls:
            citations = parse_citations(str(control["text"]))
            target = next(
                (
                    citation
                    for citation in citations
                    if citation.article_number == str(control["article_number"])
                ),
                None,
            )
            if target is None:
                print(f"Verifier control did not parse: {control['id']}", file=sys.stderr)
                return 1
            verdict = verify_citation(target, repository, law_context=LAW_CONTEXT)
            verifier_real_total += 1
            verifier_false_positive += int(verdict.verdict == Verdict.FABRICATED)
            verifier_real_unverifiable += int(verdict.verdict == Verdict.UNVERIFIABLE)
            expected_verdict = str(control["expected_verdict"])
            verifier_control_records.append(
                {
                    "id": str(control["id"]),
                    "article_number": str(control["article_number"]),
                    "expected_verdict": expected_verdict,
                    "actual_verdict": verdict.verdict.value,
                }
            )
            if verdict.verdict.value != expected_verdict:
                print(
                    f"Verifier control {control['id']} expected {expected_verdict}, "
                    f"received {verdict.verdict.value}",
                    file=sys.stderr,
                )
                return 1
        try:
            ungrounded = _fabrication_summary(seed_runs, repository)
        except Exception as exc:
            print(f"Ungrounded evaluation failed validation: {exc}", file=sys.stderr)
            return 1

    total_runs = int(ungrounded["total_runs"])
    detected_fabricated_runs = int(
        ungrounded["runtime_detected_runs_with_fabrication"]
    )
    declared_fabricated_runs = int(ungrounded["declared_runs_with_fabrication"])

    metrics = {
        "citation_precision": _ratio(true_positive, predicted_total),
        "citation_recall": _ratio(true_positive, expected_total),
        "citation_exact_match_rate": _ratio(exact_match_questions, len(questions)),
        "answer_coverage": _ratio(answered_questions, len(questions)),
        "retrieval_top_1_hit_rate": _ratio(retrieval_top_1_hits, len(questions)),
        "retrieval_expected_article_recall_at_6": _ratio(
            retrieval_top_k_hits, expected_total
        ),
        "fabrication_rate_ungrounded": _ratio(
            detected_fabricated_runs, total_runs
        ),
        "declared_fabrication_rate_ungrounded": _ratio(
            declared_fabricated_runs, total_runs
        ),
        "end_to_end_detection_recall_on_declared_fabricated_runs": _ratio(
            detected_fabricated_runs, declared_fabricated_runs
        ),
        "verifier_false_positive_rate": _ratio(
            verifier_false_positive, verifier_real_total
        ),
        "counts": {
            "questions": len(questions),
            "corpus_articles": int(questions_payload.get("law", {}).get("article_count", 0)),
            "grounded_correct_citations": true_positive,
            "grounded_predicted_citations": predicted_total,
            "grounded_expected_citations": expected_total,
            "grounded_exact_match_questions": exact_match_questions,
            "grounded_answered_questions": answered_questions,
            "retrieval_top_1_hits": retrieval_top_1_hits,
            "retrieval_expected_article_hits_at_6": retrieval_top_k_hits,
            "grounded_answer_citations_marked_fabricated": (
                grounded_verifier_fabricated
            ),
            "grounded_answer_citations_marked_unverifiable": (
                grounded_verifier_unverifiable
            ),
            "ungrounded_runs": total_runs,
            "ungrounded_runs_with_runtime_detected_fabrication": (
                detected_fabricated_runs
            ),
            "ungrounded_runs_declared_with_fabrication": declared_fabricated_runs,
            "ungrounded_label_mismatches": len(ungrounded["label_mismatches"]),
            "real_citations_checked_by_verifier": verifier_real_total,
            "real_citations_wrongly_marked_fabricated": verifier_false_positive,
            "real_citations_marked_unverifiable": verifier_real_unverifiable,
        },
    }
    seed_expected_articles = {
        str(number)
        for seed in seed_runs.get("seeds", [])
        for number in seed.get("expected_articles", [])
    }
    evaluation_expected_articles = {
        str(number)
        for question in questions
        for number in question.get("expected_articles", [])
    }
    overlapping_expected_articles = sorted(
        seed_expected_articles & evaluation_expected_articles, key=int
    )
    evaluation_questions_touching_seed_articles = sum(
        bool(
            {
                str(number)
                for number in question.get("expected_articles", [])
            }
            & seed_expected_articles
        )
        for question in questions
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "command": "make eval",
        "corpus": questions_payload.get("law", {}),
        "corpus_article_count": int(
            questions_payload.get("law", {}).get("article_count", 0)
        ),
        "models": {
            "ungrounded_seed_producer": seed_runs.get("model_provenance"),
            "embedding": settings.embedding_model,
            "reranker": settings.reranker_model,
            "grounded_generator": "local constrained top-1 extractive synthesis",
        },
        "pipeline": ["retrieve", "rank", "generate", "parse", "verify"],
        "inputs": {
            "evaluation_questions_sha256": _file_sha256(QUESTIONS_PATH),
            "seed_runs_sha256": _file_sha256(SEED_RUNS_PATH),
            "verifier_real_controls_sha256": _file_sha256(VERIFIER_CONTROLS_PATH),
        },
        "metric_definitions": {
            "citation_precision": (
                "Micro exact-match precision over unique article numbers parsed from "
                "non-refused grounded answers against the hand-checked expected sets."
            ),
            "citation_recall": (
                "Micro exact-match recall over all hand-checked expected article numbers; "
                "refused answers contribute zero predicted citations."
            ),
            "fabrication_rate_ungrounded": (
                "Share of the 80 answer texts for which the runtime parser and exact-law "
                "verifier detect at least one nonexistent article. This does not trust the "
                "committed has_fabricated_article boolean."
            ),
            "verifier_false_positive_rate": (
                "Known real current and archived citation forms marked FABRICATED divided "
                "by all known real checks. Archived-law controls must be UNVERIFIABLE, not "
                "FABRICATED. UNVERIFIABLE is reported separately."
            ),
        },
        "evaluation_scope": {
            "grounded_path": (
                "Current local top-1 extractive fallback with the production confidence gate."
            ),
            "cached_grounded_answers_used": False,
            "questions_are_corpus_derived": True,
            "expected_articles_are_not_passed_to_retrieval_or_ranking": True,
            "semantic_claim_support_is_evaluated": False,
            "seed_and_evaluation_sets_are_article_independent": False,
            "seed_expected_articles": sorted(seed_expected_articles, key=int),
            "overlapping_seed_and_evaluation_articles": overlapping_expected_articles,
            "evaluation_questions_touching_seed_articles": (
                evaluation_questions_touching_seed_articles
            ),
            "verifier_verified_means": (
                "The cited article number exists in the identified law. It does not prove "
                "that the article semantically supports the answer."
            ),
        },
        "seed_provenance": {
            "producer_label": seed_runs.get("model_provenance"),
            "generation_mode_claim": seed_runs.get("generation_mode"),
            "exact_system_instruction": seed_runs.get("exact_system_instruction"),
            "provider_response_ids_present": all(
                bool(run.get("provider_response_id"))
                for seed in seed_runs.get("seeds", [])
                for run in seed.get("runs", [])
            ),
            "sampling_parameters_present": all(
                isinstance(run.get("sampling_parameters"), dict)
                for seed in seed_runs.get("seeds", [])
                for run in seed.get("runs", [])
            ),
            "selection_conditioned_on_fabrication": bool(
                seed_runs.get("verification", {}).get("retention_threshold")
            ),
            "interpretation": (
                "The declared fabrication rate describes a retained challenge set. "
                "It is not an unbiased estimate of the named model's general legal "
                "citation fabrication rate. The artifact has no provider response IDs "
                "or recorded sampling parameters, so independent replay is not possible."
            ),
        },
        "metrics": metrics,
        "ungrounded_runtime_audit": ungrounded,
        "verifier_real_controls": verifier_control_records,
        "questions": records,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"Wrote {RESULT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
