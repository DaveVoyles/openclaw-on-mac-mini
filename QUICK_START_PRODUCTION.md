# OpenClaw Production Quick Start

## 🚀 Deploy with Docker

### Production Deployment

```bash
# Clone repository
git clone https://github.com/DaveVoyles/openclaw-on-mac-mini.git
cd openclaw-on-mac-mini

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Deploy with production compose
docker-compose -f docker-compose.prod.yml up -d

# Check status
docker-compose -f docker-compose.prod.yml ps
docker logs openclaw-prod -f
```

### Using Pre-Built Images

```bash
# Pull from GitHub Container Registry
docker pull ghcr.io/davevoyles/openclaw-on-mac-mini:latest

# Run with custom configuration
docker run -d \
  --name openclaw \
  --env-file .env \
  -p 8765:8765 \
  -v $(pwd)/data:/app/data \
  ghcr.io/davevoyles/openclaw-on-mac-mini:latest
```

## 🛠️ Development Setup

```bash
# Setup development environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-test.txt

# Install pre-commit hooks
./scripts/pre-commit-setup.sh

# Run locally
docker-compose up -d
```

## 📦 Creating a Release

```bash
# Interactive release
./scripts/release.sh

# Manual release
git tag v1.0.0
git push origin v1.0.0
```

## 🔒 Security

```bash
# Run security scans locally
docker run --rm -v $(pwd):/scan aquasec/trivy fs /scan

# Check dependencies
pip install safety
safety check
```

## 📚 Documentation

- **Main Docs:** https://davevoyles.github.io/openclaw-on-mac-mini/
- **Commands:** [COMMANDS.md](docs/COMMANDS.md)
- **Architecture:** [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **API Reference:** [API_REFERENCE.md](docs/API_REFERENCE.md)

## 🔧 Useful Commands

```bash
# View logs
docker-compose -f docker-compose.prod.yml logs -f

# Restart service
docker-compose -f docker-compose.prod.yml restart

# Stop service
docker-compose -f docker-compose.prod.yml down

# Rebuild image
docker-compose -f docker-compose.prod.yml build --no-cache

# Run tests
pytest tests/ -v

# Lint code
ruff check src/ tests/

# Format code
ruff format src/ tests/
```

## 🌐 URLs

- Repository: https://github.com/DaveVoyles/openclaw-on-mac-mini
- Documentation: https://davevoyles.github.io/openclaw-on-mac-mini/
- Container Registry: ghcr.io/davevoyles/openclaw-on-mac-mini
- Security: https://github.com/DaveVoyles/openclaw-on-mac-mini/security

## 📞 Support

- Issues: https://github.com/DaveVoyles/openclaw-on-mac-mini/issues
- Discussions: https://github.com/DaveVoyles/openclaw-on-mac-mini/discussions
