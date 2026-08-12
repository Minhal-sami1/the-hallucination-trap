# The Hallucination Trap

The Hallucination Trap is a live audit for fabricated legal citations. It sends one legal question through two answer paths at the same time:

- **Ungrounded model:** no retrieval. The prompt asks the model to cite specific articles.
- **Grounded pipeline:** official corpus retrieval, cross-encoder ranking, constrained generation, citation parsing, and exact verification.

The audit shows each citation while the answers stream. A green verdict means that the exact article key exists. A red verdict means that the citation resolves to the loaded law, but the article does not exist. An amber verdict means that the citation is outside the loaded corpus or is incomplete.

> **Independent demo built by Minhal Abdul Sami. Not affiliated with HAQQ.**

This project is a technical demonstration. It is not legal advice. A `VERIFIED` verdict proves that an article number exists in the identified law. It does not prove that the article supports the answer.

## Public artifacts

| Artifact | Status |
| --- | --- |
| Live HTTPS demo | **Pending deployment. No public URL is claimed in this README yet.** |
| Public repository | <https://github.com/Minhal-sami1/the-hallucination-trap> |
| Three-minute Loom | **Pending recording.** Use the exact [recording plan](docs/LOOM_SCRIPT.md). |

## Quick start

### Host requirements

- Docker Engine or Docker Desktop with Docker Compose v2
- An internet connection for the first image and model download
- Optional: GNU Make for the short command aliases in the repository

Python 3.12, Node.js 22, PostgreSQL 16, pgvector, and all application packages run in containers. You do not need to install those packages on the host.

After the public repository exists, clone it and run one command:

```bash
git clone https://github.com/Minhal-sami1/the-hallucination-trap.git
cd the-hallucination-trap
make demo
```

If GNU Make is not installed, this one Docker Compose command performs the same operation:

```bash
docker compose build api web && docker compose up -d db && docker compose run --rm api alembic -c api/alembic.ini upgrade head && docker compose run --rm api python /workspace/scripts/ingest.py && docker compose run --rm api python /workspace/scripts/parse_official_html.py --check && docker compose run --rm api python /workspace/scripts/verify_corpus.py && docker compose up
```

`make demo` builds the services, starts PostgreSQL, runs the migration, ingests the complete corpus, verifies it, and starts the application. The first ingest downloads the pinned embedding and reranker files. A later run uses the Docker model cache.

Open <http://localhost:5173>. Open <http://localhost:5173/results> for the measured results. Use `Ctrl+C` to stop the attached services. Run `make down` to stop the project services.

The exact definition-of-done sequence is:

```bash
make setup && make ingest && make verify
make run
```

`make setup` builds the API and web development images, starts PostgreSQL, and applies the Alembic migration. `make ingest` creates embeddings and upserts all articles. `make verify` exits with a nonzero status if a required corpus check fails. `make run` starts the API on port 8000 and the Vite frontend on port 5173.

## Required commands

| Command | Action |
| --- | --- |
| `make setup` | Build the development images, start PostgreSQL, and run the migration. |
| `make parse-corpus` | Rebuild 1,422 bilingual records from the saved official DOM and compare them with the committed extracts. |
| `make ingest` | Load the committed official extracts, create embeddings, prewarm the reranker, and populate PostgreSQL. |
| `make verify` | Parse the raw official DOM, then check counts, languages, embeddings, duplicate keys, detailed spot-check records, `robots.txt`, file sizes, and SHA-256 values. |
| `make eval` | Run the fixed 50-question evaluation and replace `results/eval.json`. |
| `make test` | Run the full Python `pytest` suite. |
| `make lint` | Run Ruff on `api`, `scripts`, and `tests`. |
| `make run` | Start the API and frontend development services. |
| `make demo` | Run setup, ingest, verify, and then start all services. |
| `make check-trap` | Run the primary cached Arabic trap 10 times. Require each red verdict in less than 2,000 ms. This alias uses POSIX shell parameter expansion. On PowerShell, use the direct Node command below. |
| `make logs` | Follow the Docker Compose logs. |
| `make down` | Stop the Docker Compose services. The database and model volumes remain. |

Run the frontend checks in its Node container:

```bash
docker compose run --rm web npm test
docker compose run --rm web npm run build
```

On Windows PowerShell, run the trap check directly:

