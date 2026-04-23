#!/bin/bash
# Reset OpenClaw Dashboard Authentication
# 
# Purpose: Disable or reset dashboard authentication if credentials are forgotten
# Usage: 
#   ./reset-dashboard-auth.sh disable   # Disable authentication entirely
#   ./reset-dashboard-auth.sh reset     # Generate new random credentials
#   ./reset-dashboard-auth.sh status    # Show current auth status
#
# This script is designed for agent access to recover from forgotten credentials.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

show_status() {
    print_header "Dashboard Authentication Status"
    
    if [ ! -f "$ENV_FILE" ]; then
        print_error "No .env file found at $ENV_FILE"
        return 1
    fi
    
    USERNAME=$(grep "^OPENCLAW_DASHBOARD_USERNAME=" "$ENV_FILE" | cut -d'=' -f2 || echo "")
    PASSWORD=$(grep "^OPENCLAW_DASHBOARD_PASSWORD=" "$ENV_FILE" | cut -d'=' -f2 || echo "")
    
    if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
        print_warning "Authentication is DISABLED (empty credentials)"
        echo "  Username: $([[ -z $USERNAME ]] && echo '(not set)' || echo "$USERNAME")"
        echo "  Password: $([[ -z $PASSWORD ]] && echo '(not set)' || echo 'SET')"
    else
        print_success "Authentication is ENABLED"
        echo "  Username: $USERNAME"
        echo "  Password: ${PASSWORD:0:10}... (hidden)"
    fi
    echo
}

disable_auth() {
    print_header "Disabling Dashboard Authentication"
    
    if [ ! -f "$ENV_FILE" ]; then
        print_error "No .env file found at $ENV_FILE"
        return 1
    fi
    
    # Clear credentials in .env
    if grep -q "^OPENCLAW_DASHBOARD_USERNAME=" "$ENV_FILE"; then
        sed -i '' 's/^OPENCLAW_DASHBOARD_USERNAME=.*/OPENCLAW_DASHBOARD_USERNAME=/' "$ENV_FILE"
        print_success "Cleared OPENCLAW_DASHBOARD_USERNAME"
    fi
    
    if grep -q "^OPENCLAW_DASHBOARD_PASSWORD=" "$ENV_FILE"; then
        sed -i '' 's/^OPENCLAW_DASHBOARD_PASSWORD=.*/OPENCLAW_DASHBOARD_PASSWORD=/' "$ENV_FILE"
        print_success "Cleared OPENCLAW_DASHBOARD_PASSWORD"
    fi
    
    echo
    print_warning "Authentication has been DISABLED"
    echo "  Next container restart will allow unauthenticated dashboard access"
    echo
    echo "To re-enable, run: $0 reset"
    echo
}

reset_auth() {
    print_header "Resetting Dashboard Authentication"
    
    if [ ! -f "$ENV_FILE" ]; then
        print_error "No .env file found at $ENV_FILE"
        return 1
    fi
    
    # Generate new credentials
    NEW_USERNAME="davevoyles"
    NEW_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(20))")
    
    # Update .env file
    if grep -q "^OPENCLAW_DASHBOARD_USERNAME=" "$ENV_FILE"; then
        sed -i '' "s/^OPENCLAW_DASHBOARD_USERNAME=.*/OPENCLAW_DASHBOARD_USERNAME=$NEW_USERNAME/" "$ENV_FILE"
    else
        echo "OPENCLAW_DASHBOARD_USERNAME=$NEW_USERNAME" >> "$ENV_FILE"
    fi
    
    if grep -q "^OPENCLAW_DASHBOARD_PASSWORD=" "$ENV_FILE"; then
        sed -i '' "s/^OPENCLAW_DASHBOARD_PASSWORD=.*/OPENCLAW_DASHBOARD_PASSWORD=$NEW_PASSWORD/" "$ENV_FILE"
    else
        echo "OPENCLAW_DASHBOARD_PASSWORD=$NEW_PASSWORD" >> "$ENV_FILE"
    fi
    
    print_success "New credentials generated"
    echo
    echo -e "${YELLOW}📋 Save these credentials somewhere safe:${NC}"
    echo "  Username: $NEW_USERNAME"
    echo "  Password: $NEW_PASSWORD"
    echo
    echo "These credentials are now active in .env"
    echo "Next container restart will use these credentials"
    echo
}

main() {
    local action="${1:-status}"
    
    case "$action" in
        disable)
            disable_auth
            ;;
        reset)
            reset_auth
            ;;
        status)
            show_status
            ;;
        *)
            print_error "Unknown action: $action"
            echo
            echo "Usage: $0 {disable|reset|status}"
            echo
            echo "Actions:"
            echo "  disable  - Disable dashboard authentication (allow unauthenticated access)"
            echo "  reset    - Generate and set new admin credentials"
            echo "  status   - Show current authentication status"
            echo
            exit 1
            ;;
    esac
}

main "$@"
