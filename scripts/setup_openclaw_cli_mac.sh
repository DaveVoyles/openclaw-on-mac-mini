#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_HOME_DEFAULT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_HOME="${HOME}"
TARGET_RC_FILE=""
TARGET_SHELL="${OPENCLAW_SHELL:-}"
OPENCLAW_URL_VALUE="${OPENCLAW_URL:-http://192.168.1.93:8765}"
SKIP_TOKEN_PROMPT=0

usage() {
  cat <<'EOF'
Usage: setup_openclaw_cli_mac.sh [--url URL] [--home DIR] [--shell SHELL] [--rc-file FILE] [--zshrc FILE] [--skip-token-prompt]

Configures the current Mac shell for OpenClaw CLI usage by:
1. exporting OPENCLAW_HOME
2. exporting OPENCLAW_URL
3. sourcing scripts/aliases.sh from the OpenClaw repo
4. exposing OpenClaw/openclaw plus oc-* compatibility helpers
5. optionally storing OPENCLAW_TOKEN in macOS Keychain
EOF
}

detect_shell_name() {
  local shell_hint="${TARGET_SHELL:-${SHELL:-zsh}}"
  shell_hint="$(basename "$shell_hint")"
  case "$shell_hint" in
    bash|zsh)
      printf '%s\n' "$shell_hint"
      ;;
    *)
      printf 'zsh\n'
      ;;
  esac
}

default_rc_file_for_shell() {
  local shell_name="$1"
  case "$shell_name" in
    bash)
      printf '%s/.bashrc\n' "$TARGET_HOME"
      ;;
    *)
      printf '%s/.zshrc\n' "$TARGET_HOME"
      ;;
  esac
}

append_line_once() {
  local file="$1"
  local line="$2"
  touch "$file"
  if ! grep -Fqx "$line" "$file"; then
    printf '%s\n' "$line" >>"$file"
  fi
}

prompt_for_token() {
  local token_input=""
  if [[ -r /dev/tty ]]; then
    printf 'OpenClaw API token (leave blank to skip Keychain setup): ' >/dev/tty
    IFS= read -r -s token_input </dev/tty || token_input=""
    printf '\n' >/dev/tty
  else
    echo "Skipping Keychain token setup (/dev/tty unavailable)." >&2
    return 0
  fi
  if [[ -n "$token_input" ]]; then
    security add-generic-password -U -s "OpenClaw CLI" -a "${USER}" -w "${token_input}" >/dev/null
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      OPENCLAW_URL_VALUE="$2"
      shift 2
      ;;
    --home)
      TARGET_HOME="$2"
      shift 2
      ;;
    --shell)
      TARGET_SHELL="$2"
      shift 2
      ;;
    --rc-file|--zshrc)
      TARGET_RC_FILE="$2"
      shift 2
      ;;
    --skip-token-prompt)
      SKIP_TOKEN_PROMPT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

TARGET_SHELL="$(detect_shell_name)"
if [[ -z "$TARGET_RC_FILE" ]]; then
  TARGET_RC_FILE="$(default_rc_file_for_shell "$TARGET_SHELL")"
fi

mkdir -p "$(dirname "$TARGET_RC_FILE")"

append_line_once "$TARGET_RC_FILE" "export OPENCLAW_HOME=\"$OPENCLAW_HOME_DEFAULT\""
append_line_once "$TARGET_RC_FILE" "export OPENCLAW_URL=\"$OPENCLAW_URL_VALUE\""
append_line_once "$TARGET_RC_FILE" "source \"$OPENCLAW_HOME_DEFAULT/scripts/aliases.sh\""

if [[ "$SKIP_TOKEN_PROMPT" -eq 0 ]] && command -v security >/dev/null 2>&1; then
  if ! security find-generic-password -s "OpenClaw CLI" -a "${USER}" >/dev/null 2>&1; then
    prompt_for_token
  fi
fi

cat <<EOF
OpenClaw CLI shell setup written to:
  $TARGET_RC_FILE

Configured values:
  OPENCLAW_HOME=$OPENCLAW_HOME_DEFAULT
  OPENCLAW_URL=$OPENCLAW_URL_VALUE
  TARGET_SHELL=$TARGET_SHELL

Next step:
  source "$TARGET_RC_FILE"
  OpenClaw
  openclaw "what changed overnight?"
EOF
