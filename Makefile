.PHONY: test test-verbose lint

test:
	.venv/bin/python3 -m pytest tests/ -x -q --tb=short

test-verbose:
	.venv/bin/python3 -m pytest tests/ -v --tb=short

lint:
	.venv/bin/ruff check src/ tests/
