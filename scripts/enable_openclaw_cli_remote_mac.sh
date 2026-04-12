#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_HOME_DEFAULT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_HOME="${HOME}"
TARGET_RC_FILE=""
TARGET_SHELL="${OPENCLAW_SHELL:-}"
OPENCLAW_URL_VALUE="${OPENCLAW_URL:-http://192.168.1.93:8765}"
ALLOW_USER="${USER}"
SKIP_REMOTE_LOGIN_GROUP=0
SKIP_TOKEN_PROMPT=0
MAC_MINI_PUBLIC_KEY="${OPENCLAW_MAC_MINI_PUBLIC_KEY:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGoHSUm8PsFsETafvwQVYKD8DCXo4wRWHw24yPpGC40s davevoyles@mac-mini}"

usage() {
  cat <<'EOF'
Usage: enable_openclaw_cli_remote_mac.sh [--url URL] [--home DIR] [--shell SHELL] [--rc-file FILE] [--zshrc FILE] [--allow-user USER] [--skip-remote-login-group] [--skip-token-prompt]

For the local Mac you are running on, this script:
1. Ensures Remote Login is enabled
2. Authorizes the Mac Mini's SSH public key in ~/.ssh/authorized_keys
3. Optionally adds the allowed user to com.apple.access_ssh
4. Bootstraps the OpenClaw CLI shell setup via setup_openclaw_cli_mac.sh
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

append_pubkey_once() {
  local key_file="$1"
  local pubkey="$2"
  touch "$key_file"
  if ! grep -Fqx "$pubkey" "$key_file"; then
    printf '%s\n' "$pubkey" >>"$key_file"
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
    --allow-user)
      ALLOW_USER="$2"
      shift 2
      ;;
    --skip-remote-login-group)
      SKIP_REMOTE_LOGIN_GROUP=1
      shift
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

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script is intended for macOS only." >&2
  exit 1
fi

mkdir -p "$TARGET_HOME/.ssh"
chmod 700 "$TARGET_HOME/.ssh"
append_pubkey_once "$TARGET_HOME/.ssh/authorized_keys" "$MAC_MINI_PUBLIC_KEY"
chmod 600 "$TARGET_HOME/.ssh/authorized_keys"

if [[ "$SKIP_REMOTE_LOGIN_GROUP" -eq 0 ]]; then
  remote_login_state="$(sudo systemsetup -getremotelogin 2>/dev/null || true)"
  if [[ "$remote_login_state" != *"On"* ]]; then
    sudo systemsetup -setremotelogin on
  fi
  sudo dseditgroup -o edit -a "$ALLOW_USER" -t user com.apple.access_ssh
fi

bash "$SCRIPT_DIR/setup_openclaw_cli_mac.sh" \
  --url "$OPENCLAW_URL_VALUE" \
  --home "$TARGET_HOME" \
  --shell "$TARGET_SHELL" \
  --rc-file "$TARGET_RC_FILE" \
  $([[ "$SKIP_TOKEN_PROMPT" -eq 1 ]] && printf '%s' '--skip-token-prompt')

cat <<EOF
Remote Mac OpenClaw setup complete for:
  user: $ALLOW_USER
  home: $TARGET_HOME
  url:  $OPENCLAW_URL_VALUE

You should now be able to:
  source "$TARGET_RC_FILE"
  OpenClaw
  openclaw "what changed overnight?"
EOF
