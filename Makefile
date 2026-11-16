.PHONY: install test lint dev up down logs

install:
	python3 -m venv .venv
	.venv/bin/pip install -q -e '.[dev]'

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check .

# Local, no Docker: http://localhost:8080 (passwords from .env or the defaults in config.py)
dev:
	.venv/bin/uvicorn freezerhost.app:create_app --factory --port 8080 --reload

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100
