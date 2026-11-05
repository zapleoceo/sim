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

# Dependencies first (cached until pyproject/uv.lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY sim ./sim
RUN uv sync --frozen --no-dev

# Non-root runtime user. /app/static is where the built Angular app is mounted;
# /app/media holds downloaded voice/images.
RUN useradd --system --uid 10001 --home-dir /app sim \
    && mkdir -p /app/static /app/media \
    && chown -R sim:sim /app/media /app/static
USER sim

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "sim.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
