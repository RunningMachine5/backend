# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FDShield backend: a FastAPI service that ingests a single financial transaction, persists it to PostgreSQL (ParadeDB), calls an external ML Serving `/predict`, and — only when the model predicts fraud — computes per-fraud-type rule scores. It also exposes an MLOps admin surface that drives Cloud Run training jobs and serving promotions. The Agent and customer-chatbot areas are deliberately kept as **Fake** skeletons (see below) so each owner can swap in a real implementation later.

Note: the README and most in-code comments/docstrings are in Korean; match that language when editing existing comments.

## Commands

Python 3.13, managed with `uv`. Postgres/ParadeDB runs in Docker.

```bash
uv sync                                                          # install deps (use --locked in CI)
docker compose up -d                                             # start ParadeDB only (backend runs locally)
uv run --env-file .env alembic upgrade head                     # apply migrations
uv run --env-file .env uvicorn main:app --reload --port 8000    # run dev server
```

Tests use **unittest**, not pytest (despite the `.pytest_cache`):

```bash
uv run python -m unittest discover -s tests -v          # all tests
uv run python -m unittest tests.test_rule_engine -v     # single module
uv run python -m unittest tests.test_rule_engine.SomeTest.test_case   # single test
```

CI (`.github/workflows/ci.yml`) runs `unittest discover` with `OPENAI_API_KEY=test-only-key`, then builds the Docker image. `dev`-branch pushes deploy to a GCP VM via `deploy.yml`.

Alembic migrations autogenerate against `SQLModel.metadata`. `migrations/env.py` imports `app.data.model` to register every table — **a new model must be reachable from `app/data/model/__init__.py` or autogenerate will emit `DROP TABLE`**. Revision files live in `migrations/versions/` (timestamped filenames).

## Architecture

Request flow is layered: `main.py` → `app/api/*` (routers) → `app/pipelines/*` (orchestration) → `app/services/*` (units of work) → `app/repositories/*` → `app/data/model/*` (SQLModel tables). DTOs in `app/dto/*` are the contracts crossing these boundaries.

**The real path is the fraud-detection pipeline** (`app/pipelines/fraud_detection_pipeline.py`, driven by `POST /transactions`):
1. Persist customer/accounts/transaction from `TransactionCreateDTO`, then `commit`.
2. Call ML Serving `/predict` via `MLServingClient`. On any `MLServingError` the raw transaction is kept and `prediction_status` becomes `FAILED` (no failure row is written). Timeouts / `429` / `5xx` are retried (`ML_SERVING_MAX_ATTEMPTS`); `4xx` and contract errors are not.
3. If (and only if) the model returns `is_fraud`, `score_transaction_fraud_types` runs the active rule set and stores **all** per-type scores in `rule_scores` (the backend never picks one representative type; clients rank). Rule failures are logged and never block saving the ML result.

Transaction/commit discipline lives in the pipeline, not the session dependency: `app/core/db.py`'s `get_session` yields a session and does **not** commit — callers own `commit`/`rollback`. The pipeline wraps insert-through-commit in a single `IntegrityError` boundary and translates constraint names into `DuplicateTransactionError` / `CustomerIdentificationConflictError`.

**Rules engine** (`app/services/rules/`): rule sets are data. `repository.py` loads the active set, `feature_builder.py` derives features from the 54 raw fields, `expression_evaluator.py` evaluates rule expressions, `engine.py`/`scoring.py` produce `FraudTypeScoreResult`. Fraud type codes live in `app/domain/fraud_type_codes.py`.

**ML feature contract**: requests carry 54 raw features (`raw_data`, or flattened CSV column names like `ID`/`Customer_ID`). The backend validates types/required columns and forwards them to ML `/predict` as `features` with **no** one-hot encoding. `Location` must be `region names + lat + lon` within Korea bounds (lat 33–39, lon 124–132) or the request is rejected `422` before any save. See `examples/transaction-request.json`.

**MLOps admin** (`app/api/mlops.py`, `app/services/mlops/`): gated by `X-MLOps-Admin-Token`; if `MLOPS_ADMIN_TOKEN` is empty the entire `/mlops` surface returns `503`. It orchestrates Cloud Run training jobs (`cloud_run.py`), dataset builds from confirmed labels (`dataset_builder.py`), and traffic promotions. The full operator workflow (datasets → training runs → decision → promotion → deployment/complete) is documented in README.md under "MLOps 관리자 API".

### Customer chatbot — invoke the `chatbot-feature` skill first

**Before touching any chatbot code, invoke the `chatbot-feature` skill**
(`.claude/skills/chatbot-feature/SKILL.md`). It carries the index of which design document to
read for which task, the code-placement rules, the implementation order, and the
document-update obligation that follows any design change.

This applies to work touching:

- `app/services/{chatbot,rag}/`
- `app/pipelines/customer_chatbot_pipeline.py`
- `app/api/chat.py`
- the `chat_*` tables in `app/data/model/chatbot.py`
- `docs/customer-chatbot/`

The design lives in `docs/customer-chatbot/`, with `README.md` as the entry point (flow and
branching only; prompts, customer-facing wording, and the table definitions are
split into sibling files). **Never invent new prompt text or customer-facing wording in code** —
add it to the relevant document first, then move it into code. The skill's index table tells you
which file that is.

### Fake skeletons — do not treat as real

The Agent pipeline (`monitoring_agent_pipeline.py`) and its services under `app/services/{agent,analysis,rag,notification}/` are placeholders: `FakeVectorDB` (returns identical context for any query), `FakeLLM` (string-template answers), `FakeEmailSender` (prints). Transaction ingestion, ML Serving calls, Postgres persistence, and rule scoring are the **only** non-fake parts. The older Agent DTOs (`TransactionDTO`, `FraudAssessmentDTO`, etc.) are scheduled for removal once the real Agent contract is settled.

The customer-chatbot fakes are **gone**: `customer_chatbot_pipeline.py`, `FakeEmbedder`, `FakeGuideRetriever`, `FakeTransactionRepository`, the old `build_chatbot_chain`, and the `ChatbotRequestDTO`/`CustomerGuideDTO`/`ChatbotResponseDTO` DTOs were all removed. That area is now being built for real against the `docs/customer-chatbot/` design — `app/api/chat.py` holds only an empty router until PRD 2.7 lands.

## Config

All configuration is env vars read in `app/core/config.py` (no settings class). Key ones: `DATABASE_URL`, `ML_SERVING_URL`, `ML_SERVING_MAX_ATTEMPTS`/`ML_SERVING_RETRY_DELAY_SECONDS`, `MLOPS_ADMIN_TOKEN`, and the `GCP_*`/`CLOUD_RUN_*` group. Copy `.env.example` → `.env` for local runs.
