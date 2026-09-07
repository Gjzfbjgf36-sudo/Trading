.PHONY: install test lint typecheck check

install:
	python3 -m pip install -e ".[dev]"

test:
	python3 -m pytest

lint:
	python3 -m ruff check src tests

typecheck:
	python3 -m mypy

check: lint typecheck test