```powershell
node scripts/check_primary_trap.mjs http://127.0.0.1:8000 10 2000
```

## Configuration

The application has safe local defaults. An `.env` file is optional. To change a value, copy `.env.example` to `.env` and edit it. Every supported variable is documented in [.env.example](.env.example).

| Variable | Default or state | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://trap:trap@db:5432/hallucination_trap` | SQLAlchemy URL for the API, migration, ingest, verify, and evaluation commands. |
| `OPENAI_API_KEY` | Empty | Enable live OpenAI generation. |
| `AZURE_OPENAI_ENDPOINT` | Empty | Azure OpenAI endpoint. Set it with `AZURE_OPENAI_KEY`. |
| `AZURE_OPENAI_KEY` | Empty | Azure OpenAI key. Set it with `AZURE_OPENAI_ENDPOINT`. |
| `EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2@faf4aa4225822f3bc6376869cb1164e8e3feedd0` | Pinned 384-dimension multilingual embedding model. |
| `RERANKER_MODEL` | `onnx-community/gte-multilingual-reranker-base@ee64367e35a2db0da46bb6497e13a18f8bd585cb` | Pinned multilingual cross-encoder. |
| `CACHE_MODE` | `cached` | Default answer mode. Valid values are `cached` and `live`. |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Live generation model when a provider is configured. |
| `RETRIEVAL_CONFIDENCE_THRESHOLD` | `0.44` | Refuse a grounded answer below this normalized score. |
| `RETRIEVE_TIMEOUT_SECONDS` | `4` | Retrieve-stage timeout. |
| `RANK_TIMEOUT_SECONDS` | `12` | Rank-stage timeout. |
| `GENERATE_TIMEOUT_SECONDS` | `25` | Generate-stage timeout. |
| `PARSE_TIMEOUT_SECONDS` | `2` | Timeout for each citation parse operation. |
| `VERIFY_TIMEOUT_SECONDS` | `2` | Timeout for each exact citation check. |
| `VITE_API_BASE` | `http://localhost:8000` | Browser-visible API origin for the Vite development server. |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:8080` | Comma-separated browser origins that can call the API. |
| `MODEL_CACHE_DIR` | `/models` in Docker Compose | Persistent directory for local model files. |
| `HALLITRAP_DB_PASSWORD` | Empty | Optional deployment-only PostgreSQL password. A first deployment generates one in memory when empty. A rerun reuses its guarded stored database secret, or rotates the password if that secret is unavailable. |

If both provider types are configured, `OPENAI_API_KEY` takes precedence. The Azure client uses API version `2025-04-01-preview`.

The Azure deployment script reads `HALLITRAP_DB_PASSWORD` from the current PowerShell process. It does not load `.env`.

## Cached mode and live mode

The mode control is always visible in the interface.

**Cached mode is the default recording mode.** It replays one committed, model-produced ungrounded answer for a selected seed question. The interface marks the run as cached. Free-text questions do not run in cached mode because they have no recorded ungrounded answer.

The grounded answer is never a cached answer. In both modes it performs retrieval, ranking, the confidence gate, deterministic top-1 extractive synthesis, parsing, and exact verification against the current database. It cites only the highest-ranked article. A low-confidence result produces a visible refusal.

**Live mode** becomes available only when an OpenAI key or a complete Azure OpenAI configuration is present. The left path streams real provider deltas as they arrive. The right path stays local and structurally constrained to the reranked official provision. A provider error appears in the left answer panel. The server does not silently replace a live result with a cached result.

The committed seed set contains 8 questions. Each seed was retained after 10 closed-book runs. The runtime parser and verifier detected at least one nonexistent article in all 80 retained runs. This is a selected challenge set, not an unbiased estimate of general model behavior. See [Evaluation and limits](#evaluation-and-limits).

## Corpus

### Loaded law

The only loaded instrument is the attached **Civil Transactions Law in Federal Decree by Law No. (25) of 2025**. The jurisdiction is UAE federal law.

- Official English page: <https://uaelegislation.gov.ae/en/legislations/4011>
- Official Arabic page: <https://uaelegislation.gov.ae/ar/legislations/4011>
- Status at retrieval: active
- Issue date: 1 October 2025
- Official Gazette: number 809, dated 14 October 2025
- Effective date: 1 June 2026
- Loaded articles: the complete attached-law range 1 through 1,422
- Canonical language: Arabic. The portal states that Arabic prevails if the English translation conflicts with it.

The promulgating instrument has three separate articles. The ingest process excludes them because they repeat article numbers 1 through 3. Federal Law No. 5 of 1985 is an archived reference only. Article 2 of the 2025 decree repealed it from 1 June 2026. The ingest process does not load the 1985 law.

The files came from the official UAE Legislation portal. The acquisition used the public law pages and normal browser PDF downloads. It did not bypass access controls. The saved `robots.txt` had an empty `Disallow` value on 12 August 2026. The source portal reserves its rights. This repository keeps official attribution and source links. It does not claim ownership of the law text or grant a new license for that text.

Read [data/raw/SOURCE_NOTES.md](data/raw/SOURCE_NOTES.md) and [data/raw/source_manifest.json](data/raw/source_manifest.json) for acquisition details, terms review, URLs, byte counts, timestamps, and the article boundary decision.

### Source hashes

`make verify` recalculates every hash in the source manifest and exits with a nonzero status on a mismatch.

| Committed source | Role | SHA-256 |
| --- | --- | --- |
| `data/raw/official_structured_articles_en.json` | Loaded English extract, 1,422 articles | `3d216a3bebb68e63aae71ef07f93928e0d955418492c714719db5366f734c6ba` |
| `data/raw/official_structured_articles_ar.json` | Loaded Arabic extract, 1,422 articles | `6c6b1297b12479aad0cc1cfb45e2674ab24560d0af2ca699516fa8b86d841355` |
| `data/raw/official_article_dom_2025_en.html` | Raw English official article DOM, 1,422 blocks | `85b990e46ca7b359a0c091efede862d30b530e87654a98bb9ac572f5aa97bd5b` |
| `data/raw/official_article_dom_2025_ar.html` | Raw Arabic official article DOM, 1,422 blocks | `3214659f843cc193cdf1b7e23659a2d0d864198abe8f91e9bb9d74f30819f300` |
| `data/raw/uae_civil_transactions_2025_en.pdf` | Current official English PDF | `01bcc98e76de3c9bedb555a2664452bc1a215a2ed28bafd987f3c1ba206f28a6` |
| `data/raw/uae_civil_transactions_2025_ar.pdf` | Current official Arabic PDF | `5c97d04428bbd34b426cb14b0fe0dbc3041951577f0482c3128ed48cecfc621e` |
| `data/raw/uae_civil_transactions_1985_en.pdf` | Archived English reference; not loaded | `d05d02f053cfb9cac6fe769aa1942d69957b5d56de227efe790af2ff48edc4bd` |
| `data/raw/uae_civil_transactions_1985_ar.pdf` | Archived Arabic reference; not loaded | `75eefab5a3dff852b438779ec4cc59921345f6ebb1d933d1aa484400b0b80ff5` |

The ingest summary also prints the combined hash of the two loaded extracts: `47c616c22447a440bc36387575fc3c81cd3f6daae9c0442bb5daee80255576f6`.

### Manual source check

A deterministic random sample of 20 different articles was opened on both official pages in a real browser. The stored English text, Arabic text, and official DOM anchor were compared after documented whitespace normalization. All 20 English checks and all 20 Arabic checks passed.

The sample seed is `20260812`. This command reproduces the sampled article numbers:

```bash
docker compose run --rm api python -c "import random; random.seed(20260812); print(','.join(map(str,sorted(random.sample(range(1,1423),20)))))"
```

The result is `83,109,137,357,364,373,493,609,628,733,858,952,987,1195,1206,1226,1254,1308,1389,1422`. The complete manual record is [data/raw/spot_checks_2026-08-12.json](data/raw/spot_checks_2026-08-12.json). The command reproduces the selection. `make verify` checks the committed record and file hashes. A new independent source check must open the official URLs again.

## Citation index and verdicts

PostgreSQL stores one row for each article. The migration creates:

- a strict unique key on `(law_number, law_year, article_number)`;
- an English GIN full-text index;
- a separate Arabic GIN full-text index with the `simple` configuration;
- a 384-dimension pgvector column; and
- an HNSW cosine index on the vector column.

The verifier gives one of three results:

- `VERIFIED`: one exact article key exists. The UI shows up to 200 text characters and the official article link.
- `FABRICATED`: the citation resolves to the loaded law, but the exact article key does not exist. The reason names the law, its article count, and the missing article.
- `UNVERIFIABLE`: the citation is incomplete, ambiguous, or outside the loaded corpus. The system does not mark it red.

A short reference such as `Article 261` does not receive an assumed law context in free-text or live output. It stays `UNVERIFIABLE` unless the answer identifies the law. The one exception is an exact committed cached seed: its stored question identity supplies the loaded-law context. An explicit reference to another law stays outside the loaded corpus and receives `UNVERIFIABLE`. This boundary prevents a real article from another law from receiving a red verdict.

The parser supports `Article`, `Art.`, `Art`, `Section`, `s.`, Arabic `المادة`, Western digits, Arabic-Indic digits, law number and year forms, joined Arabic prepositions, malformed references, and partial streaming references. Parser and verdict behavior have direct unit tests in `tests/test_citation_parser.py` and `tests/test_citation_verification.py`.

## Pipeline

The server runs the two answer paths concurrently and sends Server-Sent Events. The explicit grounded order is:

```text
retrieve -> rank -> generate -> parse -> verify
```

1. **Retrieve.** PostgreSQL `tsvector` search selects lexical candidates. Application BM25 reranks those candidates. A 384-dimension multilingual query vector searches pgvector with cosine distance. Reciprocal-rank fusion combines lexical and vector results.
2. **Rank.** A multilingual cross-encoder reranks up to 12 hybrid candidates and returns up to 6 provisions.
3. **Generate.** A confidence below `0.44` causes a refusal. Otherwise, the grounded lane produces a deterministic extractive answer from only the top provision. The live ungrounded lane streams real provider deltas.
4. **Parse.** The parser reads stable citation forms while answer text streams. It performs one final parse when the answer ends.
5. **Verify.** An exact database lookup assigns the verdict and returns official proof when a match exists.

Each stage has its own timeout. Retrieval has 4 seconds, ranking has 12 seconds, generation has 25 seconds, and each parse and verify operation has 2 seconds. You can change these values in `.env`.

If ranking fails, the server marks that stage as degraded and continues with hybrid retrieval order. If another stage fails or times out, the server sends a visible recoverable error event to that answer panel and completes the stream. A low-confidence refusal is a correct grounded outcome, not a service error.

## Pinned local models

The source model cards label both local retrieval models as Apache-2.0. The repository includes the Apache 2.0 text and the two font OFL 1.1 texts under `third_party/licenses/`; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The production image downloads fixed revisions, copies the notices, and prewarms both models before the container can become ready.

| Stage | Runtime name | Download source and fixed revision | Verified artifact hashes |
| --- | --- | --- | --- |
| Embedding | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2@faf4aa4225822f3bc6376869cb1164e8e3feedd0` | [Qdrant ONNX port](https://huggingface.co/Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q/tree/faf4aa4225822f3bc6376869cb1164e8e3feedd0), revision `faf4aa4225822f3bc6376869cb1164e8e3feedd0` | ONNX: `634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99`; tokenizer: `fa685fc160bbdbab64058d4fc91b60e62d207e8dc60b9af5c002c5ab946ded00` |
| Reranking | `onnx-community/gte-multilingual-reranker-base@ee64367e35a2db0da46bb6497e13a18f8bd585cb` | [ONNX Community port](https://huggingface.co/onnx-community/gte-multilingual-reranker-base/tree/ee64367e35a2db0da46bb6497e13a18f8bd585cb) of [Alibaba-NLP GTE](https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base), revision `ee64367e35a2db0da46bb6497e13a18f8bd585cb` | INT8 ONNX: `ccf51dba7f8aa9205753761cfaa68c55f741792501463a3bf25d7e5bcdac7c35` |

`scripts/fetch_models.py` uses resumable downloads, five attempts, fixed revisions, and SHA-256 checks for the large model and tokenizer artifacts. Other small files are protected by their fixed repository revision.

The repository has no project `LICENSE` file at this time. Do not infer a license for the application code or the official legal text.

## Evaluation and limits

Run:

```bash
make eval
```

The command reads 50 corpus-derived questions with hand-checked expected article sets from `data/eval_questions.json`. It runs the current local grounded pipeline. It also sends all 80 committed ungrounded run texts through the current runtime parser and exact verifier. The false-positive check contains the 51 expected current-law citations and 33 hand-checked citation-form controls. The controls include 18 real archived-law references that must stay amber, never red. The command writes the full result to [results/eval.json](results/eval.json). The `/results` page reads that file through `/api/results`.

The current committed result was generated on 12 August 2026:

| Metric | Exact count | Reported value |
| --- | ---: | ---: |
| Grounded citation precision | 45 correct / 50 predicted | 90.0% |
| Grounded citation recall | 45 correct / 51 expected | 88.24% |
| Grounded exact-set match | 44 questions / 50 questions | 88.0% |
| Grounded answer coverage | 50 answered / 50 questions | 100.0% |
| Retrieval top-1 hit rate | 45 hits / 50 questions | 90.0% |
| Retrieval expected-article recall at 6 | 48 hits / 51 expected | 94.12% |
| Ungrounded challenge-set fabrication rate | 80 detected / 80 retained runs | 100.0% |
| Detection recall on declared fabricated runs | 80 detected / 80 declared | 100.0% |
| Verifier false-positive rate | 0 real citations marked fabricated / 84 real citations | 0.0% |
| Real citations marked unverifiable | 18 / 84 real citations | 21.43% |
| Grounded answer citations marked fabricated | 0 / 50 predicted citations | 0.0% |

The metric limits are important:

- Precision and recall use exact article-number set matches. They do not measure semantic support for each sentence.
- The 50 questions come from the loaded corpus. The expected article numbers are not given to retrieval or ranking.
- The seed and evaluation sets are not article-independent. All 13 seed-expected articles occur in the evaluation set. A total of 12 of 50 evaluation questions touch a seed-expected article.
- The 8 seed questions were selected because they passed a fabrication retention threshold of at least 8 of 10 runs. All retained seeds later show 10 of 10. The 100% result applies only to this selected challenge set.
- The recorded ungrounded runs name `gpt-5.6 Codex subagent` as the producer. The artifact does not contain provider response IDs or sampling parameters. Independent model replay is not possible.
- The current grounded evaluation uses local constrained top-1 extractive synthesis with the production confidence gate. It does not evaluate a live provider response.

Input hashes make the result inputs testable:

- `data/eval_questions.json`: `d3ce190408d04b780c973c913e728f72850d9803482648c8de8d4dc5df200059`
- `results/seed_runs.json`: `dae2fd7fe0b42d79d784d6edac83c29c97c89a9e7043d5ac85c9f099ac8c3e49`
- `data/verifier_real_controls.json`: `ae745fce6b2478385838ce6ecc27a4ccaa15b44fc7e8167cc6556db610369095`

## Reproduce every interface number

The interface does not contain manually entered metric values. Use these sources and commands:

| Interface value | Source | Reproduction command |
| --- | --- | --- |
| Header corpus count, law identity, and footer source link | PostgreSQL and `/api/config` | `make verify` |
| Footer count of fabricated citations caught | `results/seed_runs.json` | `docker compose run --rm api python -c "import json; p=json.load(open('results/seed_runs.json',encoding='utf-8')); print(p['verification']['runs_with_fabricated_article'])"` |
| Seed-chip fabrication rate and run count | `data/seed_questions.json` and `results/seed_runs.json` | `make eval` and inspect `results/eval.json` -> `ungrounded_runtime_audit` |
| Primary red verdict in 10 of 10 runs, under 2 seconds | Live SSE endpoint | `make check-trap` |
| Per-side verified, fabricated, and unverifiable counts | Citation events in the current SSE stream | Run a question. Count the visible audit rows by side and verdict. |
| Per-side defensibility score | Current SSE citation events | Compute `VERIFIED / (VERIFIED + FABRICATED + UNVERIFIABLE)`. The UI uses this formula. |
| Results-page metrics, 50 rows, and 1,422 corpus articles | `results/eval.json` | `make eval` |
| Results table row numbers | Ordered question array in `results/eval.json` | `make eval` |

For a public or production endpoint, pass the API base URL directly:

```bash
node scripts/check_primary_trap.mjs https://PUBLIC_HOST_PENDING 10 2000
node scripts/measure_fcp.mjs https://PUBLIC_HOST_PENDING
```

The first script exits with a nonzero status unless all 10 runs receive the fabricated verdict in less than 2,000 ms. The second script uses a clean headless Chromium profile and exits with a nonzero status unless first contentful paint is below 3,000 ms. These two scripts require Node.js 22 or later. The paint script also requires Chrome, Edge, or Chromium, or a `CHROME_PATH` value.

## Production image

The root `Dockerfile` builds the React application, installs the FastAPI service, downloads and verifies the pinned models, copies the corpus and evaluation result, and runs the service as the non-root `trap` user. FastAPI serves both the SPA and the API on port 8000. The readiness process loads and executes both local models before `/health` can report the service.

Build and run the production image against a PostgreSQL service with pgvector:

```bash
docker build -t hallucination-trap:local .
```

The development `docker-compose.yml` is the supported local orchestration path because it supplies PostgreSQL, migrations, corpus ingestion, volumes, and all required environment values.

## Azure deployment

The deployment script creates paid Azure resources. It creates a dedicated resource group, Azure Container Registry, Container Apps environment, PostgreSQL 16 Flexible Server with pgvector, one initialization job, and the public Container App. On a first deployment, the initialization job runs the migration, ingest, and verification commands before the app is created. On a rerun, the existing app resource remains deployed; its secret and revision update wait until initialization succeeds. The script stores the database URL and ACR pull credentials as Container Apps secrets and does not print them.

The current subscription role can manage ACR but cannot create role assignments. For this dedicated proof environment, the script therefore enables the ACR admin account and supplies its credentials only through secret-bearing CLI arguments. This is broader than a repository-scoped token or managed-identity pull. Use a scoped token or managed identity when the deployment owner has the required IAM authority.

Requirements:

- Azure CLI
- Git and a clean committed worktree
- internet access for the script to install or upgrade the `containerapp` CLI extension
- an authenticated Azure account: `az login`
- Contributor access to the target subscription
- PostgreSQL Flexible Server `Standard_B1ms` availability in UAE North

Deploy from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy_azure.ps1 -SubscriptionId <subscription-id> -Location uaenorth
```

The script is rerunnable. It rejects an existing named resource before reuse or update unless its UAE North location and all dedicated ownership tags match. Before the remote build, it uses `git archive` to make a temporary build context from the exact committed `HEAD`. Ignored local caches and uncommitted files cannot enter the image. The script stops if the worktree is dirty. It prints the public HTTPS URL and exact verification commands only after the initialization job succeeds and `/health` reports all 1,422 articles. Do not commit `HALLITRAP_DB_PASSWORD` or any provider key. If you set `HALLITRAP_DB_PASSWORD`, keep it only in the current process environment.

After deployment, run:

```powershell
Invoke-RestMethod 'https://PUBLIC_HOST_PENDING/health' | ConvertTo-Json
node scripts/check_primary_trap.mjs 'https://PUBLIC_HOST_PENDING' 10 2000
node scripts/measure_fcp.mjs 'https://PUBLIC_HOST_PENDING'
```

## Add a jurisdiction

Read the half-page [jurisdiction extension note](docs/EXTENDING_JURISDICTIONS.md). It defines the source adapter contract, schema change for multiple jurisdictions, manual verification process, test sequence, and a reproducible planning cost formula.

Important rule: use an official consolidated source. Keep the unchanged source files, source terms review, timestamps, hashes, and article URLs. Do not use an LLM to create or repair legal text.

## Repository map

```text
api/          FastAPI service, SQLAlchemy models, parser, verifier, and migration
web/          React and TypeScript single-page application
data/raw/     Official source files, manifest, robots record, and spot checks
scripts/      Ingest, verify, evaluation, model, deployment, and performance tools
tests/        Pytest suites for parser, verifier, corpus, and pipeline stages
results/      Reproducible evaluation and seed-run evidence
docs/         Jurisdiction extension note and Loom recording plan
```

The public API includes `/health`, `/api/config`, `/api/seeds`, `/api/audit-stream`, `/api/results`, and exact article lookup at `/api/articles/{law_number}/{law_year}/{article_number}`.

## Independence

The footer on every application page states:

> Independent demo built by Minhal Abdul Sami. Not affiliated with HAQQ.

Do not remove this statement. Do not use the HAQQ logo. Do not present this project as a HAQQ product or a HAQQ endorsement.
