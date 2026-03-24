# OpenClaw Helper Aliases
# Add this to your ~/.zshrc: source ~/openclaw/scripts/aliases.sh

alias oc-up="docker compose up -d --build"
alias oc-down="docker compose down"
alias oc-logs="docker compose logs -f openclaw"
alias oc-restart="docker compose restart openclaw"
alias oc-shell="docker exec -it openclaw /bin/bash"
alias oc-health="curl http://localhost:8765/health"
alias oc-vpn-status="launchctl list | grep proton"
