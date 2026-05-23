# Phase 1 — Tooling Modernization

**Status:** Draft for review
**Date:** 2026-05-22
**Scope:** Tooling and CI only. No library version bumps to runtime dependencies. No source code changes in `src/cancelchain/`.

## Goal

Make the project pleasant to develop on again, four years after the last active push. Phase 1 lays down a foundation of modern Python tooling so that subsequent phases (library upgrades, refactors) have a stable, fast feedback loop to land against.

Concretely: a fresh `git clone && uv sync && uv run pytest` should be the entire dev onboarding.

## Non-goals (deferred to later phases)

- Library upgrades (Flask 2→3, SQLAlchemy 1.4→2.0, pymerkle 4→5, Marshmallow 3→Pydantic v2, etc.)
- Python version floor change (stays at 3.9 for now; CI matrix expansion happens here, floor bump happens in Phase 2)
- Any modification to files under `src/cancelchain/` other than what `ruff format` produces
- Replacing `requests`, `pycryptodome`, or `base58check`
- Adding Alembic, structlog, OpenTelemetry, Sentry

## Changes

### 1. Package manager and build backend: pip + hatchling → uv + uv_build

Adopt **uv** as the sole tool for venvs, dep resolution, locking, **and building**.

- Generate `uv.lock` committed to the repo.
- Replace ad-hoc `pip install -r requirements.txt` and `hatch run …` invocations with `uv sync` and `uv run …`.
- Swap the build backend from `hatchling` to **`uv_build`**:

  ```toml
  [build-system]
  requires = ["uv_build>=0.5,<1.0"]
  build-backend = "uv_build"
  ```

