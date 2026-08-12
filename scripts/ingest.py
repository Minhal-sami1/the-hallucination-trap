from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from api.app.database import SessionLocal
from api.app.embeddings import embed_documents
from api.app.models import Article
from api.app.ranking import prewarm_reranker
from scripts.corpus_io import (
    EXPECTED_ARTICLE_COUNT,
    combined_source_hash,
    load_corpus,
    load_manifest,
)

LAW_NAME_EN = "Federal Decree by Law No. 25 of 2025 Promulgating the Civil Transactions Law"
LAW_NAME_AR = "مرسوم بقانون اتحادي رقم (25) لسنة 2025 بإصدار قانون المعاملات المدنية"


def build_embedding_text(article_text_en: str, article_text_ar: str) -> str:
    return f"English:\n{article_text_en}\n\nالعربية:\n{article_text_ar}"


def main() -> int:
    try:
        manifest = load_manifest()
        corpus = load_corpus()
    except Exception as exc:
        print(f"Corpus input failed validation: {exc}", file=sys.stderr)
        return 1

    if len(corpus) != EXPECTED_ARTICLE_COUNT:
        print(
            f"Expected {EXPECTED_ARTICLE_COUNT} articles, found {len(corpus)}",
            file=sys.stderr,
        )
        return 1

    print(f"Embedding {len(corpus)} bilingual articles. The first run downloads the model.")
    embedding_inputs = [
        build_embedding_text(article.article_text_en, article.article_text_ar)
        for article in corpus
    ]
    embeddings = embed_documents(embedding_inputs)
    if len(embeddings) != len(corpus):
        print("Embedding model returned an incomplete result set", file=sys.stderr)
        return 1

    print("Prewarming the cross-encoder reranker.")
    prewarm_reranker()

    retrieved_at_raw = manifest.get("retrieved_at", "2026-08-12T00:00:00+00:00")
    retrieved_at = datetime.fromisoformat(str(retrieved_at_raw).replace("Z", "+00:00"))
    if retrieved_at.tzinfo is None:
        retrieved_at = retrieved_at.replace(tzinfo=UTC)
    source_hash = combined_source_hash()

    rows = [
        {
            "law_name": LAW_NAME_EN,
            "law_name_ar": LAW_NAME_AR,
            "law_number": "25",
            "law_year": 2025,
            "article_number": str(article.article_number),
            "article_number_int": article.article_number,
            "article_text_en": article.article_text_en,
            "article_text_ar": article.article_text_ar,
            "source_url": article.source_url,
            "source_url_ar": article.source_url_ar,
            "retrieved_at": retrieved_at,
            "source_document_sha256": source_hash,
            "embedding": embedding,
        }
        for article, embedding in zip(corpus, embeddings, strict=True)
    ]

    with SessionLocal() as session:
        for start in range(0, len(rows), 100):
            batch = rows[start : start + 100]
            statement = insert(Article).values(batch)
            statement = statement.on_conflict_do_update(
                constraint="uq_article_legal_citation",
                set_={
                    key: getattr(statement.excluded, key)
                    for key in batch[0]
                    if key not in {"law_number", "law_year", "article_number"}
                },
            )
            session.execute(statement)

        session.execute(
            text(
                """
                UPDATE articles
                SET search_vector_en = to_tsvector('english', coalesce(article_text_en, '')),
                    search_vector_ar = to_tsvector(
                        'simple',
                        translate(
                            regexp_replace(
                                coalesce(article_text_ar, ''),
                                '[ً-ٰٟ]',
                                '',
                                'g'
                            ),
                            'أإآٱىؤئـ',
                            'اااايوي'
                        )
                    )
                WHERE law_number = '25' AND law_year = 2025
                """
            )
        )
        session.commit()

        total = session.scalar(select(func.count()).select_from(Article))
        missing_arabic = session.scalar(
            select(func.count())
            .select_from(Article)
            .where(func.length(Article.article_text_ar) == 0)
        )
        missing_embeddings = session.scalar(
            select(func.count()).select_from(Article).where(Article.embedding.is_(None))
        )

    summary = {
        "law": "Federal Decree by Law No. 25 of 2025",
        "articles_ingested": len(rows),
        "database_total": total,
        "missing_arabic": missing_arabic,
        "missing_embeddings": missing_embeddings,
        "source_hash": source_hash,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    passed = total == EXPECTED_ARTICLE_COUNT and not missing_arabic and not missing_embeddings
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
