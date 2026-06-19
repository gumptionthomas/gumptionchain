# CLI Reference

GumptionChain installs a `gumptionchain` command (with a shorter `gc` alias).
It is a Flask-aware CLI: it loads your application via `FLASK_APP` and reads
configuration from the environment and `.env` (see
[configuration.md](configuration.md)).

Run `gumptionchain --help` for the live command tree, or
`gumptionchain <command> --help` for any command's options. This document
summarizes the commands and their arguments.

Global options (before the command): `-e/--env-file FILE`, `-A/--app
IMPORT`, `--debug/--no-debug`, `--version`.

## Node lifecycle

| Command | Description |
| --- | --- |
| `gumptionchain init` | Initialize the database (applies all migrations). |
| `gumptionchain run` | Run the Flask development server (`:5000`). |
| `gumptionchain validate` | Validate the node's block chain. |
| `gumptionchain export FILE` | Export the chain to a JSON Lines file (appends if it exists). |
| `gumptionchain import FILE` | Bulk-load blocks from a JSON Lines export (idempotent). |
| `gumptionchain sync` | Synchronize the chain from configured peers (READER access). |
| `gumptionchain routes` | Show the app's registered routes. |
| `gumptionchain shell` | Open a Python shell in the app context. |

### `gumptionchain mill ADDRESS`

Start a milling (proof-of-work) process, paying coinbase rewards to `ADDRESS`.

| Option | Description |
| --- | --- |
| `-m, --multi` | Use multiprocessing across CPUs when hashing. |
| `-r, --rounds N` | Rounds of milling between new-block checks (default 1). |
| `-s, --size N` | Hashes per round, per CPU when multiprocessing (default 100000). |
| `-w, --signing_key PATH` | Signing-key file for coinbase rewards. |
| `-p, --peer TEXT` | Peer to poll before checking for new blocks/transactions. |
| `-b, --blocks N` | Stop after N blocks (default 0 = run forever). |

## `gumptionchain db` — migrations

A passthrough to Flask-Migrate / Alembic. Common subcommands:

| Command | Description |
| --- | --- |
| `db upgrade` | Apply migrations (also run by `init`). |
| `db migrate -m "msg"` | Autogenerate a new revision (hand-review before committing). |
| `db check` | Fail if models drift from the migration history (CI gate). |
| `db current` | Show the current revision. |
| `db history` | List revisions chronologically. |

## `gumptionchain signing-key`

| Command | Description |
| --- | --- |
| `signing-key create [-d DIR]` | Create a new signing-key PEM file (defaults to the configured key dir). |
| `signing-key balance ADDRESS` | Print an address's balance in GRIT. |

## `gumptionchain subject`

Query subject stakes. `SUBJECT` is always the raw (unencoded) string.

| Command | Description |
| --- | --- |
| `subject opposition SUBJECT` | Opposition total (opposition minus rescinds), in GRIT. |
| `subject support SUBJECT` | Support total, in GRIT. |
| `subject search QUERY [-n LIMIT]` | Prefix-search subjects ranked by total GRIT at stake (limit clamped to 1–50, default 8). |

## `gumptionchain txn` — create transactions

All `txn` commands build, sign, and post a transaction. Amounts are GRIT
(floats accepted). Common options: `-t/--txn-signing_key PATH` (source key),
`-h/--host TEXT` (API host), `-w/--signing_key PATH` (API-auth key),
`-y/--yes` (non-interactive).

| Command | Arguments | Description |
| --- | --- | --- |
| `txn transfer` | `FROM_ADDRESS AMOUNT TO_ADDRESS` | Transfer GRIT to another address. |
| `txn split` | `FROM_ADDRESS COUNT DENOMINATION_GRIT` | Split a balance into `COUNT` same-address chips (1–49) of `DENOMINATION_GRIT` each. |
| `txn opposition` | `ADDRESS AMOUNT SUBJECT` | Stake opposition on a subject. |
| `txn support` | `ADDRESS AMOUNT SUBJECT` | Stake support on a subject. |
| `txn rescind` | `ADDRESS AMOUNT SUBJECT --kind {opposition,support}` | Rescind a prior stake. |

The `split` command mints fixed-size "chips" so a busy key can spend without
waiting on change confirmations — a key's spend concurrency equals its number
of unspent outputs.