- Remove **all** `[tool.hatch.*]` tables from `pyproject.toml`. That includes `[tool.hatch.version]`, `[tool.hatch.build.targets.sdist]`, `[tool.hatch.envs.test]`, and `[tool.hatch.envs.test.scripts]`.
- `uv_build` does not (yet) support reading version from a module attribute the way `[tool.hatch.version] path = …` did. Two consequences:
  - `[project]` `dynamic = ["version"]` goes away; the version becomes static (`version = "1.4.1"`).
  - `src/cancelchain/__init__.py` switches `__version__ = "1.4.1"` to:

    ```python
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("cancelchain")
    ```

    Runtime callers (`application.py`'s `inject_cc_version`, the `@click.version_option(package_name='cancelchain')` decorator, etc.) keep working unchanged.
- The default `uv_build` source layout is `src/<package>/`, which matches the existing project. No `[tool.uv.build-backend]` config needed.

### 2. Dependency layout: PEP 735 groups

Move dev dependencies into `[dependency-groups]` in `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-cov>=5",
    "pytest-dotenv>=0.5",
    "requests-mock>=1.12",
    "time-machine>=2.14",
    "coverage[toml]>=7.5",
    "ruff>=0.6",
    "mypy>=1.10",
    "pre-commit>=3.7",
]
```

Delete:

- `requirements.txt` — superseded by `uv.lock` (the lockfile of record) plus the `[project.dependencies]` array (the version constraints of record).
- `requirements-dev.txt` — superseded by `[dependency-groups].dev`.
- `[tool.hatch.envs.test]` and `[tool.hatch.envs.test.scripts]` from `pyproject.toml` — superseded by `uv run pytest`.

Note: runtime dependency *versions* in `[project.dependencies]` are **unchanged** in this phase. Only dev tooling versions move forward.

### 3. Linter: ruff 0.0.275 → ≥0.6 + ruff format

- Bump ruff. The config namespace moved between 0.x versions: `select` / `ignore` now live under `[tool.ruff.lint]`. The new file:

  ```toml
  [tool.ruff]
  target-version = "py39"
  line-length = 80

  [tool.ruff.lint]
  select = [ … existing set … ]
  ignore = [ … existing set … ]

  [tool.ruff.format]
  quote-style = "single"   # preserve the project's single-quote convention
  ```

- Adopt **`ruff format`** as the formatter. The project has none today, so this is a one-time noisy commit. Add the commit's SHA to `.git-blame-ignore-revs` so `git blame` skips it.
- The current rule set (`A,B,C,DTZ,E,EM,F,FBT,I,ICN,ISC,N,PLC,PLE,PLR,PLW,Q,RUF,S,SIM,T,TID,UP,W,YTT`) and ignore list carry over verbatim. No new rules adopted in this phase — that's a separate decision.

### 4. Type checking: mypy (introduced, not yet gating)

- Add `mypy` to the dev group and a minimal `[tool.mypy]` block:

  ```toml
  [tool.mypy]
  python_version = "3.9"
  warn_unused_ignores = true
  warn_redundant_casts = true
  strict_optional = true
  files = ["src/cancelchain"]
  ```

- CI runs `uv run mypy src` but the step is **non-blocking** in Phase 1 (`continue-on-error: true`). This establishes the runner without forcing a typing campaign before any library upgrades land. Tightening (per-module strictness, blocking CI) is Phase 3.

### 5. Pre-commit

Add `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0   # pinned, bumped by pre-commit autoupdate
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict
```

Install via `uv run pre-commit install`. No mypy hook locally — too slow for per-commit; it runs in CI only.

### 6. GitHub Actions (`.github/workflows/tests.yml`)

Replace the existing workflow with:

```yaml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12', '3.13']
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv python install ${{ matrix.python-version }}
      - run: uv sync --group dev --python ${{ matrix.python-version }}
      # ruff check is non-blocking in Phase 1 because the codebase carries
      # ~37 pre-existing lint errors. Phase 3 cleans them up and removes
      # this `continue-on-error`.
      - run: uv run ruff check src tests
        continue-on-error: true
      - run: uv run ruff format --check src tests
      - run: uv run pytest
      # mypy is non-blocking in Phase 1; Phase 3 tightens this.
      - run: uv run mypy src
        continue-on-error: true
```

Notes:
- Drops Python 3.9 from the matrix (post-EOL). 3.9 stays as the `requires-python` floor and `target-version` until Phase 2 bumps both.
- Adds 3.12 and 3.13.
- `pull_request` added as a trigger — non-fork PRs from the same repo would otherwise be tested twice; the standard idiom is fine here.
- Single job, no separate "lint" job — keeps the YAML short, all three checks happen on every Python version. If matrix time becomes a problem, split lint into its own job pinned to one Python version.

### 7. Dockerfile

Replace the existing single-stage `python:3.10` Dockerfile with a multi-stage uv-based build:

```dockerfile
# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.10
FROM ghcr.io/astral-sh/uv:0.4-python${PYTHON_VERSION}-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock README.rst ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src ./src
COPY --chown=app:app app.py ./

USER app
CMD ["gunicorn", "--bind", ":8080", "--workers", "1", "--threads", "8", "--timeout", "0", "app:app"]
```

Notes:
- Non-root runtime user.
- The build stage uses the official uv image; the runtime stage uses bare `python:slim` to keep the final image small.
- `$PORT` substitution comes from the orchestrator at run time (`docker run -e PORT=…`) or is overridden by command. The previous Dockerfile baked `$PORT` into `CMD exec`, which only works under shells that expand it; the JSON-array form here doesn't, so default to `:8080` and let the deploy target override.
- Pin the uv image to a minor version (`0.4`) so reproducibility doesn't break on a uv major.

### 8. Documentation updates

- Update `README.rst` "Install" and "Run" sections to use `uv` invocations. The current text recommends `pip install cancelchain` for end-users (which still works — PyPI publishing is unchanged) but the **development** instructions should switch to `uv sync && uv run cancelchain …`.
- Update `CLAUDE.md` "Common commands" section to reflect the new invocations.

## Out of scope (explicit reminders)

- **No** changes under `src/cancelchain/` except whatever `ruff format` produces as a one-time pass, plus the two-line `__version__` change in `__init__.py` required by the `uv_build` switch (section 1).
- **No** changes to runtime dependency versions in `[project.dependencies]`.
- **No** `from __future__ import annotations` additions, no new type hints in source.
- **No** Alembic migrations, no Pydantic, no httpx, no cryptography swap.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `ruff format` diff touches every file, polluting `git blame`. | Land formatting as a single isolated commit; add its SHA to `.git-blame-ignore-revs`. |
| mypy noise on existing code overwhelms the signal. | Non-blocking in CI; no per-module strictness yet. Real cleanup happens in Phase 3, after library upgrades land. |
| Existing `tests/.test.env` autoload behavior changes under uv. | `pytest-dotenv` is unaffected by the package manager; `env_files = ["tests/.test.env"]` in `[tool.pytest.ini_options]` still drives loading. Verified by running the suite before merging. |
| `uv.lock` resolution differs from current pinned `requirements.txt`, causing a behavior change. | Phase 1 keeps the **constraints** in `[project.dependencies]` identical to what they are today. `uv lock` will resolve within those constraints; any resolver-driven version drift is bounded by the existing `>=X.Y` floors. If a specific version pin is critical for a runtime lib (e.g. `pymerkle>=4,<5`), it's already in `pyproject.toml`. |
| Dockerfile rewrite changes the runtime Python from 3.10 to 3.13 before Phase 2's compat work lands. | Pin `PYTHON_VERSION=3.10` as the default `ARG` in Phase 1's Dockerfile. Bump to 3.13 default in Phase 2 along with `requires-python`. |
| pymerkle 4 wheel availability on Python 3.13. | If wheels are missing for 3.13, drop 3.13 from the CI matrix in Phase 1 and add it back in Phase 2 when pymerkle is upgraded. Decided at PR time. |
| `uv_build` is younger than `hatchling` and was still flagged "preview" in early releases. | Pin `uv_build>=0.5,<1.0` — accepts any 0.x release (uv_build is currently at 0.11.x and tracks uv's release cadence). Verify `uv build` output is installable end-to-end as part of acceptance. If a regression surfaces post-merge, the rollback is a single commit re-instating the hatchling `[build-system]` + `[tool.hatch.version]` blocks and reverting the `__version__` shim. |

## Acceptance criteria

- [ ] `git clone … && uv sync --group dev && uv run pytest` succeeds on a clean machine with no other setup.
- [ ] `uv run ruff check src tests` runs (may emit lint errors; CI does not block on them in Phase 1 — `continue-on-error: true` lets Phase 3 clean them up).
- [ ] `uv run ruff format --check src tests` exits 0 (after the one-time format pass).
- [ ] `uv run mypy src` runs (may emit errors; CI does not block on them).
- [ ] CI gates green on 3.10, 3.11, 3.12, 3.13: `ruff format --check` and `pytest` steps pass. `ruff check` and `mypy` may be red (both are intentionally `continue-on-error: true`).
- [ ] `docker build .` succeeds and the resulting image runs `cancelchain --help` and `gunicorn app:app` correctly.
- [ ] `requirements.txt`, `requirements-dev.txt`, `[tool.hatch.envs.test]` no longer exist.
- [ ] `uv build` produces an sdist + wheel. Wheel contents (Python files under `src/cancelchain/`, including templates) match the previous hatchling build; metadata reports `version = "1.4.1"`; `python -c "import cancelchain; print(cancelchain.__version__)"` in a fresh venv installed from the wheel prints `1.4.1`.
- [ ] `pip install cancelchain` (from PyPI, when published) still works for end-users — the build backend and `[project]` table are unchanged.

## Open decisions (to be resolved at PR time)

- Python matrix top end: 3.13 only if pymerkle 4 has wheels there; otherwise cap at 3.12 in Phase 1.
- mypy plugins: skipped in Phase 1. If the project adopts SQLAlchemy 2.0 in Phase 2, that's where `sqlalchemy.ext.mypy.plugin` (or its 2.0-native typing) gets added.

## What comes next

- **Phase 2 — Library upgrades.** Bump `requires-python` to 3.11, then march through Flask 2→3, Flask-SQLAlchemy 3.0→3.1, SQLAlchemy 1.4→2.0, pymerkle 4→5, pytest 7→8, gunicorn 20→23, celery, PyJWT, etc.
- **Phase 3 — Targeted refactors.** Type hints throughout; SQLAlchemy 2.0 `Mapped[]` annotations on `models.py`; Marshmallow → Pydantic v2 (decided in brainstorming); `requests` → `httpx`; `pycryptodome` → `cryptography` with byte-for-byte compat tests; Alembic.
- **Phase 4 — Observability.** Optional; OpenTelemetry, Sentry, structured logging.

Each subsequent phase will get its own design doc and implementation plan.
