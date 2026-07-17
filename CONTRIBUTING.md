# Contributing

## Setup

    uv sync --group dev
    uv run pre-commit install

## Before committing

    uv run pytest
    uv run pre-commit run --all-files

`pre-commit` runs `ruff check`, `ruff format`, and `mypy` on every commit;
CI runs the same checks plus the full test matrix (Python 3.9–3.13) on every
push and PR.

## Guidelines

- Zero runtime dependencies — stdlib only. Don't add one for what a few
  lines of `urllib`/`sqlite3` already do.
- New logic (a branch, a new code path) needs a test. Bug fixes need a test
  that fails before the fix and passes after.
- Keep `README.md` and `README.ru.md` in sync — both are maintained.
