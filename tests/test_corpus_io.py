from scripts.corpus_io import EXPECTED_ARTICLE_COUNT, load_corpus


def test_committed_bilingual_source_is_complete() -> None:
    corpus = load_corpus()

    assert len(corpus) == EXPECTED_ARTICLE_COUNT
    assert [article.article_number for article in corpus] == list(
        range(1, EXPECTED_ARTICLE_COUNT + 1)
    )
    assert all(article.article_text_en for article in corpus)
    assert all(article.article_text_ar for article in corpus)


def test_source_links_are_official_and_article_specific() -> None:
    article = load_corpus()[260]

    assert article.article_number == 261
    assert article.source_url == (
        "https://uaelegislation.gov.ae/en/legislations/4011#item42914"
    )
    assert article.source_url_ar == (
        "https://uaelegislation.gov.ae/ar/legislations/4011#item42914"
    )
