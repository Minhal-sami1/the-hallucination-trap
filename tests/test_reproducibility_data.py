from __future__ import annotations

import json

from api.app.citations import parse_citations
from api.app.seed_data import find_seed
from scripts.corpus_io import EXPECTED_ARTICLE_COUNT, ROOT, load_corpus


def load_json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_seed_runs_are_complete_distinct_and_above_the_retention_threshold() -> None:
    payload = load_json("results/seed_runs.json")
    seeds = payload["seeds"]
    runs = [run for seed in seeds for run in seed["runs"]]

    assert len(seeds) == 8
    assert len(runs) == 80
    assert len({run["answer_text"] for run in runs}) == 80
    assert all(seed["total_runs"] == 10 for seed in seeds)
    assert all(seed["fabrication_rate"] >= 0.8 for seed in seeds)
    assert sum(run["has_fabricated_article"] for run in runs) == 80


def test_primary_cached_answer_starts_with_a_real_parser_detectable_fabrication() -> None:
    seeds = load_json("data/seed_questions.json")["questions"]
    cache = load_json("data/cached_answers.json")["answers"]
    primary = next(seed for seed in seeds if seed["is_primary"])
    text = cache[primary["id"]]["ungrounded"]["text"]
    citations = parse_citations(text)

    assert primary["language"] == "ar"
    assert citations[0].raw.startswith("المادة")
    assert int(citations[0].article_number or 0) > EXPECTED_ARTICLE_COUNT
    assert text.startswith(citations[0].raw)


def test_seed_id_cannot_replay_cache_for_a_different_question() -> None:
    assert (
        find_seed(
            seed_id="seed-ar-neighbour-light",
            question="This is not the recorded Arabic seed question.",
        )
        is None
    )


def test_seed_id_and_its_exact_question_resolve_together() -> None:
    seeds = load_json("data/seed_questions.json")["questions"]
    primary = next(seed for seed in seeds if seed["id"] == "seed-ar-neighbour-light")

    assert find_seed(seed_id=primary["id"], question=primary["question"]) == primary


def test_only_ungrounded_answers_are_cached() -> None:
    cache = load_json("data/cached_answers.json")["answers"]

    for answers in cache.values():
        assert set(answers) == {"ungrounded"}
        assert answers["ungrounded"]["provider"] == "gpt-5.6 Codex subagent"
        assert answers["ungrounded"]["run_id"]


def test_fifty_hand_checked_questions_are_bilingual_and_reference_real_articles() -> None:
    payload = load_json("data/eval_questions.json")
    questions = payload["questions"]
    articles = {article.article_number: article for article in load_corpus()}

    assert len(questions) == 50
    assert sum(question["language"] == "ar" for question in questions) >= 10
    assert len({question["id"] for question in questions}) == 50
    for question in questions:
        assert question["question"].strip()
        assert question["evidence_note"].strip()
        assert question["expected_articles"]
        for article_number in question["expected_articles"]:
            article = articles[article_number]
            assert article.article_text_en
            assert article.article_text_ar


def test_verifier_real_controls_include_current_and_archived_scope_forms() -> None:
    payload = load_json("data/verifier_real_controls.json")
    controls = payload["controls"]

    assert len(controls) == 33
    assert sum(item["expected_verdict"] == "VERIFIED" for item in controls) == 15
    assert sum(item["expected_verdict"] == "UNVERIFIABLE" for item in controls) == 18
    assert all(item["article_number"] in {"261", "1500", "1528"} for item in controls)
