#!/usr/bin/env bash
# Run OpenClaw test suite inside a Docker container using Python 3.12.
# This matches the production environment defined in the Dockerfile.
#
# Usage:
#   ./run_tests.sh              # run all tests
#   ./run_tests.sh -k memory    # filter by keyword
#   ./run_tests.sh tests/test_memory.py  # run one file

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🧪 OpenClaw Test Suite (Python 3.12 via Docker)"
echo ""

# Build the env-file argument only if .env exists
ENV_ARGS=()
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
  ENV_ARGS=(--env-file "${SCRIPT_DIR}/.env")
fi

docker run --rm \
  "${ENV_ARGS[@]}" \
  -v "$SCRIPT_DIR":/app \
  -w /app \
  python:3.12-slim \
  bash -c "
    set -e
    pip install -q -r requirements.txt -r requirements-test.txt
    python -m pytest tests/ ${*}
  "
