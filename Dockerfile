# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy
ENV TZ=America/Lima

WORKDIR /app

# Usuario no privilegiado (buenas prácticas)
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser

# Instalar uv (gestor de paquetes rápido)
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# Instalar dependencias (cacheado si pyproject.toml/uv.lock no cambian)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Instalar paquete (solo código, deps ya instaladas)
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Crear directorio de logs con permisos para appuser
RUN mkdir -p /app/logs && chown appuser:appuser /app/logs

USER appuser

EXPOSE 8002

HEALTHCHECK --interval=300s --timeout=5s --start-period=10s --retries=2 \
    CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')" || exit 1

CMD [".venv/bin/python", "-m", "autobot.main"]
