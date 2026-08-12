"""Cross-encoder re-ranking stage."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import numpy as np

from api.app.config import get_settings
from api.app.retrieval import RetrievalResult

_GTE_REPOSITORY = "onnx-community/gte-multilingual-reranker-base"
_GTE_MODEL_FILE = "onnx/model_int8.onnx"


@dataclass(slots=True)
class _OnnxCrossEncoder:
    tokenizer: object
    session: object

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        encodings = self.tokenizer.encode_batch(
            [(query, document) for document in documents]
        )
        available = {
            "input_ids": np.asarray(
                [encoding.ids for encoding in encodings], dtype=np.int64
            ),
            "attention_mask": np.asarray(
                [encoding.attention_mask for encoding in encodings], dtype=np.int64
            ),
            "token_type_ids": np.asarray(
                [encoding.type_ids for encoding in encodings], dtype=np.int64
            ),
        }
        inputs = {
            item.name: available[item.name] for item in self.session.get_inputs()
        }
        return np.asarray(self.session.run(None, inputs)[0]).reshape(-1).tolist()


@lru_cache(maxsize=2)
def _load_reranker(model_name: str, cache_dir: str):
    if model_name.startswith(f"{_GTE_REPOSITORY}@"):
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        revision = model_name.rsplit("@", 1)[1]
        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)
        local_model = cache_path / "reranker"
        if (local_model / "model_int8.onnx").exists():
            tokenizer_path = str(local_model / "tokenizer.json")
            model_path = str(local_model / "model_int8.onnx")
        else:
            tokenizer_path = hf_hub_download(
                _GTE_REPOSITORY,
                "tokenizer.json",
                revision=revision,
                cache_dir=cache_dir,
            )
            model_path = hf_hub_download(
                _GTE_REPOSITORY,
                _GTE_MODEL_FILE,
                revision=revision,
                cache_dir=cache_dir,
            )
        tokenizer = Tokenizer.from_file(tokenizer_path)
        tokenizer.enable_truncation(max_length=512)
        tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = min(2, os.cpu_count() or 1)
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            model_path,
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        return _OnnxCrossEncoder(tokenizer=tokenizer, session=session)

    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)


def prewarm_reranker() -> None:
    """Download and execute the configured cross-encoder during ingestion."""

    settings = get_settings()
    model = _load_reranker(settings.reranker_model, str(settings.model_cache_dir))
    scores = list(model.rerank("civil transaction", ["civil transaction law"]))
    if len(scores) != 1:
        raise RuntimeError("Cross-encoder prewarm returned an invalid result")


def rank(question: str, result: RetrievalResult, *, limit: int = 6) -> RetrievalResult:
    """Re-rank hybrid candidates with a cross-encoder."""

    if not result.articles:
        return result
    settings = get_settings()
    model = _load_reranker(settings.reranker_model, str(settings.model_cache_dir))
    documents = [article.text(result.query_language) for article in result.articles]
    raw_scores = list(model.rerank(question, documents))
    if len(raw_scores) != len(result.articles):
        raise RuntimeError("Cross-encoder returned an incomplete score set")
    values = np.asarray(raw_scores, dtype=np.float32)
    probabilities = 1.0 / (1.0 + np.exp(-values))
    reranked = [
        replace(article, rerank_score=float(probability))
        for article, probability in zip(result.articles, probabilities, strict=True)
    ]
    multilingual = settings.reranker_model.startswith(f"{_GTE_REPOSITORY}@")
    if multilingual:
        max_bm25 = max((article.bm25_score for article in reranked), default=0.0)

        def ranking_score(article) -> float:
            normalized_bm25 = article.bm25_score / max_bm25 if max_bm25 > 0 else 0.0
            return (
                0.84 * article.rerank_score
                + 0.14 * article.hybrid_score
                + 0.02 * normalized_bm25
            )

    else:
        rerank_weight = 0.35 if result.query_language == "ar" else 0.72
        hybrid_weight = 1.0 - rerank_weight

        def ranking_score(article) -> float:
            return (
                rerank_weight * article.rerank_score
                + hybrid_weight * article.hybrid_score
            )

    reranked.sort(key=ranking_score, reverse=True)
    top = tuple(reranked[:limit])
    confidence = min(
        1.0,
        (0.45 if multilingual else (0.75 if result.query_language == "ar" else 0.55))
        * result.confidence
        + (0.55 if multilingual else (0.25 if result.query_language == "ar" else 0.45))
        * (top[0].rerank_score if top else 0.0),
    )
    return RetrievalResult(
        articles=top,
        confidence=round(confidence, 4),
        query_language=result.query_language,
    )
