"""Create the verified legal article index."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "20260812_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "articles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("law_name", sa.String(length=300), nullable=False),
        sa.Column("law_name_ar", sa.String(length=300), nullable=False),
        sa.Column("law_number", sa.String(length=30), nullable=False),
        sa.Column("law_year", sa.Integer(), nullable=False),
        sa.Column("article_number", sa.String(length=30), nullable=False),
        sa.Column("article_number_int", sa.Integer(), nullable=True),
        sa.Column("article_text_en", sa.Text(), nullable=False),
        sa.Column("article_text_ar", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_url_ar", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_document_sha256", sa.String(length=64), nullable=False),
        sa.Column("search_vector_en", postgresql.TSVECTOR(), nullable=True),
        sa.Column("search_vector_ar", postgresql.TSVECTOR(), nullable=True),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "law_number",
            "law_year",
            "article_number",
            name="uq_article_legal_citation",
        ),
    )
    op.create_index(
        "ix_articles_search_vector_en",
        "articles",
        ["search_vector_en"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_articles_search_vector_ar",
        "articles",
        ["search_vector_ar"],
        postgresql_using="gin",
    )
    op.execute(
        "CREATE INDEX ix_articles_embedding_hnsw ON articles "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.drop_table("articles")
