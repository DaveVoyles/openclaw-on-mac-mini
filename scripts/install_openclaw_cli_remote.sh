#!/usr/bin/env bash
# Install the OpenClaw standalone CLI to a remote Mac via SSH + SCP.
# Usage (run from Mac Mini):
#   bash scripts/install_openclaw_cli_remote.sh [user@]host [openclaw-url]
#
# The remote Mac does NOT need the git repo — all files are pushed from here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TARGET_HOST="${1:-macbook}"
OPENCLAW_URL="${2:-http://192.168.1.93:8765}"

SRC_DIR="$REPO_ROOT/src"
INSTALL_DIR="\$HOME/.local/share/openclaw-cli"
BIN_DIR="\$HOME/.local/bin"

CLI_FILES=()
while IFS= read -r f; do
  CLI_FILES+=("$f")
done < <(
  find "$SRC_DIR" -maxdepth 1 -type f \
    \( -name 'openclaw_cli*.py' -o -name 'subprocess_utils.py' \) \
    -exec basename {} \; | sort
)

echo "Installing OpenClaw CLI to ${TARGET_HOST}…"
echo "  server: ${OPENCLAW_URL}"

# Create install dir on target
ssh "$TARGET_HOST" "mkdir -p ~/.local/share/openclaw-cli ~/.local/bin"

# Copy CLI source files
for f in "${CLI_FILES[@]}"; do
  scp -q "$SRC_DIR/$f" "${TARGET_HOST}:~/.local/share/openclaw-cli/$f"
done
echo "  ✓ copied CLI source files"

# Build aliases + wrapper remotely
ssh "$TARGET_HOST" bash -s <<REMOTE
set -euo pipefail

INSTALL_DIR="\$HOME/.local/share/openclaw-cli"
BIN_DIR="\$HOME/.local/bin"
RC_FILE="\$HOME/.zshrc"
OPENCLAW_URL="${OPENCLAW_URL}"

# Write openclaw_aliases.sh
cat > "\$INSTALL_DIR/openclaw_aliases.sh" <<'ALIASES'
unalias OpenClaw openclaw oc-health oc-dash oc-ask oc-chat 2>/dev/null || true

openclaw() {
  "\$HOME/.local/bin/openclaw" "\$@"
}

OpenClaw() {
  openclaw "\$@"
}

oc-health() {
  curl -fsS "\${OPENCLAW_URL:-http://192.168.1.93:8765}/health" | python3 -m json.tool
}

oc-dash() {
  open "\${OPENCLAW_URL:-http://192.168.1.93:8765}/dashboard"
}

oc-ask() {
  openclaw ask "\$@"
}

oc-chat() {
  openclaw chat "\$@"
}
ALIASES

# Write the openclaw wrapper (bakes URL so it works in non-interactive SSH sessions)
printf '#!/usr/bin/env bash\nexport OPENCLAW_URL="%s"\nexec python3 "%s/.local/share/openclaw-cli/openclaw_cli.py" "\$@"\n' \
  "$OPENCLAW_URL" "$HOME" > "$BIN_DIR/openclaw"
chmod +x "\$BIN_DIR/openclaw"

# Create openclaw-cli symlink
ln -sf "\$BIN_DIR/openclaw" "\$BIN_DIR/openclaw-cli"

# Add .zshrc entries (idempotent)
touch "\$RC_FILE"
ALIASES_LINE="source \"\$INSTALL_DIR/openclaw_aliases.sh\""
URL_LINE="export OPENCLAW_URL=\"\$OPENCLAW_URL\""

grep -Fqx "\$URL_LINE" "\$RC_FILE" 2>/dev/null || printf '%s\n' "\$URL_LINE" >> "\$RC_FILE"
grep -Fqx "\$ALIASES_LINE" "\$RC_FILE" 2>/dev/null || printf '%s\n' "\$ALIASES_LINE" >> "\$RC_FILE"

echo "  ✓ wrapper and aliases installed"
echo "  ✓ .zshrc updated"
echo ""
echo "OpenClaw CLI installed. Open a new terminal or run:"
echo "  source ~/.zshrc"
echo "  openclaw"
REMOTE
