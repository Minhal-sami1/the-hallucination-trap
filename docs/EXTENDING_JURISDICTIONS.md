# Extend the index to a new jurisdiction

The current adapter loads one official UAE federal instrument. The measured source set has 1,422 English articles, 1,422 Arabic articles, two structured browser extracts, four official PDFs, one `robots.txt` record, and 20 deterministic browser spot checks. `data/raw/source_manifest.json` holds the URLs, byte counts, SHA-256 values, retrieval times, and check results. This structure is the acquisition contract for the next jurisdiction.

Start with an official consolidated source. Review its access rules before download. Store the unchanged files in `data/raw/`. Add a manifest entry for every file. A source adapter must return the fields used by `scripts/corpus_io.py`: law name, law number, law year, article number, English text, Arabic text when published, official article URL, and retrieval time. Do not use an LLM to create or repair article text. Keep the source-language text as canonical.

The citation parser is separate from storage. Add only the grammar needed for the new source forms in `api/app/citations.py`. The verifier depends on this storage interface in `api/app/verification.py`:

```python
find_laws(law_number=None, law_year=None, law_name=None) -> Sequence[LawRecord]
get_article(law_number, law_year, article_number) -> ArticleRecord | None
```

Implement that protocol in a repository adapter. Keep exact lookups exact. Before a second jurisdiction is loaded, add a `jurisdiction` column and extend the unique key to `(jurisdiction, law_number, law_year, article_number)`. Also add jurisdiction resolution to parsed references. This migration prevents identical law numbers in two countries from colliding.

Use a fixed random seed to select 20 different articles. Open each official URL in a normal browser. Compare the stored text in every published language after documented whitespace normalization. Record the article number, source anchor, text hash, check time, and pass result. Then run:

```text
make setup
make ingest
make verify
make test
make eval
```

`make verify` must stop on a missing language, duplicate key, source hash change, incomplete range, or fewer than 20 passed checks. `make eval` must use hand-checked questions for the new law and write the measured values to `results/eval.json`.

## Time and cost model

The following values are planning assumptions, not measured labour. For one clean bilingual law, allow 2 hours for source and terms review, 6 hours for the parser and adapter, 20 checks at 4 minutes each, and 3 hours for ingest and evaluation. This is 12.33 hours. At an assumed USD 100 per hour, the one-time labour estimate is USD 1,233.33. Reproduce it with:

```text
python -c "print(round((2+6+20*4/60+3)*100, 2))"
```

Add 25 to 50 percent time for scanned PDFs, unstable numbering, or a missing official translation. Cloud cost is separate and changes by contract and date. The deployment cost formula is: UAE North PostgreSQL B1ms compute + 32 GB database storage + a minimum of one always-on Container Apps replica with scale up to three + each initialization-job execution + each ACR build + ACR Basic + data transfer. Use the Azure pricing calculator on the deployment date. Do not copy an old currency amount into a proposal.
