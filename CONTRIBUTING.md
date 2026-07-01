# Contributing to Cognitrix

Thanks for your interest in improving Cognitrix! This guide covers local setup, the
development workflow, and the checks your change must pass.

## Prerequisites

- **Python 3.11–3.13**
- **[Poetry](https://python-poetry.org/)** for dependency management
- **Node.js 18+** and **pnpm** (only for the web UI)
- **Redis** (only if you work on the background task API / Celery worker)

## Local setup

```bash
git clone https://github.com/theonlyamos/cognitrix.git
cd cognitrix

# Python package + dev tools (pytest, ruff)
poetry install --with dev

# Web UI (optional)
cd frontend && pnpm install && pnpm run build && cd ..
```

Copy `.env.example` to `.env` and fill in at least one LLM provider key (see the README
for the full list). For the web UI set `JWT_SECRET_KEY` to a stable value.

## Development workflow

1. Branch off `main` (or `development`): `git checkout -b my-change`.
2. Make your change with tests.
3. Run the checks below and make sure they pass.
4. Open a pull request describing **what** changed and **why**.

## Required checks

Every pull request must pass:

```bash
poetry run ruff check .        # lint
poetry run pytest -q           # tests
```

These also run in CI on every push and pull request.

## Guidelines

- Keep exception handling narrow and logged — no bare `except:` that swallows errors.
- Library code should log via `logging.getLogger('cognitrix.log')`, not `print`.
- Add a test for any non-trivial logic (a branch, a parser, a security/auth path).
- Never re-introduce unsandboxed `exec`/`eval` or `shell=True` on untrusted input.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
