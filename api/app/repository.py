"""SQLAlchemy access to the article corpus and exact citation index."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.app.models import Article
from api.app.verification import ArticleRecord, LawRecord, law_name_matches


class SQLAlchemyArticleRepository:
    """Implement exact legal lookups without coupling the verifier to SQLAlchemy."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_laws(
        self,
        *,
        law_number: str | None = None,
        law_year: int | None = None,
        law_name: str | None = None,
    ) -> Sequence[LawRecord]:
        statement = select(
            Article.law_name,
            Article.law_name_ar,
            Article.law_number,
            Article.law_year,
            func.count(Article.id),
        ).group_by(
            Article.law_name,
            Article.law_name_ar,
            Article.law_number,
            Article.law_year,
        )
        if law_number is not None:
            statement = statement.where(Article.law_number == law_number)
        if law_year is not None:
            statement = statement.where(Article.law_year == law_year)
        laws = tuple(
            LawRecord(
                law_name=name,
                law_name_ar=name_ar,
                law_number=number,
                law_year=year,
                article_count=int(count),
            )
            for name, name_ar, number, year, count in self._session.execute(statement).all()
        )
        if law_name is None:
            return laws
        return tuple(
            law
            for law in laws
            if law_name_matches(law_name, law.law_name, law.law_name_ar)
        )

    def get_article(
        self, *, law_number: str, law_year: int, article_number: str
    ) -> ArticleRecord | None:
        row = self._session.scalar(
            select(Article).where(
                Article.law_number == law_number,
                Article.law_year == law_year,
                Article.article_number == article_number,
            )
        )
        if row is None:
            return None
        return ArticleRecord(
            law_name=row.law_name,
            law_number=row.law_number,
            law_year=row.law_year,
            article_number=row.article_number,
            article_text_en=row.article_text_en,
            article_text_ar=row.article_text_ar,
            source_url=row.source_url,
            source_url_ar=row.source_url_ar,
        )
