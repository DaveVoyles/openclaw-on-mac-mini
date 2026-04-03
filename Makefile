.PHONY: test test-verbose lint format type-check build clean help

test:
	.venv/bin/python3 -m pytest tests/ -x -q --tb=short

test-verbose:
	.venv/bin/python3 -m pytest tests/ -v --tb=short

lint:
	.venv/bin/ruff check src/ tests/

format:
	@echo "🔧 Auto-formatting..."
	.venv/bin/ruff check --fix src/ tests/ 2>/dev/null || true
	.venv/bin/ruff format src/ tests/ 2>/dev/null || true
	@echo "✅ Formatting complete"

type-check:
	@echo "🔍 Type checking..."
	.venv/bin/pyright src/ 2>/dev/null || .venv/bin/mypy src/ --ignore-missing-imports 2>/dev/null || echo "⚠️  Install pyright or mypy: pip install pyright"

build:
	@echo "🐳 Building Docker image..."
	docker build -t openclaw:latest .

clean:
	@echo "🧹 Cleaning..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	find . -type f -name '.coverage' -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov 2>/dev/null || true
	@echo "✅ Clean complete"

help:
	@echo "Available targets:"
	@echo "  test          Run pytest (quick, stop on first failure)"
	@echo "  test-verbose  Run pytest with verbose output"
	@echo "  lint          Run ruff linter"
	@echo "  format        Auto-fix formatting with ruff"
	@echo "  type-check    Run type checker (pyright/mypy)"
	@echo "  build         Build Docker image"
	@echo "  clean         Remove __pycache__, .pyc, caches"
	@echo "  help          Show this help"
