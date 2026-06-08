# Base `/transact` (Tiers 0+1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A base-node `/transact` page that builds (via existing server endpoints), signs client-side with an ephemeral imported key, and submits transactions — plus a stake-attestation signer. Keys are in-memory only, never persisted.

**Architecture:** The node's existing `GET /transaction/{transfer,opposition,support,rescind}` endpoints return an *unsigned, sealed* txn built from a public key. The browser recomputes/verifies the txid, signs `signing_data` with the imported key, and POSTs. The only new crypto code is a parity-exact `data_csv`/`txid`/sign module in JS.

**Tech Stack:** Flask + Jinja + Bootstrap; client ESM (`clients/wallet/`, vendored to `static/wallet/` via `scripts/sync_wallet.py`); Web Crypto (RSA, SHA-256/512 already in `gc-crypto`); pytest + node `--test`.

**Spec:** `docs/superpowers/specs/2026-06-08-base-transact-design.md`

**Canonical formats (from `payload.py` / `transaction.py`) — must match byte-for-byte:**
- `Inflow.data_csv` = `f"{outflow_txid},{outflow_idx}"`
- `Outflow.data_csv` = `f"{amount},{address or ''},{opposition or ''},{rescind or ''},{support or ''},{rescind_kind or ''}"`
- `Transaction.data_csv` = `",".join([timestamp, address, public_key, ",".join(inflow.data_csv...), ",".join(outflow.data_csv...), version])` (+ `,prev_hash` only for coinbases — never here)
- `txid = sha256(sha512(data_csv)).hexdigest()` (`gc-crypto.millHash` does the digests; hex it)
- `signing_data = (data_csv + "," + txid).encode()`; `signature = wallet.sign(signing_data)`
- Server JSON omits `None` fields (`asdict_sans_none`), so reconstruct with `?? ''`.

**Existing JS to reuse (`clients/wallet/`):** `Wallet.fromPrivateKeyB58(b58)`, `Wallet.fromPublicKeyB64`, `wallet.sign(bytes)→sig`, `wallet.publicKeyB64`/address accessors; `gc-crypto` `millHash`, `base58decode`, `base64encode`; `gc-sig` `signHeaders` (request auth); `gc-attestation` `signStakeAttestation`; `gc-backup` `importPlain`. **Read these before writing `gc-transaction.mjs`.**

---

## PR 1 — `gc-transaction.mjs` + Python-locked parity tests

Branch: `feat/base-transact-js-core` off fresh `main`.

### Task 1: Python fixture emitter (locks parity)

**Files:** Create `tests/fixtures/gen_txn_fixtures.py`, output `tests/fixtures/txn_signing_vectors.json`

- [ ] **Step 1:** Write a script that builds one `Transaction` per shape using fixed inputs (construct `Transaction` directly with literal `Inflow`/`Outflow` lists — NOT via a chain, for determinism), for: transfer (1 inflow, 2 outflows addr+change), opposition (inflow + opposition outflow + change), support, rescind (rescind outflow + restake change). For each, emit `{name, txn_dict (to_dict, unsigned), data_csv, txid, signing_data_b64}` using the real `.data_csv` / `.calculate_txid()` / `.signing_data`. Use a fixed timestamp + a fixed wallet's address/public_key (load a test wallet, set on the txn).

```python
# sketch
import base64, json
from gumptionchain.transaction import Transaction
from gumptionchain.payload import Inflow, Outflow
from gumptionchain.wallet import Wallet
w = Wallet(b58ks='<fixed test b58>')  # or generate + print the b58 for the JS test
def vec(name, inflows, outflows):
    t = Transaction(timestamp='1700000000', inflows=inflows, outflows=outflows)
    t.set_wallet(w); t.seal()
    return {'name': name, 'txn': t.to_dict(), 'data_csv': t.data_csv,
            'txid': t.txid, 'signing_data_b64': base64.b64encode(t.signing_data).decode()}
# ... build the four shapes, json.dump a list
```

