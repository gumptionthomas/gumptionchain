# create_split — UTXO chip-minting primitive

**Date:** 2026-06-18
**Status:** design approved
**Motivation:** A key's concurrent-spend capacity equals its number of
independent UTXOs, and `create_transfer` *consolidates* (gathers inputs → one
change output), so a busy payer collapses to a few big lumps and stalls on the
"your previous transaction is still confirming" wall (observed live: a
gumptactoe house key down to 2 UTXOs, both pending-locked, 46 GRIT unspendable).
`create_split` lets a key pre-shard its balance into many small same-address
UTXOs ("chips") so it can pay many times concurrently — and chips sized to the
payment make those payments change-free, breaking the re-consolidation cycle.

## Goal

A new build primitive: in one transaction, mint **`count` outputs of
`denomination` grains each, back to the key's own address**, plus one change
output for any leftover. Exposed end-to-end (domain → node API → ApiClient →
node-proxy → CLI), mirroring `create_transfer`. Player signing/custody and txn
validation are untouched.

## Background — what exists (and is reused)

- `Chain.create_transfer(signing_key, amount, dest_address)` (`chain.py:605`):
  gathers `unspent_outflows(address, limit=amount, filter_pending=True)` until
  the running sum ≥ amount, raises `self._funds_error(address, amount)` on
  shortfall, emits the dest outflow + a change outflow to self. `create_split`
  mirrors this exactly, differing only in the outputs it builds.
- `_funds_error` (`chain.py:521`): returns `PendingFundsError` when the confirmed
  balance covers the amount but the pending-filtered gather fell short, else
  `InsufficientFundsError`. Reused as-is.
- `MAX_FLOWS = 50` (`transaction.py:55`): a regular transaction allows 0–50
  inflows and 1–50 outflows (`RegularTransactionModel`). This bounds a split to
  ≤ 50 outputs total.
- Transfer's surfaces are the template: `TransferTxnQueryModel` + `TransferTxnView`
  + `/api/transaction/transfer` (`api.py:503-540`, `authorize_transactor`);
  `ApiClient.get_transfer_transaction` (`api_client.py:143`); the `node_proxy`
  build routes (`node_proxy.py` `_build` + `/txn/transfer`); the `gc txn transfer`
  CLI (`command.py:697`, build+sign+submit one-shot).

## What we build — five layers

### 1. Domain — `Chain.create_split(signing_key, denomination, count)`

In `chain.py`, beside `create_transfer`:

```python
def create_split(
    self, signing_key: SigningKey, denomination: int, count: int
) -> Transaction:
    address = signing_key.address
    total = denomination * count
    t = Transaction()
    balance = 0
    unspent = self.unspent_outflows(
        address, limit=total, filter_pending=True
    )
    for txid, index, outflow in unspent:
        balance += outflow.amount or 0
        t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=index))
    if balance < total:
        raise self._funds_error(address, total)
    for _ in range(count):
        t.add_outflow(Outflow(amount=denomination, address=address))
    if balance - total:
        t.add_outflow(Outflow(amount=balance - total, address=address))
    t.set_signing_key(signing_key)
    t.seal()
    return t
```

- **Self-sharding:** every chip outflow goes to the key's **own** `address`;
  there is no destination parameter.
- **Outputs:** `count` chips (+ ≤1 change) = `count` or `count+1` outflows.

### 2. The `MAX_FLOWS` bound

`count` chips + 1 change ⇒ **`count` must be in `1..49`** (reserve one of the 50
output slots for change). The domain method assumes a validated `count`; the
node endpoint enforces it (below). Over 49 → a clean `400`. **No auto-tree**
for bigger fan-outs in v1 — the consumer calls `split` again as the chips/change
confirm.

### 3. Node API — `/api/transaction/split`

In `api.py`, mirroring `TransferTxnView`:

```python
class SplitTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    public_key: PublicKeyType
    denomination: int = Field(ge=1)
    count: int = Field(ge=1, le=MAX_FLOWS - 1)   # 1..49
```

A `SplitTxnView(MethodView)` GET that validates the query model, builds the
unsigned txn via `lc.create_split(signing_key, denomination, count)`, and returns
its `to_json()`. Registered at `/transaction/split` via `authorize_transactor`
(same role gate as the other build endpoints). `MAX_FLOWS` is imported from
`transaction`.

### 4. `ApiClient.get_split_transaction`

In `api_client.py`, mirroring `get_transfer_transaction`:

```python
def get_split_transaction(
    self, public_key: str, denomination: int, count: int,
    timeout: int | float | None = None,
    raise_for_status: bool = True,  # noqa: FBT001
) -> httpx.Response:
    return self.get(
        '/api/transaction/split',
        params={
            'public_key': public_key,
            'denomination': str(denomination),
            'count': str(count),
        },
        timeout=timeout, raise_for_status=raise_for_status,
    )
```

