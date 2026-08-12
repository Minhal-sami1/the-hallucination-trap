.PHONY: setup parse-corpus ingest verify eval test lint run demo check-trap down logs

setup:
	docker compose build api web
	docker compose up -d db
	docker compose run --rm api alembic -c api/alembic.ini upgrade head

parse-corpus:
	docker compose run --rm api python /workspace/scripts/parse_official_html.py --check

ingest:
	docker compose run --rm api python /workspace/scripts/ingest.py

verify:
	docker compose run --rm api python /workspace/scripts/parse_official_html.py --check
	docker compose run --rm api python /workspace/scripts/verify_corpus.py

eval:
	docker compose run --rm api python /workspace/scripts/run_eval.py

test:
	docker compose run --rm api pytest -q

lint:
	docker compose run --rm api ruff check api scripts tests

run:
	docker compose up --build

demo: setup ingest verify
	docker compose up

check-trap:
	node scripts/check_primary_trap.mjs $${URL:-http://127.0.0.1:8000} 10 2000

down:
	docker compose down

logs:
	docker compose logs -f api web
