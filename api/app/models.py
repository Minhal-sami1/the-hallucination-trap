from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from api.app.database import Base

EMBEDDING_DIMENSIONS = 384


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint(
            "law_number",
            "law_year",
            "article_number",
            name="uq_article_legal_citation",
        ),
        Index("ix_articles_search_vector_en", "search_vector_en", postgresql_using="gin"),
        Index("ix_articles_search_vector_ar", "search_vector_ar", postgresql_using="gin"),
        Index(
            "ix_articles_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    law_name: Mapped[str] = mapped_column(String(300), nullable=False)
    law_name_ar: Mapped[str] = mapped_column(String(300), nullable=False)
    law_number: Mapped[str] = mapped_column(String(30), nullable=False)
    law_year: Mapped[int] = mapped_column(Integer, nullable=False)
    article_number: Mapped[str] = mapped_column(String(30), nullable=False)
    article_number_int: Mapped[int | None] = mapped_column(Integer)
    article_text_en: Mapped[str] = mapped_column(Text, nullable=False)
    article_text_ar: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_url_ar: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    search_vector_en: Mapped[str | None] = mapped_column(TSVECTOR)
    search_vector_ar: Mapped[str | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
