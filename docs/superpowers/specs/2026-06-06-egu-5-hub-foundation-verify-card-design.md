# EGU #5 / gumption-hub — foundation + verify card — design

**Date:** 2026-06-06
**Status:** Approved — ready for implementation planning
**Issues:** #155 (EGU #5 hub, foundation) + #176c (verify page + Bluesky OG unfurl,
the third slice of the verifiable stake card #176, EGU #3)
**Type:** New repository (`gumption-hub`) that embeds `gumptionchain` as a package
and runs as a non-milling node with its own UI. No change to gumptionchain
consensus, schema, or peer protocol.

## Summary

Stand up **`gumption-hub`** — a new repo that is simultaneously the **EGU front
door** (gumption.com) and the **canonical, non-milling GumptionChain node** — and
ship its first real feature: the **"Verified on GumptionChain" verify card**
(#176c), which turns a signed stake attestation (#176b) into a shareable,
independently-verifiable claim that unfurls on Bluesky.

The hub embeds `gumptionchain` the way **thecancelbutton** embedded `cancelchain`:
import the package, build on the app factory, suppress the default chain UI, and
add the hub's own UI/UX on top. Because the hub *is* a node, the verify card reads
the canonical chain from its **own** local node (no third-party trust, no service
hop).

This sub-project is the foundation (#5a) + the verify experience (#5b/#176c).
The chain explorer re-skin (#5c), wallet hosting (#5d), and handle binding (#5e)
are deferred to their own cycles.

## The two personalities (one app)

GumptionChain is the connective tissue of the EGU, so one app wears two hats:

- **EGU hub** — the universe front door: landing, about, links to member projects
  (Too Big To Fail / 2b2f, future games).
- **GumptionChain node** — a full non-milling node: it syncs the chain from miller
  peers, serves the `/api/*` peer protocol, and exposes human-facing chain views
  (explorer, deferred to #5c).

This dual use is acceptable precisely because the chain is the nerve that ties the
EGU together.

## Architecture — embedding gumptionchain

`gumptionchain.create_app` already exposes the exact seam this needs:

```python
def create_app(app=None, config_map=None, register_browser=True): ...
```

The hub's app factory:

```python
# gumption_hub/app.py  (illustrative)
from flask import Flask
from gumptionchain import create_app
from gumption_hub.hub import hub_blueprint

def create_hub_app():
    app = Flask(__name__)                          # hub owns templates/static/UI
    create_app(app=app, register_browser=False)    # node bones; default chain UI off
    app.register_blueprint(hub_blueprint)          # /, /about, /verify, /proof, POST /proof
    return app
```

- **`app=...`** — the hub passes its *own* `Flask` instance, so its templates,
  static dir, and `2b2f`-derived design system are the app root.
- **`register_browser=False`** — the bare-Bootstrap node UI is off from day one;
  the hub owns 100% of the human UI.
- **`/api/*` is untouched** — the node's peer protocol routes (gossip with
  millers, the #176a `/api/transaction/<txid>` provenance endpoint) stay exactly
  where peers expect them. Only the *HTML* surface is the hub's.

**Node configuration (deployment, not code):** non-milling (no miller address in
this node's role config), `GC_PEERS` = the miller nodes, a real database
(Postgres) rather than SQLite. The hub never mills; it is a read-replica + UI +
provenance store.

**Dependency:** `gumption-hub` depends on `gumptionchain` as a package — a **uv
path dependency** (`../gumptionchain`) for local dev, a **git dependency** (pinned)
for CI/deploy. No fork, no vendoring.

## Repository & workflow

- **New repo:** `/home/gumptionthomas/Development/gumption-hub`, pushed to
  `gumptionthomas/gumption-hub` via `gh repo create`. Its own `CLAUDE.md`,
  `pyproject.toml` (uv + uv_build), `.gitignore`, ruff/mypy config mirroring
  gumptionchain's gates, and `migrations/`.
- **Spec & plan live here** in `gumptionchain/docs/superpowers/` (where all EGU
  history lives). **Implementation Task 1 scaffolds the new repo**; the iterative
  build then runs in a Claude Code session **rooted in `gumption-hub`** so the new
  repo's own conventions/permissions govern.

## Design system — 2B2F, ported

The hub adopts the **2B2F ("Too Big To Fail") design language** from `acquire-llm`
(`src/acquire_llm/static/css/main.css` + its `base.html`). Both are Flask +
Bootstrap 5 + static CSS + zero-npm, so this is a faithful **port**, not a
cross-framework translation.

- **Foundation:** Bootstrap 5.3.3 (CDN), `data-bs-theme` light/dark with the
  no-flash inline script, Google Fonts **Inter** (body 400/600/700) + **Righteous**
  (display/brand).
- **Tokens:** `--gold #d4a520`, `--gold-dark #b8960c`, `--gold-light #e8c547`,
  `--gold-deep #6b5608` (AA text); paper `#fbf8f0`, ink `#2a2620`.
- **Components/motifs:** `btn-gold`/`btn-outline-gold`, `modal--2b2f`, segmented
  buttons, taupe `badge-muted`, engraved hairline frames (`::before` inset border),
  gold 135° gradients, **wax-seal / stamp** motif (Righteous, letterspaced), and
  `font-variant-numeric: tabular-nums` for amounts/heights.
- **Hub additions:** the **stake certificate card** (see below) and the verify
  panel, built in this same language.

A `gumption-hub`-owned stylesheet carries the ported base layer plus the
hub-specific pieces; the bundled Righteous/Inter font files are used by the OG
image renderer (via fontconfig).

## Vendoring the wallet module

`/verify` and `/proof/<hash>` run `verifyStake` in the browser, which needs the
runtime wallet ESM (`gc-attestation.mjs`, `gc-message.mjs`, `gc-errors.mjs`,
`gc-wallet.mjs`, `gc-crypto.mjs`, `gc-sig.mjs`, `index.mjs`). These live under
`gumptionchain/clients/wallet/`, which is **not** part of the importable
`gumptionchain` wheel — so the hub cannot import them from the package. The hub
**vendors** the runtime `.mjs` (excluding `*.test.mjs` and `*-cli.mjs`) into
`gumption-hub`'s static dir via a small **sync script** (copies from the
`../gumptionchain` checkout), with a test asserting the expected files are present.
Parity is already locked by gumptionchain's own suite; the hub only serves the
files. (A future cleanup could ship the wallet as package data or its own package;
out of scope here.)

## Routes & information architecture (integrated, flat)

Chain views are first-class top-level citizens; the "GumptionChain is one EGU
project" framing lives in the nav + landing narrative, **not** in a URL prefix.

| Route | Personality | This sub-project? |
|---|---|---|
| `/` | EGU landing (front door) | ✅ (minimal) |
| `/about` | what the EGU is; links to members | ✅ (minimal) |
| `/verify` | paste & check any proof | ✅ |
| `/proof/<hash>` | shareable verified card + OG unfurl | ✅ |
| `POST /proof` | submit a proof; returns `/proof/<hash>` | ✅ |
| `GET /tx/<txid>/provenance.json` | **public** provenance read (client `fetchProvenance`) | ✅ |
| `/explorer`, `/block/<hash>`, `/tx/<txid>`, `/chains` | chain explorer UI | ⬜ #5c |
| `/subject/<s>`, `/leaderboard`, `/gumption/<addr>` | canonical chain UI | ⬜ #5c |
| `/api/*` | node peer protocol | inherited, **untouched** |

Notes: `/` is the EGU front door (today's chain index would move to `/explorer`,
which is #5c). `/tx` is the short, share-friendly transaction path. The proof
page's "View transaction" link is **forward-wired** to `/tx/<txid>` and goes live
in #5c (per the agreed "chain-browser links come later").

## The verify card

### The stake attestation (recap, from #176b)

A gc-msg-v1 proof whose `message` is the canonical JSON of a stake claim
`{txid, kind, subject|address, amount, handle?}`. `verifyStake` (JS + Python,
already shipped in #185) composes three checks: **signature**, **on-chain**
(via injected `fetchProvenance`), **consistency**.

### Card composition — "Ledger receipt" (chosen)

A gold-dossier record: a header band (`✓ Verified on GumptionChain`, Righteous,
gold-tint gradient, seal dot) over labeled rows — **Claim** (kind · amount GRIT ·
subject), **Signer** (handle + truncated address), **Transaction** (txid · block
height). Opposition carries a subtle brick accent (`#9c4b3b`); GRIT is the display
unit (grains ÷ 100), amounts tabular-nums. The static OG image renders exactly
these immutable rows; the **live confirmation depth** appears only in the page's
live-verify panel (see below), never in the cached image.

### `/proof/<hash>` — the page a Bluesky click opens

1. **Human share line** — the natural-language statement
   (*"I put 3 GRIT in opposition to 'goblins.'"*).
2. **The ledger card** (the OG snapshot's live twin).
3. **"Verified live in your browser"** panel — `verifyStake` re-runs client-side
   against the hub's own node and lights the three sub-checks (Signature /
   On-chain / Consistent) green, each with a plain-English explanation. The seal
   may start neutral and light up once the live check passes.
4. **Actions** — View transaction (→ `/tx/<txid>`, live in #5c), Verify it
   yourself (→ `/verify`), Share.
5. **Raw signed proof** (collapsible) for skeptics; a footer stating anyone can
   verify independently with the wallet module + any node.

### `/verify` — paste & check

A standalone page: paste any proof JSON → the same client-side `verifyStake` → the
same verdict/checks UI. Optional "save & share" → `POST /proof` to mint a
`/proof/<hash>` link.

## Data flow

1. **Create** — the wallet demo (`clients/wallet/passkey-wallet-demo.html`, in
   gumptionchain) builds + signs a stake attestation with the existing
   `gc-attestation.mjs`, then `POST /proof {proof}`.
2. **Store** — the hub runs `parse_stake_attestation(proof)` (reject malformed →
   400), computes the **content hash** (sha256 over the canonical proof JSON),
   and inserts idempotently. Returns `{ id: <hash>, url: /proof/<hash> }`.
3. **Share** — the poster drops `gumption.com/proof/<hash>` into a Bluesky post.
4. **Unfurl** — Bluesky's crawler GETs `/proof/<hash>`; the server renders HTML
   with OG meta (`og:title`, `og:description`, `og:image`, `twitter:card=
   summary_large_image`). `og:image` = `/proof/<hash>/og.png`.
5. **Visit** — a human GETs `/proof/<hash>`; the page renders the card and loads
   the wallet ESM, which runs
   `verifyStake(proof, { fetchProvenance: txid => GET /tx/<txid>/provenance.json })`
   → the three checks resolve live.

The verify **engine** is unchanged from #176b; the hub provides the *transport
adapter* (`fetchProvenance` → same-origin `/tx/<txid>/provenance.json`, mapping
404 → `null`, leaving genuine transport errors to propagate) and the *UI*.

**Why a hub public provenance endpoint, not `/api/transaction/<txid>`:** the
node's `/api/transaction/<txid>` is `authorize_reader` (gc-sig-authenticated), so a
wallet-less public visitor cannot call it. The hub — which *is* the node, in the
same process — exposes an **unauthenticated** `GET /tx/<txid>/provenance.json` that
performs the identical lookup in-process (`node_lc_dao()` →
`lc.transaction_provenance(txid)`, falling back to
`ChainDAO.pending_provenance(txid)`) and returns the same JSON. This is the public
read surface the hub legitimately owns (the explorer in #5c uses it too); the
permissioned `/api/*` peer protocol is untouched.

## Storage model

A hub-owned table (its own migration; does not touch the chain schema):

```
stored_proof(
  content_hash  TEXT PRIMARY KEY,   -- sha256 of canonical proof JSON
  proof_json    TEXT NOT NULL,      -- the exact submitted proof
  txid          TEXT NOT NULL,      -- claim.txid, for indexing
  created_at    TIMESTAMP NOT NULL
)
```

- **Content-addressed** → idempotent (same proof = same id, dedup), no id
  enumeration. The `content_hash` is `sha256` over a **canonical encoding of the
  proof envelope** — the same canonical-JSON discipline the wallet already uses
  (fixed key order, compact separators, no whitespace), so the hash is
  deterministic and identical whether the proof was minted in JS or Python.
  `proof_json` persists the submitter's exact bytes for re-verification.
- **Anti-spam:** a request size cap on `POST /proof` + a per-IP reverse-proxy
  rate limit, consistent with the open-transacting posture (a heavier submit-PoW
  is out of scope, mirroring the chain's #151 stance).

## OG image generation

Server-rendered PNG at **1200×630**, produced the way acquire-llm does it
(`src/acquire_llm/og.py`): a **Jinja SVG template** (`og/proof.svg.jinja`,
the ledger card in vector form) → rasterized to PNG via **`cairosvg`**
(`cairosvg.svg2png(bytestring=svg, output_width=1200, output_height=630)`). Fonts
(Righteous + Inter) resolve by family name through **fontconfig** — cairosvg/Pango
ignore inline `@font-face`, so the deploy image installs the vendored font files
into the system font path and runs `fc-cache` (mirroring acquire-llm's Dockerfile).
`cairosvg` is a new hub dependency (it is *not* a gumptionchain dependency). The
PNG is **lazy-generated and cached to disk** (atomic temp-write-then-rename, as in
`render_recap_png`) and, because it is keyed by the immutable content hash, never
needs regeneration. To stay genuinely immutable, the OG image renders
**only immutable facts** — the claim (kind · amount · subject), the signer, the
txid, the block height, and the "Verified on GumptionChain" mark — and **omits the
live confirmation count** (which grows over time). It is therefore a pure function
of the stored proof and can be **content-addressed and cached immutably**
(`Cache-Control: immutable`). The **live confirmation depth** is rendered only in
the page's live-verify panel, never baked into the static image. The page's OG meta
points at `/proof/<hash>/og.png`.

## Error handling

- `GET /proof/<unknown>` → **404**, styled in the 2B2F language.
- `POST /proof` with a malformed/invalid proof → **400** (`BadAttestationError`),
  no storage.
- Verify outcomes are **data, not exceptions**: failed checks render as red rows +
  reasons (`bad-signature`, `not-canonical`, `claim-mismatch`, …). Only
  structurally malformed input throws — mirroring `verifyStake`.
- `fetchProvenance` **transport** errors surface distinctly as "couldn't reach the
  node," never silently collapsed into "txn-not-found" (which would mark a real
  canonical stake unverifiable).

## Testing

- **Python (`uv run pytest` in gumption-hub):**
  - the hub app boots with an embedded gumptionchain node (test config);
  - `POST /proof` stores + content-addresses + dedups; malformed → 400;
  - `GET /proof/<hash>` renders the expected OG meta tags; `GET
    /proof/<hash>/og.png` returns a valid PNG of the right dimensions; unknown
    hash → 404;
  - the `fetchProvenance` adapter maps 404 → `null` and propagates other errors.
  - The `verifyStake` engine itself is already covered by #176b — not re-tested
    here.
- **JS (`node --test`):** the page's small client glue (proof → `verifyStake` →
  DOM verdict) is unit-tested against a fake provenance, reusing #176b fixtures.
- **Manual / demo:** extend `MANUAL-VERIFICATION.md` (and the wallet demo) for the
  end-to-end flow — sign → submit → share link → unfurl preview → live verify.
- **CI gates** mirror gumptionchain: `ruff check` + `ruff format --check`, `mypy`,
  pytest; zero npm.

## Out of scope

- Chain explorer re-skin — `/explorer`, `/block`, `/tx`, `/chains`, and the
  canonical chain UI (tallies, leaderboards, "your gumption") — **#5c**.
- Wallet hosting + management page beyond the demo hook — **#5d**.
- Handle-ownership (AT-Protocol bidirectional) verification — **#5e / #176 L3**.
- A node `/verify` endpoint (rejected in #176b: verification is client-side).
- Submit-PoW anti-spam (deferred, mirrors chain #151).

## Decisions log

- **Hub stack = Python/Flask, and the hub *is* a non-milling node** — same stack as
  the chain; the verify card reads provenance from its own local node (no
  third-party trust, no service hop). [user]
- **New repo embedding gumptionchain (the thecancelbutton pattern)** — reconciles
  "separate repo" with "is a node"; website churn stays out of the
  consensus-critical repo; chain code is reused, not forked. [user precedent]
- **Server-stored, content-addressed proofs** — clean short share links, idempotent
  dedup, no id enumeration; spam-bounded by content-addressing + size cap + proxy
  rate limit.
- **Minimal creation scope** — POST endpoint + wallet-demo hook now; polished
  "share your stake" UI deferred to #5d.
- **Card B (ledger receipt)** in the 2B2F gold-dossier language; opposition brick
  accent; GRIT display unit, tabular-nums.
- **Integrated flat URLs** — chain views are top-level (`/explorer`, `/tx/<txid>`),
  not nested under `/chain` (which stuttered); EGU-project framing lives in nav +
  landing. `/api/*` never moves.
- **`/verify` + `/proof/<hash>` top-level** (not under a chain prefix) — short
  share links + provenance is an EGU-wide concern.
- **Landing/about minimal this round** — the headline is the verify card; the EGU
  shell is fleshed out later.
- **OG via SVG-Jinja → cairosvg** (not Pillow) — matches acquire-llm's actual
  technique (`og.py`); vector card B is easy to author and rasterize; fonts via
  fontconfig.
- **Public in-process provenance endpoint** — `/api/transaction/<txid>` is
  gc-sig-authed; the hub serves a wallet-less public read for the verifier and the
  future explorer, leaving the peer protocol untouched.
- **Vendor the wallet ESM** — `clients/wallet/*.mjs` aren't in the gumptionchain
  wheel; the hub syncs the runtime modules into its static dir (sync script + a
  presence test).

## Definition of done

- `gumption-hub` repo exists (own `CLAUDE.md`, `pyproject.toml` with a
  gumptionchain dependency, ruff/mypy/pytest gates, migrations), boots as a
  non-milling node with `register_browser=False`, and serves the 2B2F-styled
  shell (`/`, `/about`) + footer/nav.
- The verify experience works end-to-end: `POST /proof` (store +
  content-address + dedup + 400 on bad input), `GET /proof/<hash>` (OG meta + live
  `verifyStake`), `GET /tx/<txid>/provenance.json` (public in-process provenance
  read), `GET /proof/<hash>/og.png` (1200×630 SVG→PNG via cairosvg), `GET /verify`
  (paste & check). 404 for unknown proofs.
- Tests (Python + JS glue) pass; ruff/mypy clean; zero npm.
- `MANUAL-VERIFICATION.md` + the wallet demo updated for the full share flow.
- No change to gumptionchain consensus, schema, or `/api/*` peer protocol. Part of
  #155 (#5a/#5b) and #176c.
