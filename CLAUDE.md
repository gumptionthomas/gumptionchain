# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

CancelChain is a custom proof-of-work blockchain (Flask + SQLAlchemy) where tokens are assigned to *subjects* (UTF-8 strings, 1–79 chars) as **opposition** (`subject`, rescindable via `forgive`) or **support** (`support`, permanent). It runs as both a Flask web app (browser views + JSON API) and a `cancelchain` CLI. The chain is permissioned: API access is gated by role (`READER` < `TRANSACTOR` < `MILLER` < `ADMIN`) keyed off wallet addresses listed in config.

Units: 1 **CCG / grumble** = 100 **curmudgeons** (`CURMUDGEON_PER_GRUMBLE` in `cancelchain.chain`). Float CLI amounts are converted via `grumble_to_curmudgeons`.

## Common commands

Tooling is driven by **Hatch**. Pinned dev versions live in `requirements-dev.txt`; the source of truth for the test env is `[tool.hatch.envs.test]` in `pyproject.toml`.

```bash
# Tests (full suite, uses tests/.test.env via pytest-dotenv)
hatch run test:run                    # pytest, no coverage
hatch run test:run-coverage           # pytest with coverage
hatch run test:run tests/test_chain.py::test_name   # single test
hatch run test:run -- --runmulti      # opt in to multiprocessing-marked tests (skipped by default)

# Lint (CI runs this exact command)
ruff check src tests

# Local app run (after `pip install -e .` and a populated .env)
cancelchain init                      # create SQLite schema (FLASK_SQLALCHEMY_DATABASE_URI)
cancelchain import path/to/cancelchain.jsonl   # bulk-load blocks from JSON Lines export
cancelchain run                       # Flask dev server on :5000
cancelchain --help                    # full CLI tree (txn/, wallet/, subject/, mill, sync, validate, export, import)

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

- `ruff` config in `pyproject.toml`: target Python 3.9, `line-length = 80`, large rule set enabled (`A,B,C,DTZ,E,EM,F,FBT,I,ICN,ISC,N,PLC,PLE,PLR,PLW,Q,RUF,S,SIM,T,TID,UP,W,YTT`). CI fails on any `ruff check src tests` violation. Several rules are ignored project-wide (see `ignore = [...]`) — notably `Q000` (double-quote preferred is *off*, single-quote is used throughout) and `S101` (assert allowed in tests).
- Python ≥ 3.9 (CI matrix: 3.9, 3.10, 3.11). Avoid 3.10-only syntax in `src/`.
- SQLAlchemy is pinned `<2.0`; don't import from 2.0-only namespaces. Flask-SQLAlchemy is 3.x (uses `db.Model`, `db.session`, classic `Model.query` style).
- pymerkle is pinned `>=4,<5` (block Merkle tree). v5 has breaking changes.
