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

CMD [".venv/bin/python", "./app/main.py", "--config-file", "/config/config.yml"]
