# Base node `/transact` — browser transaction creation (Tiers 0+1)

**Date:** 2026-06-08
**Context:** EGU #1 / #5 boundary decision — wallet/txn creation in the base app
**Status:** design approved

## Goal

Give the vanilla `gumptionchain` node a browser path to **create and submit
transactions** (and sign stake attestations), so a standalone node is usable
for *participating* in the chain, not just exploring it. Keys are
**ephemeral, imported, in-memory only** — never persisted, never sent.

This is **Tiers 0 + 1** of the wallet roadmap. Deferred:
- **Tier 1.5** — persistent browser wallet (IndexedDB + passkey/passphrase +
  backup). Carries an origin-trust risk; gets its own security-focused pass.
- **Tier 2** — hosted/networked features (cross-device sync, the handle→address
  identity directory, recovery-as-a-service). Stays in the hub.

## Boundary (restated)

Base = stateless, ephemeral, single-shot signing client. Hub = the *trusted
origin* for persistence (1.5) plus the server-only features (2). Mirrors the
verify(base)/proof-store(hub) split: base does the stateless single-shot, the
moment you want it *remembered* you go to the hub.

## Architecture: node builds the unsigned txn, browser signs it

The node already exposes the right primitive — server-side construction
endpoints that take a **public key** (no private key) and return an *unsigned,
sealed* transaction (`src/gumptionchain/api.py`):

| Endpoint (GET, `authorize_transactor`) | Builds via | Params |
|---|---|---|
| `/transaction/transfer` | `lc.create_transfer` | `public_key`, `amount`, `address` |
| `/transaction/opposition` | `lc.create_opposition` | `public_key`, `amount`, `subject` |
| `/transaction/support` | `lc.create_support` | `public_key`, `amount`, `subject` |
| `/transaction/rescind` | `lc.create_rescind` | `public_key`, `amount`, `subject`, `kind` |

Each does the UTXO selection, inflow/outflow + change assembly, and `seal()`
(sets the txid) server-side; it can't sign (no private key). So the browser
**never reimplements transaction construction**.

Per-transaction flow:
1. Browser → `GET /transaction/<type>?public_key=…&…` → unsigned, sealed txn JSON.
2. Browser parses + **displays** the txn (type, amount, dest/subject, inputs,
   change/fee) for explicit user confirmation.
3. Browser independently recomputes `txid` from the returned fields and
   **verifies it matches** the node's txid (catches a dishonest node), then
   signs `signing_data` with the imported key.
4. Browser → `POST /transaction/<txid>` with the signed txn, gc-sig-v1 authed.

### Signing parity (the one genuinely new piece)

From `transaction.py`:
- `data_csv` = canonical CSV of `timestamp, address, public_key, <inflows csv>,
  <outflows csv>, version[, prev_hash]` (the trailing `prev_hash` only for
  coinbases — never produced here).
- `txid = mill_hash_str(data_csv)` where `mill_hash = sha256(sha512(data))`,
  hex.
- `signing_data = (data_csv + "," + txid).encode()`.
- `signature = wallet.sign(signing_data)`.

The browser must reconstruct **`data_csv`** (incl. each `Inflow.data_csv` /
`Outflow.data_csv` sub-serialization and field order) to build `signing_data`
and to verify the txid. This is the only real port, and it is **parity-critical**
— one byte off and the node rejects the txn. It ships with **test vectors
generated from Python** so JS and Python agree exactly.

Everything else already exists in the shipped wallet ESM
(`src/gumptionchain/static/wallet/`, source `clients/wallet/`):
- `gc-crypto.millHash` already computes `sha256(sha512(...))` (hex via a small
  bytes→hex helper),
- `Wallet.fromPrivateKeyB58` / `sign(bytes)` (RSA, Web Crypto),
- `signHeaders` (gc-sig-v1 request auth) for the GET/POST calls,
- `gc-attestation.signStakeAttestation` for the attestation feature,
- `gc-backup.importPlain` (b58) for key import.

*Rejected alternative:* having the endpoints return `signing_data` so the
browser signs server-provided bytes — less JS, but it delegates trust and skips
the txid-honesty check. The browser deriving what it signs is worth the port.

## Page: one `/transact`, two modes + attestation

A single `browser` blueprint route `/transact` → `transact.html`, extending
`base.html`, content block only, with a nav link. Three sections:

1. **Build & sign** (Tier 1) — choose type (transfer / opposition / support /
   rescind), fill the type-specific fields, import key, confirm the parsed txn,
   sign + submit. Import: paste a b58 private key or upload a `.pem`
   (`wallet create` output) — parsed to a `Wallet` **in JS memory only**.
2. **Broadcast** (Tier 0, secondary/"advanced") — paste a pre-signed txn JSON
   and submit. *Note:* `POST /transaction/<txid>` is `authorize_transactor`, so
   even broadcast needs a key to sign the **request envelope** — the page
   reuses the imported key for that. No new unauthed submit endpoint (that is
   the #151 submit-PoW territory, out of scope).
3. **Sign attestation** — produce a `gc-msg-v1` stake proof (the producer side
   of `/verify`) via `signStakeAttestation` + the imported key. `/verify` stays
   verify-only and links here.

All client JS is an inline `<script type="module">` importing from
`url_for('browser.static', filename='wallet/…')` + a new
`wallet/gc-transaction.mjs`, consistent with how `/verify` wires its module
(base CSP already allows the inline module).

## Auth / anti-spam reality

- Works cleanly on an **open-transacting** node (`GC_TRANSACTOR_ADDRESSES=["*"]`).
- On a **closed** node, the imported key's address must be in
  `TRANSACTOR_ADDRESSES` or the build GET / submit POST returns 403 — the UX
  surfaces this explicitly ("this node restricts transacting; your address
  isn't authorized") rather than failing opaquely.
- Existing anti-spam (`MAX_PENDING_TXNS` 503, reverse-proxy rate limit, specced
  submit-PoW #151) is unchanged; this page is just a browser client of the
  existing authed API.

## Security model (this effort)

- **Import-only, in-memory, never persisted, never transmitted.** Only the
  signature + public key leave the browser. No IndexedDB, no passkey, no
  storage (that's 1.5).
- Loud, persistent framing on the page: "your private key never leaves your
  browser; this node does not store it; reloading or closing the tab clears
  it."
- The key lives in a module-scoped variable for the session; provide an
  explicit "forget key" control.
- Confirmation step before every signature shows exactly what is being signed.

## Data flow

```
/transact (browser)
  import key (b58/PEM) -> Wallet (JS, in-memory)
  GET /transaction/<type>?public_key=...   (signHeaders authed)
      -> unsigned sealed txn JSON
  recompute txid (gc-transaction) == server txid ?  (honesty check)
  display parsed txn -> user confirms
  signing_data = data_csv + "," + txid ; wallet.sign(...)
  POST /transaction/<txid>  (signHeaders authed) -> 201/202/503/403
attestation: signStakeAttestation(wallet, claim) -> proof JSON (paste into /verify)
```

## Testing

- **JS parity (node:test):** `gc-transaction.test.mjs` — `data_csv` + `txid` +
  `signing_data` for each txn type match **Python-generated fixtures**;
  round-trip a server-built unsigned txn → signed txn that Python
  `Transaction.from_json(...).validate()` + signature-verify accepts. (A small
  Python helper/test emits the fixtures so the two stay locked.)
- **Server:** the build endpoints already have coverage; add a cross-check test
  that a JS-signed txn (fixture) validates server-side.
- **Browser view:** `/transact` renders (200), has the three sections + the
  security framing + nav link; seam test (consumer `base.html` re-skins it).
- **Hard gates:** ruff + format, mypy strict, pytest, and the node `--test`
  glob (already includes `src/gumptionchain/static/js/*.test.mjs`; extend to the
  wallet dir if needed).

## PR decomposition (sequential, off fresh main)

0. **docs** — this spec + the plan.
1. **`gc-transaction.mjs` + parity tests** — `data_csv`/`txid`/`signing_data`
   reconstruction and txn signing, vetted against Python fixtures. Foundation,
   no UI. Synced into `static/wallet/` via the existing `scripts/sync_wallet.py`
   (source of truth is `clients/wallet/`).
2. **`/transact` page** — build & sign (all four types) + broadcast mode +
   confirmation UX + closed-node 403 handling + security framing + nav link +
   view/seam tests.
3. **Attestation signing** section + `/verify` link.

## Out of scope / follow-ups

- Tier 1.5 (persistent wallet + passkey) — separate spec, security-focused.
- An unauthed public submit endpoint (needs #151 submit-PoW first).
- PEM import edge cases beyond standard pkcs8 (b58 is the primary path).