- [ ] **Step 2:** Run it; commit the script + generated `txn_signing_vectors.json`. Commit: `test(wallet): python-generated txn signing parity vectors`.

### Task 2: `gc-transaction.mjs` (TDD against the vectors)

**Files:** Create `clients/wallet/gc-transaction.mjs`, `clients/wallet/gc-transaction.test.mjs`

- [ ] **Step 1: Failing test** — `gc-transaction.test.mjs` (node:test) loads `../../tests/fixtures/txn_signing_vectors.json`; for each vector asserts `dataCsv(v.txn) === v.data_csv`, `await txid(v.txn) === v.txid`, and `base64(signingData(v.txn)) === v.signing_data_b64`. Run `node --test clients/wallet/gc-transaction.test.mjs` → FAIL (module missing).

- [ ] **Step 2: Implement `gc-transaction.mjs`:**

```js
import { millHash } from './gc-crypto.mjs';

const inflowCsv = (i) => [String(i.outflow_txid), String(i.outflow_idx)].join(',');
const outflowCsv = (o) => [
  String(o.amount), o.address ?? '', o.opposition ?? '',
  o.rescind ?? '', o.support ?? '', o.rescind_kind ?? '',
].join(',');

export function dataCsv(txn) {
  const fields = [
    String(txn.timestamp), String(txn.address), String(txn.public_key),
    (txn.inflows ?? []).map(inflowCsv).join(','),
    (txn.outflows ?? []).map(outflowCsv).join(','),
    String(txn.version),
  ];
  if (txn.prev_hash != null) fields.push(String(txn.prev_hash));
  return fields.join(',');
}

const hex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');

export async function txid(txn) {
  return hex(await millHash(new TextEncoder().encode(dataCsv(txn))));
}

export function signingData(txn) {
  return new TextEncoder().encode(dataCsv(txn) + ',' + txn.txid);
}

// Verify the node's txid, then sign. Returns a signed txn object ready to POST.
export async function signUnsignedTxn(unsigned, wallet) {
  const recomputed = await txid({ ...unsigned, txid: undefined });
  if (recomputed !== unsigned.txid) {
    throw new Error('txid mismatch: node-built txn does not match its fields');
  }
  const sig = await wallet.sign(signingData(unsigned));
  return { ...unsigned, public_key: wallet.publicKeyB64, address: wallet.address, signature: sig };
}
```

> Confirm the exact accessor names on the JS `Wallet` (`publicKeyB64`/`address`/`sign` return type) by reading `gc-wallet.mjs`; adjust. `millHash` returns a Uint8Array (SHA-512 then SHA-256) — hex it. The server JSON already carries `public_key`/`address` (built from your key), so `signUnsignedTxn` mostly confirms + signs.

- [ ] **Step 3: Run** → PASS. Add a sign-then-verify vector if a public-key verify helper exists in `gc-wallet`/`gc-crypto`.

- [ ] **Step 4: Sync + Python cross-check** — run `uv run python scripts/sync_wallet.py` to copy into `static/wallet/`. Add a pytest that loads a JS-signed fixture txn (emit one signed vector from JS, or sign in Python with the same wallet) and asserts `Transaction.from_json(...).validate()` + signature verify passes. Run `uv run pytest -k txn_parity`.

- [ ] **Step 5: Extend the node test glob** if needed so CI runs `clients/wallet/*.test.mjs` (check `.github/workflows/tests.yml`; `static/gumptionchain/static/js/*.test.mjs` is already covered — add the wallet dir glob).

