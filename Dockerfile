FROM python:3.11.0-alpine3.17

ENV POETRY_HOME=/opt/poetry
ENV POETRY_VENV=/opt/poetry-venv
ENV POETRY_CACHE_DIR=/opt/.cache

RUN apk add --no-cache libpq
RUN apk add --no-cache --virtual .deps gcc musl-dev postgresql-dev openssl-dev libffi-dev g++

RUN python3 -m -venv ${POETRY_VENV} \
    && ${POETRY_VENV}/bin/pip install --upgrade pip setuptools wheel

ENV PATH="${PATH}:${POETRY_VENV}/bin"

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN ${POETRY_VENV}/bin/pip install poetry
RUN poetry install --no-root --only main

RUN apk del .deps

USER 999

COPY app ./app
CMD [ "poetry", "run", "python", "./app/main.py"]