### 5. node-proxy route + CLI

**`node_proxy` `/txn/split`** (browser-facing build relay):

```
POST /api/node/txn/split
  { "public_key": "...", "denomination_grit": 2, "count": 30 }
→ 200  <unsigned split txn JSON>
```

- `public_key` non-empty string; `denomination_grit` → grains via the shared
  `_grit_to_grains` (>0, ≤0.01 GRIT precision); `count` parsed as int.
- Relays to `make_client().get_split_transaction(public_key, grains, count)`;
  returns the unsigned txn. Client signs (`signUnsignedTxn`) + submits via the
  existing `/api/node/txn/submit`. Same `_ok`/`_call` error mapping,
  CSRF-exempt, rate-limit hook as the other proxy routes.

**CLI `gc txn split COUNT DENOMINATION_GRIT`** in `command.py`, mirroring
`txn transfer`: `host_api_client(...).get_split_transaction(...)` → sign →
post_transaction → report the txid. (`COUNT` and `DENOMINATION_GRIT` as
arguments; `-h/--host`, `--txn-signing_key` options like the siblings.)

## Data flow

```
house/relay → POST /api/node/txn/split {public_key, denomination_grit, count}
  → grit→grains → ApiClient.get_split_transaction → GET /api/transaction/split
    → Chain.create_split → gather inputs (filter_pending) → count×denom + change
  ← unsigned split txn
  → signUnsignedTxn → POST /api/node/txn/submit → {txid}
  → (mine a block) → chips confirm → N independent UTXOs ready for concurrent spends
```

## Validation & errors

- `denomination ≥ 1`, `count ∈ 1..49` (pydantic on the endpoint; CLI/proxy parse
  then rely on the endpoint). `count > 49` or `< 1`, `denomination < 1` → `400`.
- `denomination × count` exceeds the spendable (non-pending) balance →
  `_funds_error`: `PendingFundsError` if confirmed covers it (funds locked in
  pending), else `InsufficientFundsError` — both `400` with their messages.
- A split is **one** transaction → counts as 1 against the per-transactor
  in-flight cap (`MAX_PENDING_PER_TRANSACTOR`).

## Testing

- **Domain** (`tests/`): `create_split` emits exactly `count` outflows of
  `denomination` to the signer's own address + a change outflow for leftover;
  total outflows ≤ `MAX_FLOWS`; an exact split (no remainder) emits no change;
  insufficient non-pending funds → `PendingFundsError` (when confirmed covers)
  / `InsufficientFundsError` (when it doesn't). Use the `mill_block` reward to
  fund.
- **Node endpoint**: `GET /api/transaction/split` returns the unsigned txn with
  the right outflow shape; `count=50`/`count=0`/`denomination=0` → `400`
  (validation); `authorize_transactor` gate.
- **ApiClient**: `get_split_transaction` builds the right request.
- **Proxy** (`tests/test_node_proxy.py`, `FakeClient`): `/txn/split` relays,
  converts `denomination_grit`→grains, forwards `(public_key, grains, count)`;
  bad amount → 400; node-down → 502.
- **CLI** (`tests/test_command.py`): `txn split` builds+signs+submits; the
  resulting pending txn carries `count` chip outflows.
- Full `pytest` + `ruff` + `mypy` + `node --test` (unaffected) green.

## Scope

**In:** the domain method, node endpoint + query model, ApiClient method, proxy
route, CLI command, and their tests. One base branch → PR.

**Out / deferred:**
- **Fill mode** (`create_split(key, denomination)` with no count → mint
  `balance // denomination` chips). The consumer can compute `count` itself;
  revisit if the ergonomic is wanted.
- **Auto-tree** for >49 chips in one call (inter-level confirmation complexity).
- **Auto-shard on receipt** (a node/app policy that splits incoming payments
  automatically) — an app concern, not a base primitive.
- **Spending unconfirmed change** (would remove the block-wait entirely but adds
  reorg risk) — explicitly not pursued.

## Invariants — what does NOT change

- Transaction validation, the txid/signing path, `MAX_FLOWS`, and the
  consensus/block rules. A split is an ordinary regular transaction.
- `create_transfer`, the existing build endpoints, and the gather/`_funds_error`
  helpers (reused, not modified).
- Player signing/custody.

## Risks

- **Chips help concurrency only if payments match the denomination.** A payment
  that isn't a single chip still combines chips + makes change (re-consolidating
  a little). Mitigation: size chips to the common payment; documented, not
  enforced. This is a usage guideline, not a code constraint.
- **49-chip cap per txn** means sharding a large balance into many chips takes
  several splits (each one txn, each needing its inputs confirmed before the next
  can spend the change). Acceptable; the alternative (auto-tree) is deferred.
- **Self-transfer outputs** must pass the existing outflow validation (address
  outflow to self is already valid — `create_transfer` change does the same).
