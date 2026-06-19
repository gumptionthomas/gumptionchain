# Browser-Facing Node Proxy

`node_proxy_blueprint` (in
[`node_proxy.py`](../src/gumptionchain/node_proxy.py)) is an **embeddable Flask
blueprint** for consumer web apps. It lets a browser build, sign, and submit
GumptionChain transactions without exposing the node host or holding a relay
key in client-side code.

It is a **library helper an app mounts itself** — it is not part of a
GumptionChain node's default API surface. The relay holds no signing key
itself; the app supplies a configured `ApiClient` (node host + a
TRANSACTOR/READER key), and the relay converts GRIT↔grains, validates
subjects, and maps node errors to clean JSON.

## Mounting

```python
from gumptionchain import node_proxy_blueprint

app.register_blueprint(
    node_proxy_blueprint(
        make_client,            # () -> configured ApiClient (node host + key)
        url_path='/api/node',   # mount prefix (default)
        rate_limit=my_limiter,  # optional: (request) -> bool
        max_body_bytes=65536,   # optional request-size guard
    )
)
```

`make_client` is called per request, so it can pick a key based on app state
(e.g. a per-game house key). Requests exceeding `max_body_bytes` get `413`; a
`rate_limit` returning `False` gets `429`.

## Routes (relative to `url_path`)

Unlike the raw node API, **amounts here are in GRIT** — the relay converts to
grains before calling the node.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/balance/<address>` | Address balance, in GRIT. |
| GET | `/subject/balances?subject=<raw>` | A subject's support and opposition, in GRIT. |
| GET | `/subject/search?q=<prefix>&limit=<n>` | Prefix-search subjects (amounts in GRIT). |
| POST | `/txn/support` | Build an unsigned support txn. |
| POST | `/txn/oppose` | Build an unsigned opposition txn. |
| POST | `/txn/transfer` | Build an unsigned transfer txn. |
| POST | `/txn/split` | Build an unsigned self-split txn. |
| POST | `/txn/submit` | Submit a client-signed transaction. |
| GET | `/txn/<txid>/status` | Report `pending` or `milled` (with block + confirmations). |

### Build request bodies (JSON)

- **support / oppose:** `{"public_key", "subject", "amount_grit"}`
- **transfer:** `{"public_key", "to_address", "amount_grit"}`
- **split:** `{"public_key", "denomination_grit", "count"}` (`count` a positive integer)

Each build endpoint returns the **unsigned** transaction JSON. The client signs
it locally and submits it back via `POST /txn/submit` with
`{"signed": {<the signed txn, including txid and signature>}}`.

This server-builds / client-signs split keeps private keys in the browser
(never on the relay) while keeping the node host and relay credentials on the
server. See the
[transact UI extension seam](ui-extension-seam.md) for how the base explorer
and a themed host app layer on top of this.
