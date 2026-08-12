"""Hybrid retrieval: PostgreSQL full text plus BM25 and pgvector."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.app.embeddings import embed_query
from api.app.models import Article

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "does",
    "for",
    "how",
    "in",
    "is",
    "it",
    "of",
    "or",
    "the",
    "to",
    "what",
    "when",
    "who",
    "with",
    "ام",
    "الى",
    "ان",
    "او",
    "اي",
    "في",
    "فيها",
    "عن",
    "علي",
    "ما",
    "ماذا",
    "متي",
    "من",
    "هو",
    "هل",
    "هي",
    "ومن",
}


@dataclass(frozen=True, slots=True)
class RetrievedArticle:
    article_number: str
    law_name: str
    law_number: str
    law_year: int
    article_text_en: str
    article_text_ar: str
    source_url: str
    source_url_ar: str
    bm25_score: float = 0.0
    vector_score: float = 0.0
    hybrid_score: float = 0.0
    rerank_score: float = 0.0

    def text(self, language: str) -> str:
        return self.article_text_ar if language == "ar" else self.article_text_en


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    articles: tuple[RetrievedArticle, ...]
    confidence: float
    query_language: str


def _normalize_token(token: str) -> str:
    value = "".join(
        character
        for character in unicodedata.normalize("NFKC", token.casefold())
        if not unicodedata.combining(character)
    )
    return value.translate(
        str.maketrans(
            {
                "أ": "ا",
                "إ": "ا",
                "آ": "ا",
                "ٱ": "ا",
                "ى": "ي",
                "ؤ": "و",
                "ئ": "ي",
                "ـ": "",
            }
        )
    )


def tokenize(value: str) -> list[str]:
    # Remove combining marks before token boundaries are found. Otherwise an
    # Arabic diacritic can split one word into two tokens (for example
    # ``إِزالة`` became ``ا`` + ``زالة``).
    normalized_value = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    return [_normalize_token(token) for token in _TOKEN_RE.findall(normalized_value)]


def query_tokens(value: str) -> list[str]:
    return [token for token in tokenize(value) if len(token) > 1 and token not in _QUERY_STOPWORDS]


def _bm25_scores(query: str, documents: list[str]) -> list[float]:
    """Calculate BM25 for a PostgreSQL tsvector-selected candidate set."""

    query_terms = list(dict.fromkeys(query_tokens(query)))
    if not query_terms or not documents:
        return [0.0] * len(documents)
    tokenized = [tokenize(document) for document in documents]
    average_length = sum(map(len, tokenized)) / max(len(tokenized), 1)
    document_frequency = {
        term: sum(1 for tokens in tokenized if term in set(tokens)) for term in query_terms
    }
    k1, b = 1.5, 0.75
    scores: list[float] = []
    for tokens in tokenized:
        counts = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = counts[term]
            if not frequency:
                continue
            containing = document_frequency[term]
            inverse_frequency = math.log(
                1 + (len(tokenized) - containing + 0.5) / (containing + 0.5)
            )
            denominator = frequency + k1 * (
                1 - b + b * len(tokens) / max(average_length, 1)
            )
            score += inverse_frequency * frequency * (k1 + 1) / denominator
        scores.append(score)
    return scores


def _article_from_row(row: Article) -> RetrievedArticle:
    return RetrievedArticle(
        article_number=row.article_number,
        law_name=row.law_name,
        law_number=row.law_number,
        law_year=row.law_year,
        article_text_en=row.article_text_en,
        article_text_ar=row.article_text_ar,
        source_url=row.source_url,
        source_url_ar=row.source_url_ar,
    )


def retrieve(
    question: str,
    language: str,
    session: Session,
    *,
    limit: int = 12,
    candidate_limit: int = 60,
) -> RetrievalResult:
    """Return ranked hybrid candidates and a normalized top-result confidence."""

    language = "ar" if language == "ar" else "en"
    vector_column = Article.search_vector_ar if language == "ar" else Article.search_vector_en
    text_config = "simple" if language == "ar" else "english"
    query_terms = list(dict.fromkeys(query_tokens(question)))[:24]
    if query_terms:
        query_function = func.to_tsquery(text_config, " | ".join(query_terms))
    else:
        query_function = func.plainto_tsquery(text_config, question)

    keyword_rows = list(
        session.scalars(
            select(Article)
            .where(vector_column.op("@@")(query_function))
            .order_by(func.ts_rank_cd(vector_column, query_function).desc())
            .limit(candidate_limit)
        )
    )
    keyword_articles = [_article_from_row(row) for row in keyword_rows]
    bm25 = _bm25_scores(question, [item.text(language) for item in keyword_articles])
    keyword_articles = [
        replace(item, bm25_score=score)
        for item, score in zip(keyword_articles, bm25, strict=True)
    ]
    keyword_articles.sort(key=lambda item: item.bm25_score, reverse=True)

    query_embedding = embed_query(question)
    distance = Article.embedding.cosine_distance(query_embedding)
    vector_rows = session.execute(
        select(Article, distance.label("distance"))
        .where(Article.embedding.is_not(None))
        .order_by(distance)
        .limit(candidate_limit)
    ).all()
    vector_articles = [
        replace(_article_from_row(row), vector_score=max(0.0, 1.0 - float(row_distance)))
        for row, row_distance in vector_rows
    ]

    combined: dict[tuple[str, int, str], RetrievedArticle] = {}
    rank_scores: dict[tuple[str, int, str], float] = {}
    for rank, article in enumerate(keyword_articles, start=1):
        key = (article.law_number, article.law_year, article.article_number)
        combined[key] = article
        rank_scores[key] = rank_scores.get(key, 0.0) + 0.52 / (60 + rank)
    for rank, article in enumerate(vector_articles, start=1):
        key = (article.law_number, article.law_year, article.article_number)
        existing = combined.get(key)
        combined[key] = (
            replace(existing, vector_score=article.vector_score) if existing else article
        )
        rank_scores[key] = rank_scores.get(key, 0.0) + 0.48 / (60 + rank)

    if not combined:
        return RetrievalResult(articles=(), confidence=0.0, query_language=language)

    max_rrf = 0.52 / 61 + 0.48 / 61
    ranked = [
        replace(article, hybrid_score=min(1.0, rank_scores[key] / max_rrf))
        for key, article in combined.items()
    ]
    ranked.sort(key=lambda item: item.hybrid_score, reverse=True)
    selected = tuple(ranked[:limit])
    lexical_support = min(1.0, (selected[0].bm25_score / 5.0)) if selected else 0.0
    vector_support = selected[0].vector_score if selected else 0.0
    confidence = min(
        1.0,
        0.42 * selected[0].hybrid_score + 0.28 * lexical_support + 0.30 * vector_support,
    )
    return RetrievalResult(
        articles=selected,
        confidence=round(confidence, 4),
        query_language=language,
    )
