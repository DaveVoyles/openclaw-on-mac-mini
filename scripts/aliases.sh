# OpenClaw Helper Aliases
# Add this to your ~/.zshrc: source ~/openclaw/scripts/aliases.sh

: "${OPENCLAW_HOME:=$HOME/openclaw}"

# zsh refuses to define a function over an existing alias with the same name.
unalias OpenClaw openclaw oc-up oc-down oc-logs oc-restart oc-shell oc-health oc-dash oc-ask oc-chat 2>/dev/null || true

openclaw() {
  python3 "$OPENCLAW_HOME/scripts/openclaw_cli.py" "$@"
}

OpenClaw() {
  openclaw "$@"
}

oc-up() {
  (
    cd "$OPENCLAW_HOME" &&
      docker compose up -d --build &&
      docker image prune -f
  )
}

oc-down() {
  (cd "$OPENCLAW_HOME" && docker compose down)
}

oc-logs() {
  (cd "$OPENCLAW_HOME" && docker compose logs -f openclaw)
}

oc-restart() {
  (
    cd "$OPENCLAW_HOME" &&
      docker compose up -d --build
  )
}

oc-shell() {
  docker exec -it openclaw /bin/bash
}

oc-health() {
  curl -fsS "${OPENCLAW_URL:-http://localhost:8765}/health" | python3 -m json.tool
}

oc-dash() {
  open "${OPENCLAW_URL:-http://localhost:8765}/dashboard"
}

oc-ask() {
  openclaw ask "$@"
}

oc-chat() {
  openclaw chat "$@"
}

alias oc-vpn-status="launchctl list | grep proton"
