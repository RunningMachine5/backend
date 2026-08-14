# Repository Guidelines

## Project Structure & Module Organization

`main.py` creates the FastAPI application. Application code lives under `app/`: routers in `api/`, orchestration in `pipelines/`, business logic in `services/`, persistence in `repositories/`, SQLModel tables in `data/model/`, and request/response contracts in `dto/`. Domain constants and enums belong in `domain/`; YAML resources belong in `resources/`. Tests are in `tests/` and mirror features with files such as `test_rule_engine.py`. Alembic revisions live in `migrations/versions/`; design notes and agent/chatbot guidance live in `docs/`. Example API payloads are stored in `examples/`.

## Build, Test, and Development Commands

- `uv sync` installs the Python 3.13 dependencies from `uv.lock` (`uv sync --locked` matches CI).
- `docker compose up -d` starts the local ParadeDB service.
- `uv run --env-file .env alembic upgrade head` applies database migrations.
- `uv run --env-file .env uvicorn main:app --reload --port 8000` starts the development API; check `/health` and `/docs`.
- `uv run python -m unittest discover -s tests -v` runs the full test suite.
- `uv run python -m unittest tests.test_rule_engine -v` runs one test module.
- `docker build -t fdshield-backend:local .` verifies the production image build.

Copy `.env.example` to `.env` before local development. Never commit credentials or admin tokens.

## Coding Style & Naming Conventions

Use four-space indentation and standard PEP 8 naming: `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Add type hints to public boundaries and keep API, pipeline, service, and repository responsibilities separate. No formatter or linter is currently configured, so preserve surrounding import and formatting style. Match the existing Korean language when editing comments or documentation.

## Testing & Database Changes

Tests use `unittest`, including async mocks where needed. Name files `test_<feature>.py`, classes `Test<Behavior>`, and methods `test_<expected_result>`. Cover success, validation, and failure paths without calling live ML, email, or cloud services. New SQLModel tables must be exported through `app/data/model/__init__.py` before Alembic autogeneration; review generated migrations for unintended drops.

## Commits & Pull Requests

Recent history favors concise prefixes such as `feat:`, `fix:`, and `refactor:`, often followed by an issue number, for example `fix: validate ML response contract (#128)`. Keep commits focused. Follow `.github/PULL_REQUEST_TEMPLATE.md`: link the issue, summarize changes, list verification commands, flag review risks, and add screenshots only when output is visual. PRs targeting `dev` or `main` must pass unit tests and the Docker build in CI.
