FROM python:3.13.13-alpine3.23 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN apk add --no-cache --virtual .deps gcc musl-dev postgresql-dev openssl-dev libffi-dev g++ \
    && uv sync --frozen --no-dev \
    && apk del .deps


FROM python:3.13.13-alpine3.23

ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache libpq

WORKDIR /app

COPY --from=builder /app/.venv ./.venv

USER 999

COPY app ./app
COPY themes ./themes

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os,time; f='/tmp/healthy'; exit(0 if os.path.exists(f) and float(open(f).read()) > time.time() else 1)"

CMD [".venv/bin/python", "./app/main.py", "--config-file", "/config/config.yml", "--extensions-dir", "/extensions"]
