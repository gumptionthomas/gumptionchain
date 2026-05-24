# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

CancelChain is a custom proof-of-work blockchain (Flask + SQLAlchemy) where tokens are assigned to *subjects* (UTF-8 strings, 1–79 chars) as **opposition** (`subject`, rescindable via `forgive`) or **support** (`support`, permanent). It runs as both a Flask web app (browser views + JSON API) and a `cancelchain` CLI. The chain is permissioned: API access is gated by role (`READER` < `TRANSACTOR` < `MILLER` < `ADMIN`) keyed off wallet addresses listed in config.

Units: 1 **CCG / grumble** = 100 **curmudgeons** (`CURMUDGEON_PER_GRUMBLE` in `cancelchain.chain`). Float CLI amounts are converted via `grumble_to_curmudgeons`.

## Common commands

Tooling is driven by **uv** + **uv_build**. Runtime deps live in `[project.dependencies]`; dev deps in `[dependency-groups].dev`; both resolved into `uv.lock`.

```bash
# Tests (full suite, uses tests/.test.env via pytest-dotenv)
uv run pytest                            # full suite
uv run pytest --cov=cancelchain          # with coverage
uv run pytest tests/test_chain.py::test_name   # single test
uv run pytest --runmulti                 # opt in to multiprocessing-marked tests (skipped by default)

# Lint and format (CI runs both)
uv run ruff check src tests
uv run ruff format --check src tests

# Type check (non-blocking in CI; expect existing errors)
uv run mypy src

# Pre-commit (once per clone)
uv run pre-commit install
uv run pre-commit run --all-files

# Local app run (after `uv sync` and a populated .env)
uv run cancelchain init                  # create SQLite schema (FLASK_SQLALCHEMY_DATABASE_URI)
uv run cancelchain import path/to/cancelchain.jsonl   # bulk-load blocks from JSON Lines export
uv run cancelchain run                   # Flask dev server on :5000
uv run cancelchain --help                # full CLI tree (txn/, wallet/, subject/, mill, sync, validate, export, import)

# Production entry point (see Dockerfile)
gunicorn --bind :$PORT app:app
```

`cancelchain` is a `FlaskGroup` CLI defined in `src/cancelchain/__init__.py` (entry point `cancelchain = "cancelchain:cli"`). `python-dotenv` autoloads `.env` from CWD.

## Configuration model

Two stacked layers, both read by `create_app()` in `src/cancelchain/__init__.py`:

1. **`FLASK_*` env vars** → injected into `app.config` via `Flask.config.from_prefixed_env()` (strips the `FLASK_` prefix). This is how `SECRET_KEY`, `SQLALCHEMY_DATABASE_URI`, etc. get set.
2. **`CC_*` env vars** → loaded into `EnvAppSettings` (`src/cancelchain/config.py`, dataclass), then `app.config.from_object`. Values are JSON-parsed when possible, so list/bool settings (`CC_PEERS`, `CC_MILLER_ADDRESSES`, `CC_API_ASYNC_PROCESSING`) must be valid JSON strings in the env.

