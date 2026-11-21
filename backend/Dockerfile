# One image for every SIM v2 process (infra/docker-compose.yml picks the command):
#   api       uvicorn sim.apps.api:create_app --factory ... (default CMD)
#   worker    python -m sim.apps.worker
#   telegram  python -m sim.apps.telegram
#   migrate   alembic upgrade heads && python -m sim.seed
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    SIM_STATIC_DIR=/app/static \
    SIM_MEDIA_DIR=/app/media

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app

# ffmpeg: Broker TTS answers in WAV, Telegram voice notes are OGG/Opus; frames from videos;
# PCM for calls.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (cached until pyproject/uv.lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY sim ./sim
RUN uv sync --frozen --no-dev

# Non-root runtime user. /app/static: the built admin (read-only bind mount);
# /app/media: the shared media volume (library, generated and incoming files).
RUN useradd --system --uid 10001 --home-dir /app sim \
    && mkdir -p /app/static /app/media \
    && chown -R sim:sim /app/media /app/static
USER sim

EXPOSE 8000
# Readiness of the api (database reachable). worker/telegram serve no HTTP: compose disables it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=4)"]

CMD ["uvicorn", "sim.apps.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop"]
