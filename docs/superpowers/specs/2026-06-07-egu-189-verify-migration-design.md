# EGU #189 — Migrate verify + public provenance to base; build the base↔extension UI seam

**Issue:** #189 — Batteries-included default UI for a vanilla gumptionchain node (themeable by extensions)
**Date:** 2026-06-07
**Scope:** Migration of the verify capability + public provenance read from `gumption-hub` down into base `gumptionchain`, and the template-override seam that migration forces. Fleshing out the rest of #189's base pages (home/explorer, blocks list, subject tally pages) is explicitly **out of scope** for this spec.

## Background

The verify/proof feature was built in `gumption-hub` first. On review, the primitives it depends on already live in base (`gumptionchain.attestation`, `gumptionchain.message` in Python; `clients/wallet/gc-attestation.mjs`, `gc-message.mjs` in JS, which the hub *vendors*). The hub added presentation plus two thin services on top of base primitives.

Splitting those by where they belong:

| Piece | Verdict | Why |
|---|---|---|
| Verify capability (`verify.html` + `verify-glue.mjs`) | **migrate to base** | Stateless chain-truth lens; batteries-included; rides the template seam like every other read page. |
| Public provenance read (`lookup_provenance`, JSON endpoint) | **migrate to base** | Verify's data dependency; already morally public (base's HTML `transaction_view` serves the same data unauthed). |
| Proof store (`StoredProof` + `POST /proof` + permalink + `proofs.py`) | **stays in hub** | Stateful sharing/distribution service (off-chain envelopes, public write endpoint, abuse surface, new table). Not chain truth. It is the reference example of an extension-built feature. |
| OG social cards (`og.py`, `/proof/<hash>/og.png`) | **stays in hub** | Hub branding. |

### Two distinct seams (do not conflate)

