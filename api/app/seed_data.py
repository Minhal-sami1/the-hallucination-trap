"""Load committed seed questions and model-produced cached answers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = ROOT / "data" / "seed_questions.json"
CACHE_PATH = ROOT / "data" / "cached_answers.json"


class SeedDataError(RuntimeError):
    """Raised when reproducibility artifacts are absent or malformed."""


def _read_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SeedDataError(f"Required reproducibility artifact is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SeedDataError(f"Expected a JSON object in {path}")
    return payload


@lru_cache(maxsize=1)
def load_seed_data() -> dict[str, Any]:
    payload = _read_object(SEED_PATH)
    questions = payload.get("questions")
    if not isinstance(questions, list) or len(questions) < 8:
        raise SeedDataError("Seed data must contain at least eight evaluated questions")
    required = {"id", "language", "question", "fabrication_rate"}
    for question in questions:
        if not isinstance(question, dict) or not required.issubset(question):
            raise SeedDataError("Each seed needs id, language, question, and fabrication_rate")
    return payload


@lru_cache(maxsize=1)
def load_answer_cache() -> dict[str, Any]:
    payload = _read_object(CACHE_PATH)
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise SeedDataError("Cached answers must contain an answers object")
    return payload


def find_seed(*, seed_id: str | None, question: str) -> dict[str, Any] | None:
    normalized = " ".join(question.casefold().split())
    if seed_id:
        for seed in load_seed_data()["questions"]:
            if seed["id"] != seed_id:
                continue
            seed_question = " ".join(str(seed["question"]).casefold().split())
            return seed if seed_question == normalized else None
        return None
    for seed in load_seed_data()["questions"]:
        if " ".join(str(seed["question"]).casefold().split()) == normalized:
            return seed
    return None


def cached_answer(seed_id: str, side: str) -> dict[str, Any]:
    cache = load_answer_cache()
    seed_answers = cache["answers"].get(seed_id)
    if not isinstance(seed_answers, dict) or not isinstance(seed_answers.get(side), dict):
        raise SeedDataError(f"No {side} cached answer is recorded for seed {seed_id}")
    answer = seed_answers[side]
    if not str(answer.get("text", "")).strip():
        raise SeedDataError(f"Cached {side} answer for seed {seed_id} has no text")
    return answer
