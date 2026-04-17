.PHONY: test test-cli test-verbose lint format type-check build clean deploy deploy-cli verify-deploy ship ship-server ship-cli help

test:
	.venv/bin/python3 -m pytest tests/ -x -q --tb=short

test-cli:
	.venv/bin/python3 -m pytest --noconftest -o addopts='' tests/test_openclaw_cli.py tests/test_dashboard.py -q

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

deploy:
	@echo "🚀 Rebuilding and restarting container..."
	docker compose build openclaw
	docker compose up -d openclaw
	@echo "✅ Container redeployed"

deploy-cli:
	@echo "🚀 Deploying OpenClaw CLI to macbook..."
	bash scripts/install_openclaw_cli_remote.sh macbook
	@echo "✅ CLI deployed — run 'make verify-deploy' to confirm"

verify-deploy:
	@echo "🔍 Checking deployed CLI version on macbook..."
	@ssh macbook 'python3 -c "import sys; sys.path.insert(0,\"$$HOME/.local/share/openclaw-cli\"); from openclaw_cli import _CLI_BUILD; print(\"build:\", _CLI_BUILD)"' 2>&1 || true
	@echo ""
	@echo "🔍 Checking server health + git SHA..."
	@curl -fsS http://192.168.1.93:8765/health | python3 -m json.tool

# ── Ship: one command to push code, update server, and update MacBook CLI ──
# Usage (from Mac Mini): make ship           -- deploy everything
#         make ship-server  -- restart server container only
#         make ship-cli     -- update MacBook CLI only
ship-server:
	@echo "🔄 Pulling latest on Mac Mini..."
	git pull --ff-only
	@echo "🐳 Restarting openclaw container to load new Python code..."
	/usr/local/bin/docker restart openclaw
	@sleep 5
	@echo "✅ Server health:"
	@curl -fsS http://192.168.1.93:8765/health | python3 -m json.tool

ship-cli:
	@echo "💻 Deploying CLI to MacBook..."
	bash scripts/install_openclaw_cli_remote.sh macbook
	@echo "✅ CLI deployed to MacBook"

ship: ship-server ship-cli
	@echo ""
	@echo "✅ Both server and CLI updated. Run 'make verify-deploy' to confirm."

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
	@echo "  test-cli      Run standalone CLI/dashboard tests with minimal pytest bootstrap"
	@echo "  test-verbose  Run pytest with verbose output"
	@echo "  lint          Run ruff linter"
	@echo "  format        Auto-fix formatting with ruff"
	@echo "  type-check    Run type checker (pyright/mypy)"
	@echo "  build         Build Docker image"
	@echo "  deploy        Rebuild + restart container (use after git pull/commit)"
	@echo "  deploy-cli    Deploy CLI Python files to macbook via SSH/SCP"
	@echo "  verify-deploy Confirm deployed CLI build and server health"
	@echo "  ship          Pull + restart server + deploy CLI (full deploy in one step)"
	@echo "  ship-server   Pull latest + restart openclaw container only"
	@echo "  ship-cli      Deploy CLI to MacBook only"
	@echo "  clean         Remove __pycache__, .pyc, caches"
	@echo "  help          Show this help"
