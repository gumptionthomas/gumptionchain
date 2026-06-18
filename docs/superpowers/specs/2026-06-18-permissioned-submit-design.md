# Permissioned submit + per-transactor accounting

**Date:** 2026-06-18
**Status:** design approved
**Motivation:** Close the last open write frontier on a deliberately
permissioned chain — transaction submission — and make per-relay abuse both
*capped* and *visible*, without touching the thing that makes the chain
trustworthy (the player's signing key).

## Goal

Today `GC_TRANSACTOR_ADDRESSES=["*"]` lets any authenticated signing key submit
transactions. That exposes *load* (spam / pool-flooding), not theft. Move to a
**permissioned-submit** posture where only allowlisted relays (the hub, each
game's house key) may submit, and add two node-side primitives the permissioning
makes enforceable:

1. A **per-transactor in-flight cap** — bound how many unconfirmed txns any one
   relay can hold in the mempool.
2. **Per-app submission accounting** — record which transactor submitted each
   txn, powering both the cap and a "which apps submit the most" stat.

Player-side signing and custody are **out of scope and unchanged**: a transactor
is a gatekeeper/relay, not a custodian. The player's signature carries authority
over funds; the transactor only decides whether to forward it.

## Boundary & trust model

- Submission gating is **per hop, by the forwarding node's authenticated
  identity** (`gc-sig` `_address`). The proxy-as-transactor pattern means
  consumers come along for free: gumptactoe/hub already submit via
  `ApiClient(node, house_key).post`, so an exact allowlist that includes their
  house keys is satisfied by construction.
- The cap and attribution are **node-local operational data — NOT consensus**.
  They never enter the block, the txid, or validation. A synced/gossiped txn is
  attributed (per hop) to the peer that forwarded it; cross-gossip *origin*
  propagation is explicitly deferred (see Out of scope).
- This is **not** a DoS-CPU defense. `authorize` verifies the RSA signature
  *before* the role check (`api.py:270-288`), so a flood of junk-signed POSTs
  still costs signature work regardless of the cap. The cap bounds the *pool*;
  the reverse-proxy/IP rate limit and the specced hashcash submit-PoW (#151)
  remain the separate request/CPU layer.

## What we build — four parts

### 1. The gate (operator config + docs — no app code)

`Role.address_role` already does exact-allowlist matching when `"*"` is absent,
and `authorize_transactor` already guards the submit endpoint (`TxnView`) and the
build endpoints. So permissioned submit is a config change:

```ini
GC_TRANSACTOR_ADDRESSES=["<hub_addr>", "<game_house_addr>", ...]   # drop "*"
```

`READER` stays `["*"]` — open reads, gated writes. Update CLAUDE.md's "Open
transacting & anti-spam (EGU)" section: the wildcard-submit posture is replaced
by exact-allowlist relays + the per-transactor cap below; #151 submit-PoW stays
noted as the separate CPU-DoS escalation.

### 2. Attribution data — one node-local table

New `SubmissionDAO` (`models.py`) → table `submission`:

| column | type | notes |
|---|---|---|
| `txid` | `String(100)`, **primary key** | first-submitter-wins via insert-or-ignore |
| `transactor_address` | `String(100)`, indexed | the authenticated `_address` that admitted it |
| `submitted_at` | `DateTime`, indexed | enables future `?since=` windowing |

- Written **once, on successful new admission** to the pending pool
  (`txn is not None` from `receive_transaction`), for **all** roles (so stats
  include hub/millers per-hop). `INSERT OR IGNORE` on `txid` keeps the first
  submitter as the recorded origin; re-submits don't double-count (and a
  re-submit returns `txn is None` anyway).
- Folded into the **baseline** migration `63d32cd7621a` (greenfield, no
  backfill). Domain object follows the existing DAO pattern; this table is
  node-local and has no domain-dataclass/serialization counterpart beyond what
  the DAO needs.

DAO methods:
- `SubmissionDAO.record(txid, transactor_address)` — insert-or-ignore at admission.
- `SubmissionDAO.pending_count(transactor_address) -> int` — count of this
  transactor's submissions whose `txid` is still in the pending pool (join
  `submission` × `pending_txn` on `txid`). Powers the cap; self-clears as txns
  confirm/expire.
- `SubmissionDAO.transactor_leaderboard() -> Select` — `GROUP BY
  transactor_address`, `count(*)` desc, `max(submitted_at)` (mirrors
  `subject_leaderboard`). Powers stats.

### 3. The in-flight cap

In `TxnView.post` (`api.py`), which already receives `_address`/`_role` via
`kwargs` from `authorize`:

```python
node, _, _ = node_lc_dao()
address = kwargs['_address']
role = kwargs['_role']
cap = current_app.config['MAX_PENDING_PER_TRANSACTOR']
if role == Role.TRANSACTOR and SubmissionDAO.pending_count(address) >= cap:
    return make_json_response(
        {'error': 'transactor pending quota exceeded'}, 429
    )
txn = node.receive_transaction(txid, request.data, visited_hosts=vhosts, process=process)
...
if txn is not None:                       # newly admitted to the pool
    SubmissionDAO.record(txid, address)
```

- Cap enforced **only when `role == Role.TRANSACTOR`** (exact) — MILLER/ADMIN
  (trusted infra + peer gossip) bypass it. `Role.address_role` returns the
  highest matching role, so a hub listed as MILLER is exempt.
- `429` (`"transactor pending quota exceeded"`) is distinct from the existing
  global-pool `503` (`MempoolFullError`). Check happens before admission; over-cap
  submissions are neither admitted nor logged.
- New config `MAX_PENDING_PER_TRANSACTOR` in `EnvAppSettings` (`config.py`), env
  `GC_MAX_PENDING_PER_TRANSACTOR`, **default 100** (small and safe; the global
  `MAX_PENDING_TXNS` stays 10000 as the far backstop).

### 4. Stats surface

- `GET /api/stats/transactors` (`authorize_reader`) → executes
  `transactor_leaderboard()`:
  ```json
  {"transactors": [
    {"address": "GC…GC", "count": 1234, "last_submit_at": "2026-06-18T…Z"},
    ...
  ]}
  ```
  Cumulative/all-time in v1; `submitted_at` makes a `?since=` window a cheap
  later add. Addresses shown raw — app-name labeling is a consumer/hub concern
  (no registry in base).
- `ApiClient.get_transactor_stats(*, raise_for_status=True)` → the endpoint.
- Base browser `/stats` explorer page rendering the leaderboard via
  `paginate_rows` (mirrors the `/subjects` page); linked from the explorer nav.

## Data flow (submission)

```
app proxy → POST /api/transaction/<txid>  (gc-sig as house TRANSACTOR key)
  authorize_transactor: verify sig → _address, _role  (verify is BEFORE the gate)
  TxnView.post:
    if role == TRANSACTOR and pending_count(address) >= 100 → 429
    receive_transaction(...) → admitted?
      yes → SubmissionDAO.record(txid, address)   [first-submitter-wins]
      (gossip to peers as today; peers attribute the forwarding node per-hop)
  → 201/202/200
later: GET /api/stats/transactors → leaderboard(count desc)
```

## Migration

Add the `submission` table (+ its indexes) to the baseline migration
`63d32cd7621a` and the `SubmissionDAO` model. `db.create_all()` (tests) picks it
up; `gumptionchain db check` parity holds on a fresh DB. No backfill (greenfield).

## Testing

- **DAO** (temp SQLite): `record` is insert-or-ignore (first-submitter-wins);
  `pending_count` counts only still-pending submissions for the address and
  drops when a txn leaves the pool; `transactor_leaderboard` ranks by count desc
  with `last_submit_at`.
- **Submit path** (Flask test client + signed clients):
  - TRANSACTOR over the cap → `429` (`"transactor pending quota exceeded"`),
    txn not admitted, not logged.
  - TRANSACTOR under the cap → admitted + a `submission` row written.
  - MILLER/ADMIN submitting past the cap → admitted (exempt), still logged.
  - duplicate re-submit → no double-count, no second row.
  - global `MempoolFullError` still returns `503` (unchanged).
- **Stats**: `GET /api/stats/transactors` returns the documented shape and is
  READER-gated (401/403 without); `/stats` page renders the leaderboard.
- **Migration parity**: fresh-DB `gumptionchain init` + `gumptionchain db check`
  → clean.
- Full `pytest` + `ruff` + `ruff format --check` + `mypy` green.

## Scope

**In:** the gate (config + CLAUDE.md rewrite); `SubmissionDAO` + table (baseline
migration); the in-flight cap with the TRANSACTOR-only/role exemption + 429;
`MAX_PENDING_PER_TRANSACTOR` config; `/api/stats/transactors` + `ApiClient`
method + `/stats` browser page; tests. One base branch → PR.

**Out / deferred:**
- **Cross-gossip origin propagation** — v1 attributes per hop (the ingress node
  records the real submitter; downstream millers record the forwarding peer). A
  `GC-Origin-Transactor` header to carry the original submitter chain-wide is a
  later add if/when chain-wide per-app stats are wanted.
- **`?since=` windowed stats** and **app-name labeling** (consumer concern).
- **#151 submit-PoW** — the separate CPU/DoS layer; the cap does not subsume it.
- **Hot revocation without restart** — config is read at startup; removing a
  transactor takes effect on restart (accepted; revocation is rare).
- Per-transactor cap **overrides** (per-address custom caps) — one global default
  for v1.

## Invariants — what does NOT change

- Player signing / custody / txn validation / the block, txid, and consensus
  path. `submission` is node-local metadata only.
- The build endpoints and existing submit semantics (gossip-on-new-admission,
  async post-processing, the `503` global cap).
- `READER`/`MILLER`/`ADMIN` role behavior; only the *operator's* choice to stop
  using the TRANSACTOR `"*"` wildcard changes posture.

## Risks

- **Cap too low chokes a legit busy app.** Default 100 is deliberately
  conservative; hitting it legitimately is the signal to raise
  `GC_MAX_PENDING_PER_TRANSACTOR`. It's a single config knob, restart to change.
- **`pending_count` join cost** grows with the `submission` table (all-time) but
  filters by indexed `transactor_address` then checks membership in the bounded
  pending pool — fine at expected scale; revisit only if the table gets huge.
- **Attribution is trust-scoped to permissioned relays.** A permitted-but-
  malicious transactor can still spam up to its cap and mis-be-attributed only as
  itself (it can't forge another's address — `_address` is gc-sig-authenticated).
  Acceptable: the gate caps blast radius; revocation handles a bad actor.
