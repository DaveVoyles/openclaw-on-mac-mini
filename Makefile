.DEFAULT_GOAL := help

.PHONY: test test-cli test-verbose lint lint-fix format type-check build clean deploy deploy-cli verify-deploy ship ship-server ship-cli e2e e2e-macbook slack-manifest slack-manifest-push install-watcher smoke smoke-verbose ci validate-env help

help:  ## Show available targets and descriptions
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

test:  ## Run pytest (quick, stop on first failure)
	.venv/bin/python3 -m pytest tests/ -x -q --tb=short

test-cli:  ## Run standalone CLI/dashboard tests
	.venv/bin/python3 -m pytest --noconftest -o addopts='' tests/test_openclaw_cli.py tests/test_dashboard.py -q

test-verbose:  ## Run pytest with verbose output
	.venv/bin/python3 -m pytest tests/ -v --tb=short

e2e:  ## Run E2E tests locally
	@echo "🧪 Running E2E tests locally..."
	python3 scripts/run_e2e_tests.py

e2e-macbook:  ## Run E2E tests on MacBook (--host macbook, 90s timeout)
	@echo "🧪 Running E2E tests on MacBook..."
	python3 scripts/run_e2e_tests.py --host macbook --timeout 90

lint:  ## Run ruff linter on src/ and tests/
	.venv/bin/ruff check src/ tests/

lint-fix:  ## Auto-fix lint and format issues
	.venv/bin/ruff check src/ tests/ --fix --unsafe-fixes 2>/dev/null || .venv/bin/ruff check src/ tests/ --fix
	.venv/bin/ruff format src/ tests/

format:  ## Auto-format code with ruff
	@echo "🔧 Auto-formatting..."
	.venv/bin/ruff check --fix src/ tests/ 2>/dev/null || true
	.venv/bin/ruff format src/ tests/ 2>/dev/null || true
	@echo "✅ Formatting complete"

type-check:  ## Run type checker (pyright or mypy)
	@echo "🔍 Type checking..."
	.venv/bin/pyright src/ 2>/dev/null || .venv/bin/mypy src/ --ignore-missing-imports 2>/dev/null || echo "⚠️  Install pyright or mypy: pip install pyright"

smoke:  ## Run smoke test tier (fast gate, ~18s)
	.venv/bin/python3 -m pytest -m smoke -q --timeout=30

test-fast:  ## Run all tests except slow and expensive ones
	.venv/bin/python3 -m pytest tests/ -m "not slow and not expensive" -q --timeout=30

smoke-verbose:  ## Run smoke tests with verbose output
	.venv/bin/python3 -m pytest -m smoke -v --timeout=30

ci:  ## Full local CI equivalent (lint + smoke + typecheck)
	@echo "🔍 Running lint..."
	.venv/bin/ruff check src/ tests/
	.venv/bin/ruff format src/ tests/ --check
	@echo "🧪 Running smoke tests..."
	.venv/bin/python3 -m pytest -m smoke -q --timeout=30
	@echo "🔍 Running type check..."
	python3 scripts/mypy_enforce.py || true
	@echo "✅ Local CI complete"

validate-env:  ## Validate .env against .env.example
	python3 scripts/validate_env.py

build:  ## Build Docker image
	@echo "🐳 Building Docker image..."
	docker build -t openclaw:latest .

deploy:  ## Rebuild + restart container (use after git pull/commit)
	@echo "🚀 Rebuilding and restarting container..."
	docker compose build openclaw
	docker compose up -d openclaw
	@echo "✅ Container redeployed"

deploy-cli:  ## Deploy CLI to macbook via SSH/SCP
	@echo "🚀 Deploying OpenClaw CLI to macbook..."
	bash scripts/install_openclaw_cli_remote.sh macbook
	@echo "✅ CLI deployed — run 'make verify-deploy' to confirm"

verify-deploy:  ## Confirm deployed CLI build and server health
	@echo "🔍 Checking deployed CLI version on macbook..."
	@ssh macbook 'python3 -c "import sys; sys.path.insert(0,\"$$HOME/.local/share/openclaw-cli\"); from openclaw_cli import _CLI_BUILD; print(\"build:\", _CLI_BUILD)"' 2>&1 || true
	@echo ""
	@echo "🔍 Checking server health + git SHA..."
	@curl -fsS http://192.168.1.93:8765/health | python3 -m json.tool

# ── Ship: one command to push code, update server, and update MacBook CLI ──
# Usage (from Mac Mini): make ship           -- deploy everything
#         make ship-server  -- restart server container only
#         make ship-cli     -- update MacBook CLI only
ship-server:  ## Pull latest + recreate openclaw container on Mac Mini
	@echo "🔄 Pulling latest on Mac Mini..."
	ssh macmini "cd /Users/davevoyles/openclaw && git pull --ff-only && git rev-parse --short HEAD > src/_git_sha.txt"
	@echo "🧹 Clearing Python bytecode cache to prevent stale .pyc issues..."
	ssh macmini "find /Users/davevoyles/openclaw/src -name '*.pyc' -delete 2>/dev/null; find /Users/davevoyles/openclaw/src -name '__pycache__' -type d -exec rmdir {} + 2>/dev/null; true"
	@echo "🐳 Recreating openclaw container (picks up any docker-compose.yml mount changes)..."
	ssh macmini "cd /Users/davevoyles/openclaw && /usr/local/bin/docker-compose up -d --no-deps --force-recreate openclaw"
	@sleep 8
	@echo "✅ Server health:"
	@curl -fsS http://192.168.1.93:8765/health | python3 -m json.tool

ship-cli:  ## Deploy CLI to MacBook only
	@echo "💻 Deploying CLI to MacBook..."
	bash scripts/install_openclaw_cli_remote.sh macbook
	@echo "✅ CLI deployed to MacBook"

ship: ship-server ship-cli  ## Full deploy: pull + restart server + update MacBook CLI
	@echo ""
	@echo "✅ Both server and CLI updated. Run 'make verify-deploy' to confirm."

install-watcher:  ## Install Mac folder watcher (run once on parent's Mac)
	bash scripts/install_watcher.sh

slack-manifest:  ## Copy Slack manifest to clipboard + open browser
	@echo "🌐 Copying manifest to clipboard and opening browser..."
	@echo "   In the browser: Cmd+A → Cmd+V → Save Changes"
	@echo "   After saving, update SLACK_BOT_TOKEN in .env if Slack issues a new token."
	python3 scripts/update_slack_manifest.py --browser

slack-manifest-push:  ## Push Slack manifest via API (needs SLACK_CONFIG_TOKEN in .env)
	@echo "📋 Pushing Slack manifest via API (requires SLACK_CONFIG_TOKEN in .env)..."
	python3 scripts/update_slack_manifest.py --push

clean:  ## Remove __pycache__, .pyc, and build caches
	@echo "🧹 Cleaning..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	find . -type f -name '.coverage' -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov 2>/dev/null || true
	@echo "✅ Clean complete"
