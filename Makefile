.PHONY: install test lint run up down demo retrytest

install:
	pip install -r requirements-dev.txt

lint:
	ruff check app workers tests scripts

test:
	pytest tests/unit -q

# Run offline: SQLite, mock CRM, heuristic fit, inline processing (no Redis).
run:
	DATABASE_URL=sqlite+pysqlite:///./dev.sqlite CRM_PROVIDER=mock PROCESS_INLINE=true \
	uvicorn app.main:app --reload

up:
	docker compose up --build

down:
	docker compose down -v

# Seed qualify/disqualify/dedupe leads. Requires the server running.
demo:
	python scripts/seed_demo.py --base-url http://localhost:8000

# Prove idempotency holds under concurrent retries. Requires the server running.
retrytest:
	python scripts/retry_test.py --base-url http://localhost:8000 --concurrency 20
