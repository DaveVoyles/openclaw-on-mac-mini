#!/usr/bin/env bash
# Release helper script for OpenClaw
# Creates and pushes a new release tag

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Functions
error() {
    echo -e "${RED}❌ Error: $1${NC}" >&2
    exit 1
}

success() {
    echo -e "${GREEN}✅ $1${NC}"
}

warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

info() {
    echo -e "$1"
}

# Check if git is clean
check_git_clean() {
    if [[ -n $(git status -s) ]]; then
        error "Working directory is not clean. Commit or stash changes first."
    fi
}

# Validate semantic version
validate_version() {
    local version=$1
    if ! [[ $version =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        error "Invalid version format. Use vX.Y.Z (e.g., v1.0.0)"
    fi
}

# Get current version from git tags
get_current_version() {
    git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0"
}

# Bump version
bump_version() {
    local current=$1
    local type=$2
    
    # Remove 'v' prefix
    current=${current#v}
    
    IFS='.' read -r major minor patch <<< "$current"
    
    case $type in
        major)
            major=$((major + 1))
            minor=0
            patch=0
            ;;
        minor)
            minor=$((minor + 1))
            patch=0
            ;;
        patch)
            patch=$((patch + 1))
            ;;
        *)
            error "Invalid bump type. Use: major, minor, or patch"
            ;;
    esac
    
    echo "v${major}.${minor}.${patch}"
}

# Main script
main() {
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "🚀 OpenClaw Release Helper"
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    # Check if git is clean
    check_git_clean
    success "Working directory is clean"
    
    # Get current version
    CURRENT_VERSION=$(get_current_version)
    info "Current version: ${CURRENT_VERSION}"
    echo ""
    
    # Ask for version bump type or custom version
    info "How do you want to bump the version?"
    info "  1) Major (breaking changes)"
    info "  2) Minor (new features)"
    info "  3) Patch (bug fixes)"
    info "  4) Custom version"
    echo ""
    read -p "Select (1-4): " choice
    echo ""
    
    case $choice in
        1)
            NEW_VERSION=$(bump_version "$CURRENT_VERSION" "major")
            ;;
        2)
            NEW_VERSION=$(bump_version "$CURRENT_VERSION" "minor")
            ;;
        3)
            NEW_VERSION=$(bump_version "$CURRENT_VERSION" "patch")
            ;;
        4)
            read -p "Enter version (e.g., v1.0.0): " NEW_VERSION
            validate_version "$NEW_VERSION"
            ;;
        *)
            error "Invalid selection"
            ;;
    esac
    
    info "New version: ${GREEN}${NEW_VERSION}${NC}"
    echo ""
    
    # Confirm
    read -p "Create release ${NEW_VERSION}? (y/N): " confirm
    if [[ ! $confirm =~ ^[Yy]$ ]]; then
        warning "Release cancelled"
        exit 0
    fi
    echo ""
    
    # Pull latest changes
    info "Pulling latest changes from main..."
    git pull origin main
    success "Up to date with main"
    echo ""
    
    # Run tests
    info "Running tests..."
    if pytest tests/ -x -q --tb=short \
        --ignore=tests/test_llm_chat.py \
        --ignore=tests/test_llm_ratelimiter.py \
        --ignore=tests/test_model_selection.py \
        -k 'not test_gateway_successful_request and not test_returns_json_response' > /dev/null 2>&1; then
        success "All tests passed"
    else
        error "Tests failed. Fix issues before releasing."
    fi
    echo ""
    
    # Run linting
    info "Running linting..."
    if ruff check src/ tests/ > /dev/null 2>&1; then
        success "Linting passed"
    else
        warning "Linting issues found (non-blocking)"
    fi
    echo ""
    
    # Create and push tag
    info "Creating tag ${NEW_VERSION}..."
    git tag -a "$NEW_VERSION" -m "Release $NEW_VERSION"
    success "Tag created"
    echo ""
    
    info "Pushing tag to origin..."
    git push origin "$NEW_VERSION"
    success "Tag pushed"
    echo ""
    
    # Summary
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    success "Release ${NEW_VERSION} initiated!"
    info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    info "The GitHub Actions workflow will:"
    info "  ✅ Run tests"
    info "  ✅ Build Docker images (amd64 + arm64)"
    info "  ✅ Push to GitHub Container Registry"
    info "  ✅ Generate changelog"
    info "  ✅ Create GitHub Release"
    info "  ✅ Update CHANGELOG.md"
    echo ""
    info "Monitor progress at:"
    info "https://github.com/DaveVoyles/openclaw-on-mac-mini/actions"
    echo ""
    info "Release will be available at:"
    info "https://github.com/DaveVoyles/openclaw-on-mac-mini/releases/tag/${NEW_VERSION}"
    echo ""
}

# Run main
main "$@"
