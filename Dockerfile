# ============================================================================
# STAGE 1: Builder - Install dependencies and compile native extensions
# ============================================================================
FROM python:3.12-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    gnupg \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Python dependencies in a virtual environment
COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (minimal - chromium only, no fonts)
RUN /opt/venv/bin/playwright install chromium

# ============================================================================
# STAGE 2: Runtime - Minimal production image
# ============================================================================
FROM python:3.12-slim AS runtime

LABEL maintainer="davevoyles"
LABEL description="OpenClaw - Autonomous AI agent with Discord interface"
LABEL version="0.6.0"

# Production environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app" \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    lsb-release \
    fonts-dejavu-core \
    openssh-client \
    ca-certificates \
    # Playwright runtime dependencies (minimal set)
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy Python virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy Playwright browsers from builder
COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright

# Copy application code
COPY src/ ./src/
COPY skills/ ./skills/
COPY templates/ ./templates/

# Create non-root user
RUN useradd -u 501 -m -s /bin/bash openclaw && \
    groupadd -g 999 docker && \
    usermod -aG docker openclaw && \
    chown -R openclaw:openclaw /app && \
    # Create cache directory for Playwright under openclaw user
    mkdir -p /home/openclaw/.cache && \
    cp -r /root/.cache/ms-playwright /home/openclaw/.cache/ && \
    chown -R openclaw:openclaw /home/openclaw/.cache

USER openclaw

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:8765/health || exit 1

EXPOSE 8765

CMD ["python", "src/bot.py"]
