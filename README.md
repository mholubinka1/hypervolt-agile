## Setup

- Install [Python version 3.11.x or higher](https://www.python.org/downloads/)
- Install [poetry](https://python-poetry.org/) version 1.3.x or higher
- Install dependencies:
    - Create a virtual environment: `poetry env use python`
    - Check that the environment has been activated: `poetry env list` (the enviroment will have `(activated)` next to it)
    - If the environment has not been activated, activate it and use it: `poetry shell`
    - Install all dependencies into the virtual environment including the development dependencies: `poetry install --with dev`

### pre-commit setup

There are pre-commit configurations (found in `.pre-commit-config.yaml`) which will execute code checking and linting tools automatically on every commit.

- Install [pre-commit](https://github.com/pre-commit/pre-commit): `pip install pre-commit`
- Install the pre-commit hooks from the configuration file: `pre-commit install`
- Test the pre-commit hooks for the first time: `pre-commit run --all-file`

## Tools

This template has a suite of tools to enforce good practice with regards to typing, linting, quality and style.

- [isort](https://pycqa.github.io/isort/) and [black](https://github.com/psf/black) for code style and formatting
- [mypy](https://mypy-lang.org/) for type checking and enforcement
- [ruff](https//beta.ruff.rs/docs/) for linting

These tools are all run automatically on each commit. In order to run them otherwise:

1. isort - `poetry run isort . --profile black`
2. black - `poetry run black .`
3. mypy - `poetry run mypy .`
4. ruff - `poetry run ruff check . --fix`
