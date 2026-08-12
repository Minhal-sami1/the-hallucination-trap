"""Download both local retrieval models into the configured cache."""

from api.app.embeddings import embed_query
from api.app.ranking import prewarm_reranker


def main() -> int:
    vector = embed_query("Civil Transactions Law قانون المعاملات المدنية")
    if len(vector) != 384:
        raise RuntimeError(f"Embedding dimension is {len(vector)}, expected 384")
    prewarm_reranker()
    print("Embedding and reranker models are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