1. **The #189 seam — template/skin override.** Base ships page templates + `base.html`; an extension drops its own `base.html` (and/or individual page templates) into app-level `templates/`, and Flask resolves app templates ahead of blueprint templates. Every customizable read page rides this one seam. **This spec builds and documents it.**
2. **"Extension owns a feature" seam — own blueprint + own model on the shared `db`** (`Model.__table__.create(bind=db.engine, checkfirst=True)` after base's `db upgrade`). Already exists; the hub's proof store already uses it. Needs nothing new. The proof store rides *this* seam, which is why it does not need to move.

## Goals

- A vanilla `gumptionchain` node ships a working **verify** page and a public **provenance** JSON read out of the box.
- Establish a documented, idiomatic **base↔extension template-override seam** so `gumption-hub` (the reference consumer) re-skins base pages instead of reimplementing them.
- Eliminate the verify/provenance duplication currently in the hub.

## Non-goals

- Fleshing out home/explorer, blocks list, or subject opposition/support tally pages (future #189 work).
- Migrating the proof store or OG cards.
- Forcing the hub to drop its own wallet-JS vendoring (optional follow-up).

---

## Section 1 — The seam

### Approach chosen

**Flask-native blueprint templates + app-override.** The browser blueprint carries `template_folder` and `static_folder`. Flask searches *app* templates before *blueprint* templates, so a consumer re-skins by dropping its own `base.html` (and/or any page template) into its app-level `templates/`. Zero config, idiomatic, and matches how the hub is already structured.

Rejected alternatives: a configurable base-template name (`{% extends config['GC_BASE_TEMPLATE'] %}` — adds a config knob and indirection, doesn't help per-page overrides) and a Jinja `ChoiceLoader`/theme directory (more machinery than one reference consumer warrants).

### Structure

```
src/gumptionchain/
  browser.py        Blueprint('browser', __name__,
                      template_folder='templates',
                      static_folder='static',
                      static_url_path='/static/gumptionchain')
  templates/
    base.html       documented BLOCK CONTRACT + a plain default skin
    verify.html     {% extends "base.html" %}
    index.html / chains.html / block.html / transaction.html  (migrated to the contract)
  static/
    wallet/*.mjs    vendored from clients/wallet (source of truth) via a sync script
    js/verify-glue.mjs
```

The blueprint changes (`template_folder`, `static_folder`, `static_url_path`) are what make base self-contained **when embedded**. The consumer creates `Flask(__name__)`, so the *app's* template/static folders are the consumer's, not base's — without blueprint-carried folders, base's templates and assets are not found in an embedded app. `static_url_path='/static/gumptionchain'` namespaces base assets so they never clash with a consumer's own `/static`.

### Block contract (defined in base `base.html`, documented in `docs/`)

| Block | Purpose |
|---|---|
| `title` | page `<title>` |
| `head` | extra `<head>` (CSS/meta) |
| `nav` | navbar contents |
| `content` | main page body (renamed from today's `page_container`) |
| `footer` | footer contents |
| `scripts` | end-of-body JS |

### Extension-injection hook (optional include, NOT a block)

Jinja blocks can only be filled by a *descendant* template. An extension never creates a descendant of a base page template (a same-named override would self-recurse), so blocks cannot be used to inject extra content into a base page. The correct primitive is an **optional include**:

```jinja
{# base verify.html, near the bottom #}
{% include "verify/extra.html" ignore missing %}
```

Base ships no `verify/extra.html`, so it renders nothing. A consumer drops `templates/verify/extra.html` into its app templates and the base page includes it. `ignore missing` makes the hook optional. (Blocks remain the right tool for the *skin* override, where the consumer's `base.html` is a genuine parent of the page templates.)

### Override rules (the documented seam)

1. Re-skin everything → drop your own `base.html` in app `templates/` (app wins over blueprint).
2. Re-skin one page's body → drop a same-named page template (e.g. `index.html`); it renders on base's route with base's view data.
3. Inject into a page without replacing it → provide the page's optional include partial (e.g. `verify/extra.html`).
4. Add brand-new pages → your own blueprint (unchanged from today).
5. Shared-page links in your skin reference **base** endpoint names (e.g. `url_for('browser.verify_view')`).

Payoff: a consumer sets `register_browser=True`, overrides just `base.html`, and every base page — explorer, blocks, transactions, verify — inherits the skin at once.

---

## Section 2 — What migrates & where it lands

### New in base (`gumptionchain`)

| Component | Lands at | Notes |
|---|---|---|
| `lookup_provenance(txid)` | `src/gumptionchain/provenance.py` | Lifted verbatim from the hub; already pure base code (`node_lc_dao`, `lc.transaction_provenance`, `ChainDAO.pending_provenance`). Unauthed. |
| Public provenance route | `browser.py` → `GET /transaction/<mill_hash:txid>/provenance.json` | Sibling of the existing `/transaction/<txid>` HTML view; canonical path (the hub's `/tx/...` was ad-hoc). 404 → JSON `{"error": "transaction not found"}`. |
| `verify_view` | `browser.py` → `GET /verify` | Renders `verify.html`. Pure read; no DB writes. |
| `verify.html` | `templates/verify.html` | Pure verify only: paste attestation → 3-check verdict. Ends with `{% include "verify/extra.html" ignore missing %}`. |
| `verify-glue.mjs` | `static/js/verify-glue.mjs` | `hubFetchProvenance` → `fetchProvenance`, defaulting to base's canonical provenance path. Origin injectable for tests. |
| Wallet JS | `static/wallet/*.mjs` | Vendored from `clients/wallet/` via a sync script. Served via `browser.static`. |

### Explicitly NOT migrating (stays in hub)

`proofs.py`, `StoredProof`, `POST /proof`, `GET /proof/<hash>`, `og.py`, the OG route.

### Splitting verify from proof-store

The hub's current `verify.html` contains a "Create a demo proof & share it" block that POSTs to `/proof`. That section does **not** migrate. Base's `verify.html` is verify-only and exposes the optional `verify/extra.html` include. The hub re-skins verify and supplies `verify/extra.html` with its demo-proof/share UI — keeping its feature without owning the verify page.

### Path canonicalization

Base owns `/verify` and `/transaction/<txid>/provenance.json`. The hub's old `/tx/<txid>/provenance.json` and `hub.verify_page` go away; the hub's skin + landing relink to `browser.verify_view`.

---

## Section 3 — Provenance + verify data flow

### Public provenance read

```
GET /transaction/<mill_hash:txid>/provenance.json   (browser blueprint, unauthed)
  └─ provenance.lookup_provenance(txid)
       ├─ node_lc_dao() → longest chain
       ├─ lc.transaction_provenance(txid)        # canonical/confirmed txns
       └─ ChainDAO.pending_provenance(txid)       # mempool fallback
  → 200 {#176a provenance dict: status, confirmations, height, ...}
  → 404 {"error": "transaction not found"}
```

This is the same data base already serves authed at `/api/transaction/<txid>` and unauthed (as HTML) at `/transaction/<txid>` — no new trust boundary, just a JSON shape for client JS. The `mill_hash` route converter rejects a malformed txid before the lookup.

### Verify flow (client-side except the one fetch)

```
/verify page
  textarea(gc-msg-v1 proof) → verify-glue.runVerify(proof)
                                └─ gc-attestation.verifyStake(proof, { fetchProvenance })
     1. signature: verifyMessage(proof); sig.valid && sig.address == proof.address
     2. onchain:   fetchProvenance(claim.txid) → GET .../provenance.json; status == 'canonical'
                   (+ optional minConfirmations)
     3. consistent: signer == staker && claim matches on-chain outflow
  renderVerdict() lights the 3 checks + seal; reasons[] shown
```

- `fetchProvenance` defaults to base's `/transaction/<txid>/provenance.json` (origin injectable for tests).
- Failure reasons surface verbatim from `verifyStake`: `bad-signature` / `expired`; `txn-not-found` / `not-canonical` / `insufficient-confirmations`; `signer-not-staker` / `claim-mismatch`.
- No proof-store coupling: verify never writes. Base's verify page has no knowledge of `/proof`.

`verifyStake` already lives in base (`clients/wallet/gc-attestation.mjs`) and is fully self-contained; it only needs an injected `fetchProvenance`.

---

## Section 4 — Static assets & packaging

### Vendoring (source of truth stays `clients/wallet/`)

```
clients/wallet/*.mjs  ── scripts/sync_wallet.py ──►  src/gumptionchain/static/wallet/*.mjs
   (source of truth)       (excludes *.test.mjs, *-cli.mjs)        (vendored, shipped in wheel)
```

- Add `scripts/sync_wallet.py` to base — like the hub's, but `DEST = src/gumptionchain/static/wallet` and `--source` defaults to `.` (the source is the local `clients/wallet`).
- Runtime closure to vendor (driven by `verifyStake`'s imports): `gc-attestation`, `gc-message`, `gc-errors`, `gc-crypto`, `gc-sig`, `gc-wallet`, `index`.
- `static/js/verify-glue.mjs` is authored directly in base (not vendored — it is base's own glue).
- Vendored `.mjs` files are committed to git (not generated at build time), so the wheel is reproducible and the sync script is a dev convenience, not a build step.

### Serving (works embedded, not just standalone)

```python
blueprint = Blueprint(
    'browser', __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/static/gumptionchain',
)
```

Templates reference assets via `url_for('browser.static', filename='wallet/gc-attestation.mjs')` — resolves whether base runs standalone or embedded.

### CSP

Verify loads ES modules from `'self'` (base.static) and Bootstrap from `cdn.jsdelivr.net`; inline `<script type="module">` needs `'unsafe-inline'`. The existing CSP in `application.py` already allows `script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net`. **No CSP change needed.**

### Packaging

`uv_build` ships everything under `src/gumptionchain/` (that is why today's `templates/` works when installed), so the new `static/` dir is included in the wheel automatically — no packaging config change.

---

## Section 5 — Hub consumption, testing, PR breakdown

### Hub consumption changes (in `gumption-hub`)

- `create_hub_app`: flip `register_browser=False` → **`True`**. The hub gains base's verify, provenance, and explorer pages — all auto-re-skinned by the hub's existing `base.html`.
- Resolve the `/` collision the seam-way: **drop the hub's own `/` route**, override base's `index.html` template with the landing body (base's `index_view` supplies `lc`; the landing ignores it). Relink brand/nav: `hub.landing` → `browser.index_view`, `hub.verify_page` → `browser.verify_view`.
- Delete hub-side duplicates: `verify_page` route + `verify.html`, `verify-glue.mjs`, `provenance.py`, and the ad-hoc `/tx/<txid>/provenance.json` route. `proof_page` switches to `from gumptionchain.provenance import lookup_provenance`.
- Add `templates/verify/extra.html` = the hub's "sign a demo proof & POST /proof" UI (rides the include hook).
- Constraint that keeps the skin simple: base's `verify.html` injects only into `content` / `title` / `scripts` (blocks the hub's `base.html` already defines), so no skin changes beyond the link updates.

### Testing

**Base:**
- `/verify` renders (200, has the textarea).
- Provenance route → 200 dict for a known txn; 404 JSON for unknown; **no auth headers required**.
- `test_wallet_vendored` — required `.mjs` modules present (mirrors the hub's test); a missing vendor fails CI rather than 404ing at runtime.
- Light JS test for `verify-glue`'s adapter: 404 → `null`, `!ok` → throw, txid path-encoding.
- **Seam test:** a throwaway app with an app-level `base.html` re-skins a base page (proves app-overrides-blueprint resolution).

**Hub:**
- Update `test_verify_page` / `test_provenance` / `test_pages` for the flipped routes.
- Landing renders at `/` via the index override.
- `proof_page` still works against `gumptionchain.provenance`.

### PR breakdown (~4 PRs; base PRs land first — hub has a path dep on `../gumptionchain`)

1. **base — seam foundation:** blueprint `template_folder` / `static_folder` / `static_url_path`; normalize `page_container` → `content` across the 4 existing templates + `base.html`; `scripts/sync_wallet.py` + vendored wallet modules + `test_wallet_vendored`; seam docs. (Pure infra + rename; base standalone keeps working.)
2. **base — public provenance:** `provenance.py` + `GET /transaction/<txid>/provenance.json` + tests.
3. **base — verify:** `verify.html` + `verify_view` + `verify-glue.mjs` + the `include ignore missing` hook + verify/seam tests.
4. **hub — consume:** flip `register_browser=True`, relink skin, override `index.html`, add `verify/extra.html`, delete the duplicates, update tests. (In the `gumption-hub` repo.)

PRs 1–3 are in `gumptionchain`; PR 4 is in `gumption-hub` and depends on 1–3 being available via the local path dependency.

## Open questions / risks

- **Block-contract rename blast radius:** `page_container` → `content` touches base's 4 existing templates. Pure rename, low risk, covered by existing page-render tests. Contained in PR 1.
- **Hub skin block coverage:** the hub's `base.html` defines `title` / `og` / `content` / `scripts` but not `head` / `nav` / `footer` as blocks (nav/footer are hardcoded chrome). Base page templates must therefore confine themselves to `content` / `title` / `scripts` to render correctly under the hub skin. Verify complies; this is a documented constraint of the contract, and a conformant consumer skin should define all contract blocks (even if empty).
