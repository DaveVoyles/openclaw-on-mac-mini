#!/bin/bash
# OpenClaw health check script
# Usage: ./scripts/health-check.sh

set -euo pipefail

HEALTH_URL="http://localhost:8765/health"
TIMEOUT=5

echo "🔍 OpenClaw Health Check"
echo "========================"

# Check container is running
if docker ps --filter "name=openclaw" --format "{{.Names}}" | grep -q openclaw; then
    echo "✅ Container: running"
    docker ps --filter "name=openclaw" --format "   Status: {{.Status}}"
else
    echo "❌ Container: not running"
    exit 1
fi

# Check health endpoint
if curl -sf --max-time "$TIMEOUT" "$HEALTH_URL" > /dev/null 2>&1; then
    echo "✅ Health endpoint: responding"
    curl -sf "$HEALTH_URL" | python3 -m json.tool 2>/dev/null || true
else
    echo "❌ Health endpoint: not responding"
    echo "   Checking logs..."
    docker logs openclaw --tail 10
    exit 1
fi

echo ""
echo "✅ All checks passed"
