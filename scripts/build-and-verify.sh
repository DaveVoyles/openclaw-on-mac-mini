#!/usr/bin/env bash
# OpenClaw Build & Verify
# Rebuilds the container and runs post-deploy smoke tests.
#
# Usage:
#   bash scripts/build-and-verify.sh          # full build + all tests
#   bash scripts/build-and-verify.sh --quick  # full build + infrastructure tests only

set -e
cd "$(dirname "$0")/.."

echo "🔨 Building and deploying OpenClaw..."
docker compose up -d --build

echo ""
echo "⏳ Waiting 15s for bot to start and sync commands..."
sleep 15

echo ""
python3 scripts/post_deploy_test.py "$@"
