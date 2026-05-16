FROM python:3.13.13-alpine3.23 AS builder

ENV POETRY_VENV=/opt/poetry-venv
ENV POETRY_CACHE_DIR=/opt/.cache
ENV POETRY_VIRTUALENVS_IN_PROJECT=true
ENV PATH="/opt/poetry-venv/bin:${PATH}"

RUN python3 -m venv ${POETRY_VENV} \
    && pip install --upgrade pip setuptools wheel \
    && pip install poetry

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN apk add --no-cache --virtual .deps gcc musl-dev postgresql-dev openssl-dev libffi-dev g++ \
    && poetry install --no-root --only main \
    && apk del .deps \
    && rm -rf ${POETRY_CACHE_DIR}


FROM python:3.13.13-alpine3.23

ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache libpq

WORKDIR /app

COPY --from=builder /app/.venv ./.venv

USER 999

COPY app ./app

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os,time; f='/tmp/healthy'; exit(0 if os.path.exists(f) and float(open(f).read()) > time.time() else 1)"

CMD [".venv/bin/python", "./app/main.py", "--config-file", "/config/config.yml"]