Key `CC_*` settings: `NODE_HOST`, `PEERS` (list of `http(s)://<peer-address>@host` URLs — username component is the peer's wallet address), `WALLET_DIR`, `DEFAULT_COMMAND_HOST`, `{ADMIN,MILLER,TRANSACTOR,READER}_ADDRESSES` (regex-matched against the JWT `sub` in `api.Role.address_role`). `WALLET_DIR` is walked at startup; every `*.pem` becomes an in-memory `Wallet` in `app.wallets`, keyed by address.

## Architecture

### Layered: dataclass domain ↔ DAO ↔ SQLAlchemy

Every core entity has a paired structure:

| Domain dataclass | DAO (`src/cancelchain/models.py`) | Notes |
|---|---|---|
| `Block` (`block.py`) | `BlockDAO` | Recursive CTE on `prev_id` provides `block_chain` / `transactions_chain` / `inflows_chain` / `outflows_chain` |
| `Chain` (`chain.py`) | `ChainDAO` | A chain is identified by its tip `block_hash` |
| `Transaction` (`transaction.py`) | `TransactionDAO` | Many-to-many with blocks via `block_transactions` |
| `Inflow` / `Outflow` (`payload.py`) | `InflowDAO` / `OutflowDAO` | Outflow has at most one of `address`, `subject`, `forgive`, `support` |
| pending pool | `PendingTxnDAO` + `PendingIOflowDAO` | `PendingTxnSet` is a `MutableSet` over the DAO |

Domain objects own validation, serialization (Marshmallow schemas in `schema.py`, `block.py`, `transaction.py`, `payload.py`), and round-trip via `to_dict` / `to_json` / `from_json` / `to_dao` / `from_dao` / `to_db` / `from_db`. **Don't add validation to the DAO layer** — it lives on the dataclass. `asdict_sans_none` (in `schema.py`) is what strips `None` keys before JSON; preserve that contract when adding fields.

### Network coordination: `Node` and `Miller`

`Node` (`node.py`) is the per-request coordinator instantiated inside views and CLI commands from `app.config` + `app.clients` (a dict of `ApiClient`s, one per configured peer). It owns:

- `receive_transaction` / `receive_block`: validate → persist → optionally forward to peers
- `send_transaction` / `send_block`: gossip to peers, skipping any host listed in the `Peer-Hosts` header (loop guard)
- `fill_chain`: walk backwards from a peer's tip, staging blocks in a `ChainFill` row, then applying them in forward order (this is how `cancelchain sync` works)
- `fill_peer`: the inverse — when a peer 404s on our block's parent, push ancestors until it accepts

`Miller(Node)` (`miller.py`) extends this with `create_block` (pulls from `pending_txns`, validates each against the chain, drops failures via `txn_failed` signal) and `mill_block` (drives `milling.milling_generator` and aborts early if a longer chain shows up).

### Proof of work

`milling.mill_hash = sha256(sha512(data))`. The block header (`Block.unproven_header`) concatenates `idx,timestamp,prev_hash,target,merkle_root,version,` plus a trailing `proof_of_work` integer. Difficulty retargets every `TARGET_INTERVAL = 2016` blocks (`chain.Chain.block_target`) toward `TARGET_GOAL_SECONDS = 600` (10 min), clamped to ×4 / ÷4 per interval. `MAX_TARGET` (in production) has 6 leading hex zeros; tests patch it to `F * 64` via the session-scoped `easy_mill_chain` fixture so blocks mine instantly. `cancelchain mill --multi` uses a `multiprocessing.Pool`; those tests are gated behind `--runmulti`.

### API authentication

`src/cancelchain/api.py` issues short-lived JWTs (`HS256`, `SECRET_KEY`, `API_TOKEN_SECONDS = 4h`) via a two-step handshake:

1. `GET /api/token/<address>` returns an RSA+AES-encrypted challenge (cipher in `ApiToken`); only the holder of the private key can decrypt it.
2. `POST /api/token/<address>` with the decrypted challenge yields a JWT containing `sub` (address) and `rol` (role name).

`ApiClient` (`api_client.py`) wraps this handshake; it transparently retries once on 401 by resetting the token. `Role.address_role` re-matches `*_ADDRESSES` config regexes on every request, so role membership is dynamic — addresses can match multiple roles, and the highest one wins.

### Async post-processing

When `CC_API_ASYNC_PROCESSING=true`, block/txn POSTs return `202` without doing the gossip work synchronously. Instead `api.queue_post_process` emits an `http_post` blinker signal, and the `handle_http_post` handler enqueues a Celery task (`tasks.post_process`) that POSTs back to `/api/<...>/process` to finish the work. The Celery broker URL must come from Flask config (`CELERY_BROKER_URL`); `tasks.init_tasks` copies `app.config` into `celery.conf` and wraps tasks with an `app_context`.

## Test conventions

- `tests/.test.env` is loaded by `pytest-dotenv` (see `[tool.pytest.ini_options]`). It defines `FLASK_SECRET_KEY=testkey` and a minimal `CC_READER_ADDRESSES` allowlist; `env_override_existing_values = 1` means it *overrides* anything in your shell.
- `tests/conftest.py` builds the `app` fixture by writing temporary `.pem` wallet files into a `TemporaryDirectory` and pointing `WALLET_DIR` at it, with a `NamedTemporaryFile` SQLite DB. There are four canonical wallets (`READER_WALLET`, `TRANSACTOR_WALLET`, `MILLER_WALLET`, `MILLER_2_WALLET`) wired to the corresponding `*_ADDRESSES` configs.
- `requests_proxy` / `remote_requests_proxy` fixtures use `requests_mock` to route HTTP calls into the Flask test client — that's how peer-to-peer gossip is tested without a network.
- `time_machine` (via `time_stepper`) is used wherever timestamps participate in validation (block ordering, txn expiry).
- Mark new tests that fan out across CPU cores with `@pytest.mark.multi`; they're skipped unless `--runmulti` is passed.

## Style

- `ruff` config in `pyproject.toml` under `[tool.ruff.lint]`: target Python 3.9, `line-length = 80`, large rule set enabled (`A,B,C,DTZ,E,EM,F,FBT,I,ICN,ISC,N,PLC,PLE,PLR,PLW,Q,RUF,S,SIM,T,TID,UP,W,YTT`). Several rules are ignored project-wide — notably `Q000` (single-quote convention enforced via `[tool.ruff.format] quote-style = "single"`) and `S101` (assert allowed in tests). In Phase 1 CI, `ruff check` is `continue-on-error: true` to allow incremental cleanup of pre-existing debt; only `ruff format --check` is a hard gate. Phase 3 removes the `continue-on-error` once the existing lint is cleaned up.
- Python ≥ 3.9 (CI matrix: 3.10, 3.11, 3.12, 3.13; 3.9 is the floor but not actively tested post-EOL). Avoid 3.10-only syntax in `src/`.
- SQLAlchemy is pinned `<2.0`; don't import from 2.0-only namespaces. Flask-SQLAlchemy is 3.x (uses `db.Model`, `db.session`, classic `Model.query` style).
- pymerkle is pinned `>=4,<5` (block Merkle tree). v5 has breaking changes.

## Conventions

- **Never push directly to main.** Every change — including refactors, cleanups, one-line typo fixes, and obvious-looking patches — goes through a branch + PR. The user makes the call about what's "too small" for a PR, not me.
- **Branch names:** `<type>/<short-description>` (e.g. `feat/peer-gossip-retry`, `fix/wallet-load-race`, `docs/api-auth-readme`).
- **Commit messages:** Conventional Commits (`feat(scope): description`, `fix: ...`, `refactor: ...`, `docs: ...`).
- **PR merge:** `gh pr merge <N> --squash --delete-branch`. Never regular merge or rebase, never leave the branch lying around.
- **Always wait for Copilot review** (`wor` shorthand) before merging — even when CI is green and local testing looks clean. Copilot catches things that look obvious only in hindsight. Skip only when the user explicitly says so.
- **`mwg`** = "merge when green" — `gh pr checks <N> --watch`, then squash-merge once green.
- **Run formatting and tests before commit:** `uv run ruff format --check src tests` and `uv run pytest`. CI gates on both.
- **No "while we're in here" scope creep.** A fix PR fixes the thing it was opened for; adjacent refactors become their own PRs (or issues for later). Bundling expands blast radius and complicates revert.
- **Never start a long-running dev server** from a Claude Code session — the user runs `uv run cancelchain run` themselves in a separate terminal. Sessions only run `pytest`.

## Supply-chain practices

- **`uv.lock` is tracked in git** and is authoritative for reproducible builds. CVE remediation goes through `uv lock --upgrade-package <name>` followed by committing the resulting lock change — never edit `pyproject.toml` pins in isolation. Use `git ls-files uv.lock` to confirm tracking (don't rely on `git check-ignore` exit-code semantics — they're inverted).
- **CVE scanning** runs in `.github/workflows/security.yml` (pip-audit, `--strict`) on every PR, push to main, and a weekly Sunday cron ahead of Monday's Dependabot run. Treat a red security build as a merge blocker: surface the CVE into a separate `fix(deps):` PR (`uv lock --upgrade-package <name>`, commit, review) and remediate before merging the feature PR that introduced it. The one documented exception is the initial security-workflow rollout PR itself, where a first run may surface pre-existing CVEs that predate the workflow; those land in follow-up `fix(deps):` PRs after the rollout merges, to give CI a clean baseline.
- **Dependabot** is configured in `.github/dependabot.yml` for pip, docker, and github-actions, all on a Monday cadence with a 3-day cooldown. Minor/patch updates are grouped; majors come as individual PRs.
- **Pin third-party GitHub Actions to commit SHAs**, not tags — tags can be retargeted to point at malicious code. Keep the `# vX.Y.Z` trailing comment for human readability and let Dependabot bump the SHA + comment together.
- **Validate Dockerfile changes locally** with `docker build --target builder -t cc-test .` (or a full build for later stages) before pushing. CI workflows here don't run the Docker build — syntax errors will silently pass and break the deploy. Specific gotcha: in Dockerfiles, `#` only starts a comment at the *beginning* of a line; trailing `# whatever` on a `COPY`/`RUN` line is parsed as additional arguments.
- **Avoid ad-hoc Node/npm tooling.** The npm ecosystem is under sustained supply-chain attack (typosquats, post-install hooks, compromised maintainer tokens). Prefer Python/uvx or plain text alternatives; if a Node dep is unavoidable, surface it explicitly so the dependency surface is reviewable.
