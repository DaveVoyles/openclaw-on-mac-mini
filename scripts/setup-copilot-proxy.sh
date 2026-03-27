#!/usr/bin/env bash
# OpenClaw Copilot Proxy Setup Script
# Authenticates with GitHub Copilot and starts the proxy server.
#
# Usage: bash scripts/setup-copilot-proxy.sh
#
# Prerequisites:
# - Docker installed and running
# - GitHub Copilot subscription (Pro, Business, or Enterprise)

set -e

echo "🔐 Starting GitHub Copilot OAuth device flow..."
echo ""

# Step 1: Initiate device flow
RESPONSE=$(curl -s -X POST https://github.com/login/device/code \
  -H "Content-Type: application/json" \
  -d '{"client_id": "Iv1.b507a08c87ecfe98", "scope": "copilot"}')

USER_CODE=$(echo "$RESPONSE" | grep -o 'user_code=[^&]*' | cut -d= -f2)
DEVICE_CODE=$(echo "$RESPONSE" | grep -o 'device_code=[^&]*' | cut -d= -f2)

if [ -z "$USER_CODE" ] || [ -z "$DEVICE_CODE" ]; then
  echo "❌ Failed to initiate device flow. Response: $RESPONSE"
  exit 1
fi

echo "📋 Go to: https://github.com/login/device"
echo "📋 Enter code: $USER_CODE"
echo ""
echo "Waiting for authorization..."

# Step 2: Poll for token
for i in $(seq 1 60); do
  sleep 5
  RESULT=$(curl -s -X POST https://github.com/login/oauth/access_token \
    -H "Content-Type: application/json" \
    -d "{\"client_id\": \"Iv1.b507a08c87ecfe98\", \"device_code\": \"${DEVICE_CODE}\", \"grant_type\": \"urn:ietf:params:oauth:grant-type:device_code\"}")

  if echo "$RESULT" | grep -q "access_token=ghu_"; then
    TOKEN=$(echo "$RESULT" | grep -o 'access_token=[^&]*' | cut -d= -f2)
    echo ""
    echo "✅ Got Copilot OAuth token!"

    # Save token
    mkdir -p ~/.config/github-copilot
    cat > ~/.config/github-copilot/hosts.json << EOF
{
  "github.com": {
    "user": "$(gh api user -q .login 2>/dev/null || echo 'user')",
    "oauth_token": "${TOKEN}"
  }
}
EOF
    chmod 600 ~/.config/github-copilot/hosts.json
    echo "✅ Saved token to ~/.config/github-copilot/hosts.json"
    break
  fi

  if echo "$RESULT" | grep -q "expired_token"; then
    echo "❌ Authorization expired. Please run this script again."
    exit 1
  fi

  printf "."
done

if [ -z "$TOKEN" ]; then
  echo ""
  echo "❌ Timed out waiting for authorization."
  exit 1
fi

# Step 3: Build and start Docker container
echo ""
echo "🐳 Building Copilot proxy Docker image..."

PROXY_DIR="/tmp/copilot-openai-api"
if [ ! -d "$PROXY_DIR" ]; then
  git clone https://github.com/yuchanns/copilot-openai-api.git "$PROXY_DIR"
fi

cd "$PROXY_DIR"
docker build -t copilot-openai-api . -q

# Stop existing container if running
docker rm -f copilot-proxy 2>/dev/null || true

echo "🚀 Starting Copilot proxy on port 9191..."
docker run -d \
  --name copilot-proxy \
  --restart unless-stopped \
  -p 9191:9191 \
  -v ~/.config/github-copilot:/root/.config/github-copilot:ro \
  -e COPILOT_SERVER_PORT=9191 \
  copilot-openai-api

sleep 3

# Step 4: Test the proxy
echo ""
if curl -s http://localhost:9191/v1/models > /dev/null 2>&1; then
  echo "✅ Copilot proxy is running at http://localhost:9191/v1"
  echo ""
  echo "Add this to your OpenClaw .env file:"
  echo "  COPILOT_PROXY_URL=http://host.docker.internal:9191/v1"
  echo ""
  echo "Then restart OpenClaw to enable multi-model routing through Copilot!"
else
  echo "⚠️  Proxy started but may need a moment. Check: docker logs copilot-proxy"
fi
