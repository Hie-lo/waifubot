.PHONY: test lint fmt migrate inspect-schema

test:
	python3 -m pytest

lint:
	python3 -m ruff check .

fmt:
	python3 -m ruff check --fix .

migrate:
	python3 -m alembic upgrade head

inspect-schema:
	PYTHONPATH=src python3 -m waifubot.db.inspect
