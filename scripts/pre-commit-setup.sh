#!/usr/bin/env bash
# Pre-commit hooks setup script for OpenClaw
# This script installs and configures pre-commit hooks for development

set -e

echo "🔧 Setting up pre-commit hooks for OpenClaw..."
echo ""

# Check if pre-commit is installed
if ! command -v pre-commit &> /dev/null; then
    echo "📦 Installing pre-commit..."
    pip install pre-commit
else
    echo "✅ pre-commit is already installed"
fi

# Install pre-commit hooks
echo "📌 Installing pre-commit hooks..."
pre-commit install

# Install commit-msg hook for conventional commits
echo "📝 Installing commit-msg hook for conventional commits..."
pre-commit install --hook-type commit-msg

# Install pre-push hook for running tests
echo "🧪 Installing pre-push hook for tests..."
pre-commit install --hook-type pre-push

# Run pre-commit on all files to check setup
echo ""
echo "🔍 Running pre-commit checks on all files (this may take a moment)..."
echo ""

if pre-commit run --all-files; then
    echo ""
    echo "✅ All pre-commit checks passed!"
else
    echo ""
    echo "⚠️  Some pre-commit checks failed. Files have been auto-fixed where possible."
    echo "   Please review the changes and commit them."
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✨ Pre-commit hooks setup complete!"
echo ""
echo "Configured hooks:"
echo "  • ruff (linting + formatting)"
echo "  • mypy (type checking)"
echo "  • bandit (security scanning)"
echo "  • pytest (tests on pre-push)"
echo "  • conventional commits checker"
echo "  • standard file checks (trailing whitespace, etc.)"
echo ""
echo "Usage:"
echo "  • Hooks run automatically on git commit/push"
echo "  • Run manually: pre-commit run --all-files"
echo "  • Skip hooks: git commit --no-verify"
echo "  • Update hooks: pre-commit autoupdate"
echo ""
echo "Commit message format (enforced):"
echo "  feat: add new feature"
echo "  fix: fix a bug"
echo "  docs: update documentation"
echo "  test: add tests"
echo "  refactor: refactor code"
echo "  chore: maintenance tasks"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
