from __future__ import annotations

import json
import sys

from sqlalchemy import func, select

from api.app.database import SessionLocal
from api.app.models import Article
from scripts.corpus_io import (
    EXPECTED_ARTICLE_COUNT,
    ROOT,
    load_corpus,
    load_manifest,
    sha256_file,
)


def main() -> int:
    failures: list[str] = []

    try:
        source_articles = load_corpus()
        manifest = load_manifest()
    except Exception as exc:
        print(f"Source validation failed: {exc}", file=sys.stderr)
        return 1

    if len(source_articles) != EXPECTED_ARTICLE_COUNT:
        failures.append(
            f"Committed source has {len(source_articles)} articles, "
            f"expected {EXPECTED_ARTICLE_COUNT}"
        )

    with SessionLocal() as session:
        total = int(session.scalar(select(func.count()).select_from(Article)) or 0)
        per_law_rows = session.execute(
            select(Article.law_number, Article.law_year, func.count())
            .group_by(Article.law_number, Article.law_year)
            .order_by(Article.law_year, Article.law_number)
        ).all()
        missing_arabic = int(
            session.scalar(
                select(func.count())
                .select_from(Article)
                .where(
                    (Article.article_text_ar.is_(None))
                    | (func.length(func.trim(Article.article_text_ar)) == 0)
                )
            )
            or 0
        )
        missing_english = int(
            session.scalar(
                select(func.count())
                .select_from(Article)
                .where(
                    (Article.article_text_en.is_(None))
                    | (func.length(func.trim(Article.article_text_en)) == 0)
                )
            )
            or 0
        )
        missing_embeddings = int(
            session.scalar(
                select(func.count()).select_from(Article).where(Article.embedding.is_(None))
            )
            or 0
        )
        duplicate_rows = session.execute(
            select(
                Article.law_number,
                Article.law_year,
                Article.article_number,
                func.count().label("row_count"),
            )
            .group_by(Article.law_number, Article.law_year, Article.article_number)
            .having(func.count() > 1)
        ).all()

    if total != EXPECTED_ARTICLE_COUNT:
        failures.append(f"Database has {total} articles, expected {EXPECTED_ARTICLE_COUNT}")
    if missing_arabic:
        failures.append(f"{missing_arabic} articles have no Arabic text")
    if missing_english:
        failures.append(f"{missing_english} articles have no English text")
    if missing_embeddings:
        failures.append(f"{missing_embeddings} articles have no embedding")
    if duplicate_rows:
        failures.append(f"{len(duplicate_rows)} duplicate citation keys exist")

    spot_checks = manifest.get("spot_checks", [])
    passed_spot_checks = sum(1 for check in spot_checks if check.get("status") == "passed")
    if passed_spot_checks < 20:
        failures.append(f"Only {passed_spot_checks} of 20 required source spot checks passed")
    spot_numbers = [check.get("article_number") for check in spot_checks]
    if len(set(spot_numbers)) != len(spot_numbers):
        failures.append("Source spot-check article numbers are not unique")

    spot_record_path = ROOT / str(
        manifest.get("validation", {}).get("spot_check_record", "")
    )
    detailed_spot_checks: list[dict[str, object]] = []
    if not spot_record_path.is_file():
        failures.append("The detailed source spot-check record is missing")
    else:
        try:
            spot_record = json.loads(spot_record_path.read_text(encoding="utf-8"))
            detailed_spot_checks = spot_record.get("checks", [])
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            failures.append(f"The detailed source spot-check record is invalid: {exc}")
        else:
            detailed_numbers = [check.get("article_number") for check in detailed_spot_checks]
            manifest_numbers = [check.get("article_number") for check in spot_checks]
            if len(detailed_spot_checks) != 20 or len(set(detailed_numbers)) != 20:
                failures.append("The detailed spot-check record needs 20 unique articles")
            if detailed_numbers != manifest_numbers:
                failures.append("Detailed spot checks do not match the manifest sample")
            if any(
                check.get("english_exact_match") is not True
                or check.get("arabic_exact_match") is not True
                for check in detailed_spot_checks
            ):
                failures.append("A detailed English or Arabic source spot check failed")
            if spot_record.get("english_matches") != 20 or spot_record.get("arabic_matches") != 20:
                failures.append("Detailed spot-check totals are not 20/20 in both languages")

    robots_path = ROOT / str(
        manifest.get("access_review", {}).get("robots_snapshot", "")
    )
    if not robots_path.is_file():
        failures.append("The committed robots.txt snapshot is missing")
    else:
        robots_lines = [
            line.strip()
            for line in robots_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if "User-agent: *" not in robots_lines or "Disallow:" not in robots_lines:
            failures.append("The robots.txt snapshot does not show an empty global Disallow")

    documents_checked = 0
    source_documents = manifest.get("documents", [])
    if not isinstance(source_documents, list) or len(source_documents) < 4:
        failures.append("Source manifest does not list the required raw documents")
    else:
        for document in source_documents:
            relative_path = str(document.get("path", ""))
            path = ROOT / relative_path
            if not path.exists():
                failures.append(f"Manifest document is missing: {relative_path}")
                continue
            actual_size = path.stat().st_size
            expected_size = int(document.get("bytes", -1))
            if actual_size != expected_size:
                failures.append(
                    f"Size mismatch for {relative_path}: {actual_size} != {expected_size}"
                )
                continue
            actual_hash = sha256_file(path)
            expected_hash = str(document.get("sha256", ""))
            if actual_hash != expected_hash:
                failures.append(f"SHA-256 mismatch for {relative_path}")
                continue
            documents_checked += 1

    report = {
        "total_articles": total,
        "articles_per_law": [
            {"law_number": number, "law_year": year, "articles": count}
            for number, year, count in per_law_rows
        ],
        "articles_missing_arabic": missing_arabic,
        "articles_missing_english": missing_english,
        "articles_missing_embeddings": missing_embeddings,
        "duplicate_article_numbers": [
            {
                "law_number": number,
                "law_year": year,
                "article_number": article,
                "rows": count,
            }
            for number, year, article, count in duplicate_rows
        ],
        "source_spot_checks_passed": passed_spot_checks,
        "detailed_spot_checks_verified": len(detailed_spot_checks),
        "source_documents_hash_verified": documents_checked,
        "status": "passed" if not failures else "failed",
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
