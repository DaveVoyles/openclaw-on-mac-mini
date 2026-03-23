FROM python:3.12-slim

LABEL maintainer="davevoyles"
LABEL description="OpenClaw - Autonomous AI agent with Discord interface"
LABEL version="0.5.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')" || exit 1

RUN useradd -u 501 -m openclaw
USER openclaw

EXPOSE 8765

CMD ["python", "bot.py"]
