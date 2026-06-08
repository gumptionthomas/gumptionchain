# EGU #203 — base UI: address holdings + mempool/pending views

**Date:** 2026-06-08
**Issue:** #203 (deferred from #196; on the EGU launch checklist #190)
**Status:** design approved

## Goal

Add the two "other basic chain views" deferred from #196, on the established
base↔extension seam (`docs/ui-extension-seam.md`): **address/wallet holdings**
(an addresses leaderboard index + a per-address detail page) and a
**mempool/pending** view of the unconfirmed-transaction pool. Brings the vanilla
node's default UI to full explorer coverage.

Out of scope: hub theming (EGU #5 / gumption-hub#8); any pending-txn detail page
(pending txns are ephemeral — the mempool is a link-free summary list); changing
the existing API/mempool prune semantics.

## Constraints (inherited from the seam)

- Every page `{% extends "base.html" %}` and uses ONLY the documented block
  contract (`title`/`head`/`nav`/`content`/`footer`/`scripts`).
- Semantic Bootstrap; optional `*/extra.html` include hooks.
- Reads stay unauthed/public. SQL query construction on `ChainDAO`/the model
  layer; shaping/delegation on the `Chain` domain dataclass.
- Reuse the shared `_pagination.html` macro.
- **Read-only:** the mempool browser view must NOT mutate the pool (the API
  `PendingTxnView` prunes expired on GET; the browser read filters expired in the
  query instead, no delete).

## Pages & routes

All on the existing `browser` blueprint.

| Route | View fn | Template | Purpose |
|---|---|---|---|
| `/addresses` | `addresses_view` | `addresses.html` | Addresses leaderboard ranked by live balance |
| `/address/<address:address>` | `address_view` | `address.html` | One address: balance + holdings + transactions |
| `/mempool` | `mempool_view` | `mempool.html` | Pending pool: txid, age, in/out counts, total out |

Navbar (`base.html`) gains **Addresses** and **Mempool** links. The home stats
strip (`index.html`) gains a **Pending** count.

Route notes:
- The `address` converter (`application.py`, `validate_address_format`) accepts a
  `GC…GC` base58/32-byte address; an unknown-but-valid address renders 200 with
  zero balance + empty lists (not 404); an invalid address string → 404.
- Mempool rows are deliberately link-free: pending txns live in `PendingTxnDAO`
  (not `TransactionDAO`), so `/transaction/<txid>` would 404 until mined.

## Data-layer additions

Most data already exists: `ChainDAO.wallet_leaderboard()` (rows `.address`,
`.ct`), `wallet_balance(address)`, `unspent_outflows(address)`,
`Chain.balance(address)`, `Chain.address_transactions(address)`,
`PendingTxnDAO.count()`/`json_datas()`, and `paginate_rows` (the
`_RowPagination` helper from #200). New:

### On `Chain` (`src/gumptionchain/chain.py`)

- `wallet_leaderboard(limit=None) -> Select[Any]` — thin delegate to
  `self.to_dao().wallet_leaderboard(limit)` (mirrors `subject_leaderboard`).
- `address_holdings(address) -> Select[tuple[OutflowDAO]]` —
  `self.to_dao().unspent_outflows(address)` with a stable
  `.order_by(OutflowDAO.amount.desc(), OutflowDAO.txid)` so paginated holdings
  are deterministic (`unspent_outflows` has no inherent order). `OutflowDAO` is
  already imported in `chain.py`.
- `Chain.address_transactions(address)` already exists; confirm it carries a
  deterministic ORDER BY (timestamp desc, then a tiebreak). If it does not, add
  the ordering at the point of use in the view (do not change the existing
  method's contract).

### On `PendingTxnDAO` (`src/gumptionchain/models.py`)

- `pending_q(expired: datetime | None = None) -> Select[tuple[PendingTxnDAO]]` —
  `db.select(PendingTxnDAO)` ordered by `received` desc (newest first); when
  `expired` is given, `where(PendingTxnDAO.timestamp >= expired)` (read-only
  expiry filter mirroring `json_datas(expired=...)`, open boundary). Paginatable
  via plain `db.paginate` (single ORM entity → `.scalars()` is correct here).

### Mempool row summary (view-level, no new SQL)

`mempool_view` paginates `pending_q(expired=expiry_cutoff(now()))`, then for each
`PendingTxnDAO` on the page parses `json_data` via `Transaction.from_json` to
derive: `txid`, `received`/`timestamp`, `len(inflows)`, `len(outflows)`,
`total_out = sum(o.amount for o in outflows)`. Computed inline from the domain
`Transaction` (≤ one page of rows). Home pending count: `PendingTxnDAO.count()`.

## `_pagination.html` macro enhancement (backward-compatible)

The address detail page renders **two independent paginated lists** (holdings +
transactions) on a route that also has a **path arg** (`address`). Extend the
macro signature to:

```jinja
{% macro render_pagination(page, endpoint, page_param='page') -%}
```

and build each link as
`url_for(endpoint, **request.view_args, **_other_args, **{page_param: p})`,
where `_other_args` is the current query string (`request.args`) minus
`page_param`. This:
- lets holdings use `page` and transactions use `txn_page` independently,
- preserves the path arg (`address`) and the sibling list's current page across
  clicks,
- is backward-compatible: `blocks`/`subjects`/`chains` pass no `page_param`
  (defaults to `page`) and have empty `view_args`, so their links are unchanged
  (they incidentally gain correct `per_page` preservation).

The views read the two pages explicitly:
`db.paginate(holdings_q)` (default `page`) and
`db.paginate(txns_q, page=request.args.get('txn_page', 1, type=int))`.

## Templates & seam conformance

- `addresses.html`, `address.html`, `mempool.html` extend base, fill only
  `content`; Bootstrap cards/tables matching `subjects.html`/`blocks.html`.
- Optional include hooks: `{% include "addresses/extra.html" ignore missing %}`
  on the addresses index, `{% include "mempool/extra.html" ignore missing %}` on
  mempool.
- **Addresses index:** rank, address (links `/address/<addr>`), balance (`ct`
  grains). `render_pagination(addresses_page, 'browser.addresses_view')`. Empty:
  "No addresses with a balance yet."
- **Address detail:** balance header; a paginated **holdings** card (unspent
  outflows: amount + staking/recipient txn link to `/transaction/<txid>`,
  `page_param='page'`); a paginated **transactions** card (txns created by the
  address: txid link + timestamp, `page_param='txn_page'`). Empty sections show
  "none".
- **Mempool:** a count header ("N pending"), a table of txid, age (`received` /
  `timestamp | utc_datetime`), inflows, outflows, total out (grains).
  `render_pagination(pending_page, 'browser.mempool_view')`. Empty: "Mempool is
  empty."
- **Home (`index.html`):** add a "Pending" stat card to the existing strip,
  fed by `pending_count` passed from `index_view`.

## Data flow

```
view -> Node(...).longest_chain : Chain            (address pages)
     -> Chain.wallet_leaderboard()/address_holdings()/address_transactions()/balance()
     -> ChainDAO.<query> : Select  ->  paginate_rows | db.paginate | scalar
view -> PendingTxnDAO.pending_q(expired=...) : Select   (mempool)
     -> db.paginate -> [Transaction.from_json(row.json_data) for row in page]
template -> url_for('browser.*'), | utc_datetime, gc_version
```

Views follow the existing error contract (catch `HTTPException` → return; catch
`Exception` → `current_app.logger.exception(e); abort(500)`).

## Testing

Per PR:
- **View tests:** 200 + key markup; empty states; unknown-but-valid address →
  200 zeros; invalid address → 404; multi-page (`?page` and, for address detail,
  `?txn_page`) exercising the macro/pagination.
- **Data-layer unit tests:** `wallet_leaderboard` rows/ordering via the `Chain`
  delegate; `address_holdings` ordering; `pending_q` ordering + expiry filter
  (an expired pending txn is excluded read-only, and NOT deleted).
- **Seam tests** (`test_ui_seam.py`): app-`base.html` override re-skins each new
  page.

Hard CI gates: `ruff check`, `ruff format --check`, `mypy --strict`, `pytest`.

## PR sequence (two sequential, each off fresh main)

0. **docs** — this spec + the plan (matches the #196/#186 docs-PR precedent).
1. **PR 1 — Addresses.** `Chain.wallet_leaderboard` + `address_holdings` (+
   confirm `address_transactions` ordering), the `_pagination.html` macro
   enhancement, `/addresses` + `/address/<addr>` views/templates, nav link,
   tests + seam tests.
2. **PR 2 — Mempool.** `PendingTxnDAO.pending_q`, `/mempool` view/template, nav
   link, the home **pending count** (touches `index_view` + `index.html`), tests
   + seam test.

## Follow-ups (not in scope)

- Pending-txn detail page, if ever wanted (would parse `json_data`).
- Extending `/transaction/<txid>` to fall back to the pending pool.
