FROM python:3.12-slim AS base

LABEL maintainer="davevoyles"
LABEL description="OpenClaw - Autonomous AI agent with Discord interface"
LABEL version="0.6.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps + Docker CLI in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    lsb-release \
    build-essential \
    fonts-dejavu-core \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps (cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser for JS-rendered page scraping
RUN playwright install chromium --with-deps

# Copy application code (separate layer — fast rebuilds on code changes)
COPY src/ ./
COPY skills/ ./skills/
COPY templates/ ./templates/

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')" || exit 1

RUN useradd -u 501 -m openclaw
# Ensure we can talk to the Docker socket by being in the correct group
# Debian typically uses GID 999 for docker, but on macOS/Docker Desktop, the socket is often user-owned.
RUN groupadd -g 999 docker && usermod -aG docker openclaw
USER openclaw

EXPOSE 8765

CMD ["python", "bot.py"]
