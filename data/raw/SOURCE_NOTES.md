# UAE Civil Transactions corpus source notes

## Shipped corpus

The shipped corpus is the attached Civil Transactions Law in Federal Decree by Law No. 25 of 2025. The law is active. It took effect on 1 June 2026.

The attached law has 1,422 articles. The article numbers are a complete range from 1 to 1422. The promulgating decree has three separate articles. The ingest process must exclude those three articles because they repeat article numbers 1 to 3.

Federal Law No. 5 of 1985 is not the shipped corpus. Article 2 of the 2025 decree repealed it from 1 June 2026. Its two PDFs stay in `data/raw` only as archived reference files. The ingest process must not load them.

## Official sources

- Current English page: <https://uaelegislation.gov.ae/en/legislations/4011>
- Current Arabic page: <https://uaelegislation.gov.ae/ar/legislations/4011>
- Current English PDF: <https://uaelegislation.gov.ae/en/legislations/4011/download>
- Current Arabic PDF: <https://uaelegislation.gov.ae/ar/legislations/4011/download>
- Archived English page: <https://uaelegislation.gov.ae/en/legislations/1025/archived>
- Archived Arabic page: <https://uaelegislation.gov.ae/ar/legislations/1025/archived>

The source is the official UAE Legislation portal. The portal states that the Arabic text prevails if the English translation conflicts with it. Treat Arabic as canonical and English as the official translation.

Each structured record has a `source_dom_id`. Build its working source URLs as follows:

```text
https://uaelegislation.gov.ae/en/legislations/4011#{source_dom_id}
https://uaelegislation.gov.ae/ar/legislations/4011#{source_dom_id}
```

## Access and source terms

The portal's `robots.txt` had an empty `Disallow` value when checked on 12 August 2026. A copy is in this folder.

The terms of use permit direct links to all hosted pages. They prohibit unauthorized access, security tests, service interference, and excess load. The acquisition used the public pages and the normal EN and Arabic download links. Direct command-line requests received a Cloudflare 403 response, so the files were downloaded with a normal browser. No protection was bypassed.

The two `official_article_dom_2025_*.html` files are reduced browser DOM
snapshots. On 12 August 2026, the official English and Arabic pages were opened
in a normal browser. The browser selected every `.content_` block whose `h4`
heading was an attached-law article from 1 through 1422 and saved its unchanged
`outerHTML`. Page navigation, banners, and scripts were not retained. This
keeps the raw evidence small and prevents active third-party code from being
committed.

Run this deterministic check to parse, clean, and compare every saved article
with the structured JSON used by ingestion:

```text
make parse-corpus
```

`scripts/parse_official_html.py` uses only the Python standard library. It
normalizes CRLF to LF, changes non-breaking spaces to normal spaces, trims each
line, removes blank lines, and preserves other text. It fails unless each
language has one contiguous article range from 1 through 1422 and the rebuilt
records match the committed extracts exactly.

The site footer states that rights are reserved. This repository keeps the exact official links and hashes. It does not claim ownership of the official law text or a new license.

## File and count checks

The 2025 English PDF is a valid, tagged, unencrypted 354-page PDF. The 2025 Arabic PDF is a valid, tagged, unencrypted 204-page PDF. The first and final pages were rendered and inspected. Both language files end with Article 1422.

The English PDF contains 1,425 standalone article headings. Three are in the promulgating decree. The remaining headings form the complete attached-law range from Article 1 through Article 1422, with no missing number.

The structured files passed these checks:

- 1,422 English records and 1,422 Arabic records.
- Article range 1 to 1422 in both files.
- No missing article number.
- No duplicate article number.
- No empty English or Arabic article text.
- No mismatch between English and Arabic article numbers or official DOM anchors.

All parser inputs, structured extracts, and PDF file sizes and SHA-256 hashes
are in `source_manifest.json`.

## Source spot check

A deterministic random sample of 20 articles was checked against both official browser pages. The sample used Python seed `20260812`. All 20 English texts and all 20 Arabic texts matched the committed structured extracts after documented whitespace normalization. The official anchor IDs also matched.

The full sample, exact-match result, character counts, and reproduction command are in `spot_checks_2026-08-12.json`.