- [ ] **Step 6: Gates** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest` + `node --test clients/wallet/gc-transaction.test.mjs`. Commit: `feat(wallet): gc-transaction.mjs — parity-exact txn data_csv/txid/sign`. Open PR.

---

## PR 2 — `/transact` page (build & sign + broadcast)

Branch: `feat/base-transact-page` off fresh `main` (after PR 1).

### Task 3: route + view + nav (TDD)

**Files:** Modify `src/gumptionchain/browser.py`, `templates/base.html`; create `templates/transact.html`; test `tests/test_transact_page.py`

- [ ] **Step 1: Failing test** — `GET /transact` → 200, contains the type selector markup, the security framing string ("never leaves your browser"), the broadcast section, and imports `wallet/gc-transaction.mjs`. Run → FAIL.

- [ ] **Step 2: Add view** (no chain/DB work — it's a static shell; the page's JS calls the API):

```python
@blueprint.route('/transact')
def transact_view() -> Any:
    return render_template('transact.html', title='Transact')
```

- [ ] **Step 3: Create `transact.html`** — extends base, `content` block only. Sections: (1) **Build & sign** — a `<select>` for type (transfer/opposition/support/rescind) that shows the type-specific fields (transfer: amount + dest address; opposition/support: amount + subject; rescind: amount + subject + kind), a key-import control (textarea for b58 / file input for `.pem`), a "Sign & submit" button, and a result/error area; (2) a collapsible **Broadcast** ("paste a pre-signed txn JSON" + submit); (3) **Sign attestation** placeholder (filled in PR 3). Prominent security banner. `{% include "transact/extra.html" ignore missing %}` hook. End-of-page inline `<script type="module">` wiring (Step 4).

- [ ] **Step 4: Client glue** — create `clients/wallet/transact-glue.mjs` (synced to static) exporting the wire-up: import key → `Wallet.fromPrivateKeyB58`/PEM; on submit, build the query, `signHeaders`-authed `GET /transaction/<type>?...`, parse → `signUnsignedTxn` → render confirmation → `signHeaders`-authed `POST /transaction/<txid>`; surface 403 (closed node), 503 (mempool full), and txid-mismatch distinctly. The inline module in `transact.html` imports and calls it (mirror how `verify.html` imports `verify-glue.mjs`). Keep DOM logic testable: a small `node:test` for the pure helpers (query building, response→message mapping) with a fake fetch, like `verify-glue.test.mjs`.

- [ ] **Step 5: Nav link** in `base.html` after Mempool: `<a class="navbar-nav" href="{{ url_for('browser.transact_view') }}">Transact</a>`.

- [ ] **Step 6:** Run view test + glue test → PASS. Commit: `feat(browser): /transact build-sign + broadcast page`.

### Task 4: seam test + gates

- [ ] **Step 1:** Add a `/transact` seam test to `tests/test_ui_seam.py` (consumer `base.html` re-skins it; the page is static so no chain needed).
- [ ] **Step 2: Gates** (incl. `node --test`) green. Commit + open PR.

---

## PR 3 — Attestation signing

Branch: `feat/base-transact-attestation` off fresh `main` (after PR 2).

### Task 5: sign-attestation section (TDD)

**Files:** Modify `templates/transact.html`, `clients/wallet/transact-glue.mjs` (+ test), `templates/verify.html` (link)

- [ ] **Step 1: Failing test** — `node:test` for the attestation helper: given a wallet + a claim `{txid, kind, subject, amount}`, `signStakeAttestation` produces a proof whose `parseStakeAttestation`/`verifyStake(signature check)` round-trips. Run → FAIL if helper not wired.

- [ ] **Step 2: Implement** — wire the **Sign attestation** section: inputs for txid/kind/subject/amount, reuse the imported key, call `signStakeAttestation`, render the proof JSON + a "copy" affordance + a note "paste into /verify". Add a link from `verify.html` to `/transact#attestation`.

- [ ] **Step 3:** Run → PASS. Browser test: `/transact` shows the attestation section; `/verify` links to it.

- [ ] **Step 4: Gates** green. Commit: `feat(browser): stake-attestation signer on /transact`. Open PR.

---

## Final

After all three merge: final reviewer over the combined diff; update the base/hub boundary note + the EGU checklist (#190) to record Tiers 0+1 shipped and Tier 1.5 deferred; file the Tier 1.5 (persistent wallet + passkey) follow-up issue.
