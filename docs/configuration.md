# Configuration Reference

GumptionChain reads configuration from environment variables, conventionally
placed in a `.env` file in the working directory (loaded automatically via
[python-dotenv](https://pypi.org/project/python-dotenv/)). There are two
layers:

1. **`FLASK_*`** variables are injected into `app.config` with the `FLASK_`
   prefix stripped (Flask's `from_prefixed_env`). This is how Flask and
   extension settings such as `SECRET_KEY` and `SQLALCHEMY_DATABASE_URI` are
   set.
2. **`GC_*`** variables are parsed into the node's own settings. Values are
   **JSON-parsed when possible**, so list and boolean settings must be valid
   JSON strings (e.g. `GC_PEERS='["https://..."]'`,
   `GC_API_ASYNC_PROCESSING=true`).

## Flask settings

| Variable | Description |
| --- | --- |
| `FLASK_APP` | The application import name. Set to `gumptionchain`. |
| `FLASK_SECRET_KEY` | A unique random string. Required by Flask for session/CSRF infrastructure. |
| `FLASK_SQLALCHEMY_DATABASE_URI` | SQLAlchemy database URL (e.g. `sqlite:///gc.sqlite`, or a `postgresql+pg8000://…` URL). |

A minimal `.env`:

```ini
FLASK_APP=gumptionchain
FLASK_SECRET_KEY=change-me-to-a-random-string
FLASK_SQLALCHEMY_DATABASE_URI=sqlite:///gc.sqlite
```

## Node settings (`GC_*`)

| Variable | Default | Description |
| --- | --- | --- |
| `GC_NODE_HOST` | — | This node's own host URL, `http(s)://<address>@host`. The `<address>` is the local signing-key address this node signs *as*; auth signatures are bound to this host. |
| `GC_PEERS` | `[]` | JSON list of peer URLs `http(s)://<address>@host`. `host` is the peer; `<address>` is the **local** signing-key address this node signs as when talking to that peer. |
| `GC_DEFAULT_COMMAND_HOST` | — | Default API host for CLI commands. |
| `GC_SIGNING_KEY_DIR` | — | Directory walked at startup; every `*.pem` is loaded as an in-memory signing key, keyed by address. |
| `GC_API_CLIENT_TIMEOUT` | `10` | Per-request timeout (seconds) for outgoing peer calls. |
| `GC_API_ASYNC_PROCESSING` | `false` | When `true`, block/txn POSTs return `202` and finish gossip work via a Celery task. |
| `GC_MAX_CHAIN_FILL_DEPTH` | `50000` | Caps a single `fill_chain` ancestor walk. |
| `GC_FORK_PRUNE_DEPTH` | `100` | Depth bound for fork pruning. |
| `GC_SYNC_BATCH_SIZE` | `256` | Max blocks returned by `/api/blocks` and pulled per sync batch. |
| `GC_MAX_PENDING_TXNS` | `10000` | Global mempool admission cap. A full pool returns HTTP `503`. |
| `GC_MAX_PENDING_PER_TRANSACTOR` | `100` | Per-TRANSACTOR in-flight (unconfirmed) txn cap. Over-cap returns HTTP `429`. MILLER/ADMIN are exempt. |

## Role allowlists

Roles form the hierarchy `READER` < `TRANSACTOR` < `MILLER` < `ADMIN`. Each
variable is a JSON list of exact signing-key addresses. An address may appear
in several lists; the highest matching role wins. Roles are re-checked on
every request, so changes take effect immediately (after a config reload).

| Variable | Notes |
| --- | --- |
| `GC_READER_ADDRESSES` | May contain the literal `"*"` to grant READER to any authenticated key. |
| `GC_TRANSACTOR_ADDRESSES` | May contain `"*"` (open submission), but an **exact allowlist of relay addresses is the recommended posture** — the wildcard reopens the spam frontier. Gating exposes load, not theft. |
| `GC_MILLER_ADDRESSES` | Exact addresses only. Required for peer block gossip. |
| `GC_ADMIN_ADDRESSES` | Exact addresses only. |

Invalid configurations are rejected at startup: a non-address entry, or a
`"*"` outside `READER_ADDRESSES`/`TRANSACTOR_ADDRESSES`, raises an
`InvalidRoleConfigError`.

## Joining a network

To participate in an existing network, your instance needs MILLER or
TRANSACTOR access to a node, granted by that node's operator listing your
address in its allowlist. Configure that node as a peer:

```ini
GC_NODE_HOST=http://GCYourAddressGC@localhost:5000
GC_PEERS=["https://GCYourAddressGC@peer.example.com"]
GC_DEFAULT_COMMAND_HOST=https://GCYourAddressGC@peer.example.com
GC_SIGNING_KEY_DIR=/path/to/signing_keys
```

`GCYourAddressGC` is your signing-key address; the directory holds your key's
PEM file. Restart to load the new configuration.
