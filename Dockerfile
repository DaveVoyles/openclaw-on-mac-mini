FROM python:3.12-slim

LABEL maintainer="davevoyles"
LABEL description="OpenClaw - Autonomous AI agent with Discord interface"
LABEL version="0.5.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    lsb-release \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./
COPY skills/ ./skills/

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')" || exit 1

RUN useradd -u 501 -m openclaw
# Ensure we can talk to the Docker socket by being in the correct group
# Debian typically uses GID 999 for docker, but on macOS/Docker Desktop, the socket is often user-owned.
RUN groupadd -g 999 docker && usermod -aG docker openclaw
USER openclaw

EXPOSE 8765

CMD ["python", "bot.py"]
