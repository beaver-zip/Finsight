# Repository Guidelines

These guidelines apply to `beaver-zip/Finsight`. Run commands from its checkout, not from the directory containing this guide if they differ.

## Project Structure & Module Organization

- `backends/app/core/`: external API clients, storage, caching, and cost accounting.
- `backends/app/domain/`: data models and calculations without external calls.
- `backends/app/agents/`: company reports, legal research, news, orchestration, and verification.
- `backends/app/api/`: FastAPI endpoints; `main.py` owns application startup.
- `backends/tests/`: pytest tests, shared setup in `conftest.py`, and fixed inputs in `fixtures/`.
- `backends/eval/`: model evaluation runners, datasets, and results; distinct from regression tests.
- `frontends/corp-advisor--app/src/`: React/TypeScript pages, components, and hooks.
- `backends/assets/fonts/` and `docs/img/`: report fonts and documentation images.

## Build, Test, and Development Commands

- `docker compose up -d weaviate neo4j`: start local databases.
- From `backends/`, run `pip install -r requirements.txt` inside a virtual environment.
- `PYTHONPATH=. pytest -q`: run backend tests from `backends/`.
- `uvicorn app.main:app --port 8000`: start the backend from `backends/`.
- From `frontends/corp-advisor--app/`, run `npm ci`, then `npm run dev`.
- `npm run build`: run TypeScript checks and create the frontend build.
- `npm test`: run frontend API/stream regression tests with Node's built-in test runner.
- `npm run lint`: run ESLint; follow existing configuration.

## Coding Style & Naming Conventions

Use four-space Python indentation, type annotations, and `snake_case` functions. Match existing two-space TypeScript indentation, `PascalCase` components, and `useCamelCase` hooks. Keep calculations in `domain/` and external calls in `core/`. Make focused changes; avoid unrelated formatting, speculative abstractions, and user-specific hardcoding.

## Testing Guidelines

Use pytest and pytest-asyncio; name files `test_*.py` and tests `test_<behavior>`. Reproduce bugs before fixing them, then retain those tests. Test normal behavior alongside failures, especially financial values, citations, document replacement, and nested cost accounting. Use fixed fixtures and isolated storage; keep paid API evaluations separate. No numeric coverage threshold is configured. CI runs backend tests, frontend regression tests and builds, and secret scanning.

## Commit & Pull Request Guidelines

Recent commits use `[fix]`, `[feat]`, `[docs]`, and `[chore]` prefixes with concise summaries, often in Korean. Follow that convention. Describe the problem, resulting behavior, tests run, and remaining limitations. Link related issues when available and include screenshots for visible UI changes. Keep fixes and their regression tests together.

Apply the ponytail skill to code edits and deletions. Commit each verified, coherent work unit using the appropriate prefix (`[fix]` for bug fixes, `[refactor]` for behavior-preserving changes). Do not push unless requested.

## Documentation Management

Store durable audit reports in `docs/audits/YYYY-MM-DD-<topic>.md`; use `docs/plans/` for implementation plans when needed. Record the reviewed commit, date, scope, evidence, and validation limits. Track findings as open, fixed (with commit/test evidence), or dismissed (with reasons); distinguish executed checks from proposed tests.

Keep shared reports and plans in Git. Exclude disposable source snapshots and debug output; use `.gitignore` for shared exclusions and `.git/info/exclude` for personal notes. Never include secrets or private data. Keep this guide focused on rules and document links, rather than copying entire reports into it. Read relevant document sections as needed; saving a file does not remove earlier chat context.

Audit probes using copied source are temporary evidence, not regression coverage. Convert useful cases into tests importing current project modules under `backends/tests/` (or the frontend test suite), then remove obsolete probes and snapshots.

Current work: start with [handoff](docs/plans/2026-11-11-handoff.md), then read the [audit](docs/audits/2026-11-11-refactoring-audit.md), [test plan](docs/plans/2026-11-11-refactoring-test-plan.md), and [baseline results](docs/testing/2026-11-11-baseline.md) as needed.

## Security & Configuration

Copy `backends/.env.copy` to `backends/.env` and supply credentials locally. Never commit secrets or private uploaded documents. Keep development databases local and use disposable data for destructive integration tests.
