# EGU #203 address holdings + mempool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the address-holdings pages (addresses leaderboard + per-address detail) and a mempool/pending view to the vanilla node's base browser UI, on the #189 seam.

**Architecture:** New routes on the existing `browser` blueprint render new `{% extends "base.html" %}` templates. Reuses existing `ChainDAO`/`Chain` query methods + the `paginate_rows` (`_RowPagination`) helper; adds thin `Chain` delegates, one `PendingTxnDAO.pending_q` Select, and a backward-compatible `_pagination.html` macro enhancement (per-list page param + arg preservation). All reads unauthed/public and read-only.

**Tech Stack:** Flask + Jinja2 + Bootstrap 5, SQLAlchemy 2.0 `Select`/`db.paginate`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-08-egu-203-address-mempool-design.md`

**Reference patterns (read first):**
- `src/gumptionchain/browser.py` — view error contract; `longest_chain()`; `paginate_rows(select)` + `_RowPagination` (for multi-column rows); the `subjects_view`/`subject_view` pair (the address pages mirror them).
- `src/gumptionchain/models.py` — `ChainDAO.wallet_leaderboard` (rows `.address`,`.ct`; `:800`), `unspent_outflows` (`:734`), `wallet_balance` (`:747`); `Block.ancestry_transactions_q` orders `timestamp desc, id` so `address_transactions` is already ordered; `PendingTxnDAO` (`:1135`) with `count()` (`:1163`), `json_datas` (`:1169`).
- `src/gumptionchain/chain.py` — delegation pattern (`balance`, `subject_leaderboard`); `address_transactions` (`:1088`); `OutflowDAO` already imported.
- `src/gumptionchain/block.py` — `expiry_cutoff`; `src/gumptionchain/util.py` — `now`.
- `src/gumptionchain/transaction.py` — `Transaction.from_json` (`:322`), `.inflows`, `.outflows` (each Outflow has `.amount`), `.txid`, `.timestamp_dt`.
- `src/gumptionchain/templates/_pagination.html` (current macro), `subjects.html`/`subject.html`/`blocks.html` (look + macro usage).
- Tests: `tests/test_subjects_page.py`, `tests/test_subject_leaderboard.py`, `tests/test_provenance_public.py` (mining/staking via `mill_block` + `ApiClient`), `tests/test_ui_seam.py` (seam test shape). To create a confirmed transfer to an address for wallet tests, see how existing tests build outflows to an address (e.g. `create_outflow`/transfer helpers in `chain.py`/tests); a coinbase reward already credits the miller wallet's address, so a freshly milled chain has at least the miller address holding a balance.

---

## PR 1 — Addresses (leaderboard + detail)

Branch: `feat/egu-203-addresses` off fresh `main`.

### Task 1: `_pagination.html` macro enhancement (TDD)

**Files:**
- Modify: `src/gumptionchain/templates/_pagination.html`
- Test: `tests/test_pagination_macro.py` (new) — or extend `tests/test_blocks_page.py`

- [ ] **Step 1: Write failing tests** (render the macro via `render_template_string` inside `app.test_request_context('/address/GCxxGC?page=1&txn_page=2')`):
  - default `page_param='page'` still renders `?page=` links (back-compat).
  - `page_param='txn_page'` renders links that change `txn_page` while preserving the existing `page` query arg and any path args.
  - single page (`pages <= 1`) renders nothing.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Rewrite the macro** to accept `page_param` and preserve other args:

```jinja
{% macro render_pagination(page, endpoint, page_param='page') -%}
{%- if page.pages > 1 %}
{%- set base = request.view_args | default({}, true) %}
{%- set extra = request.args.to_dict() %}
{%- set _ = extra.pop(page_param, none) %}
<ul class="pagination">
  <li class="page-item {{ '' if page.has_prev else 'disabled' }}">
    <a class="page-link" href="{{ url_for(endpoint, **base, **extra, **{page_param: page.prev_num}) }}">Previous</a>
  </li>
  {%- for p in page.iter_pages() %}
  {%- if p %}
  <li class="page-item {{ 'active' if p == page.page else '' }}">
    <a class="page-link" href="{{ url_for(endpoint, **base, **extra, **{page_param: p}) }}">{{ p }}</a>
  </li>
  {%- else %}
  <li class="page-item disabled"><span class="page-link">…</span></li>
  {%- endif %}
  {%- endfor %}
  <li class="page-item {{ '' if page.has_next else 'disabled' }}">
    <a class="page-link" href="{{ url_for(endpoint, **base, **extra, **{page_param: page.next_num}) }}">Next</a>
  </li>
</ul>
{%- endif %}
{%- endmacro %}
```

> `request.args.to_dict()` gives a mutable plain dict; `.pop(page_param, none)`
> drops the current page param so the override wins. The requirement: links
> carry `request.view_args` + all current query args except `page_param`, plus
> the overridden `page_param`. Verify the existing `blocks`/`subjects`/`chains`
> pages still paginate (their `view_args` are empty and they pass no
> `page_param`), and that `?per_page=` is now preserved across page links.

- [ ] **Step 4: Run** new macro tests + `tests/test_blocks_page.py` + `tests/test_subjects_page.py` → PASS.

- [ ] **Step 5: Commit** — `feat(browser): per-list page param + arg preservation in pagination macro`.

### Task 2: `Chain.wallet_leaderboard` + `address_holdings` (TDD)

**Files:**
- Modify: `src/gumptionchain/chain.py`
- Test: `tests/test_address_pages.py` (new)

- [ ] **Step 1: Failing tests** — on a freshly milled chain (coinbase credits the miller wallet), `Chain.wallet_leaderboard()` returns rows including the miller address with `.ct == its balance`; `Chain.address_holdings(addr)` returns that address's unspent outflows ordered by amount desc; both agree with `Chain.balance(addr)`.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** on `Chain` (after `balance`):

```python
def wallet_leaderboard(self, limit: int | None = None) -> Select[Any]:
    return self.to_dao().wallet_leaderboard(limit=limit)

def address_holdings(self, address: str) -> Select[tuple[OutflowDAO]]:
    return (
        self.to_dao()
        .unspent_outflows(address)
        .order_by(OutflowDAO.amount.desc(), OutflowDAO.txid)
    )
```

> `Select` and `OutflowDAO` are already imported in `chain.py`. `address_transactions` already exists and is ordered (`timestamp desc, id`) — no change.

- [ ] **Step 4: Run** → PASS. `uv run mypy`.

- [ ] **Step 5: Commit** — `feat(chain): wallet_leaderboard delegate + ordered address_holdings`.

### Task 3: Addresses index view + template (TDD)

**Files:**
- Modify: `src/gumptionchain/browser.py`, `base.html` (nav)
- Create: `src/gumptionchain/templates/addresses.html`
- Test: `tests/test_address_pages.py`

- [ ] **Step 1: Failing test** — `/addresses` 200; empty chain → "No addresses with a balance yet"; freshly milled chain → miller address present + a `/address/` link + balance number.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Add view** (mirrors `subjects_view`, uses `paginate_rows` for the multi-column leaderboard rows):

```python
@blueprint.route('/addresses')
def addresses_view() -> Any:
    try:
        lc = longest_chain()
        addresses_page = (
            paginate_rows(lc.wallet_leaderboard()) if lc is not None else None
        )
    except HTTPException as e:
        return e
    except Exception as e:
        current_app.logger.exception(e)
        abort(500)
    return render_template(
        'addresses.html', title='Addresses', addresses_page=addresses_page
    )
```

- [ ] **Step 4: Create `addresses.html`** — extends base; card "Addresses"; `{% include "addresses/extra.html" ignore missing %}`; table rank / address (`<a href="{{ url_for('browser.address_view', address=row.address) }}">{{ row.address }}</a>`) / balance (`row.ct` grains); `{{ render_pagination(addresses_page, 'browser.addresses_view') }}`; empty/None → "No addresses with a balance yet." Row rank uses `loop.index + (addresses_page.page - 1) * addresses_page.per_page` (as in `subjects.html`).

- [ ] **Step 5: Nav link** in `base.html` after Subjects: `<a class="navbar-nav" href="{{ url_for('browser.addresses_view') }}">Addresses</a>`.

- [ ] **Step 6: Run** → PASS. **Commit** — `feat(browser): addresses leaderboard page`.

### Task 4: Address detail view + template (TDD, dual pagination)

**Files:**
- Modify: `src/gumptionchain/browser.py`
- Create: `src/gumptionchain/templates/address.html`
- Test: `tests/test_address_pages.py`

- [ ] **Step 1: Failing test** — `/address/<miller-addr>` 200 shows balance + at least one holding (coinbase outflow) linking to `/transaction/<txid>`; an unknown-but-valid address → 200 with zero balance + "none"; an invalid address string → 404; `?txn_page=2` is accepted (200).

> To get a valid address string in tests: use the miller wallet's `.address`
> (the `miller_wallet`/`wallet` fixtures expose `.address`). For the unknown
> case, use another `Wallet().address`.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Add view:**

```python
@blueprint.route('/address/<address:address>')
def address_view(address: str) -> Any:
    try:
        lc = longest_chain()
        if lc is None:
            balance = 0
            holdings_page = txns_page = None
        else:
            balance = lc.balance(address)
            holdings_page = db.paginate(lc.address_holdings(address))
            txns_page = db.paginate(
                lc.address_transactions(address),
                page=request.args.get('txn_page', 1, type=int),
            )
    except HTTPException as e:
        return e
    except Exception as e:
        current_app.logger.exception(e)
        abort(500)
    return render_template(
        'address.html',
        title=f'Address: {address}',
        address=address,
        balance=balance,
        holdings_page=holdings_page,
        txns_page=txns_page,
    )
```

> Import `request` from flask (add to the existing flask import line).

- [ ] **Step 4: Create `address.html`** — extends base; header with `{{ address }}` and balance (grains); a **Holdings** card: table of `holdings_page.items` (amount + `<a href="{{ url_for('browser.transaction_view', txid=flow.txid) }}">{{ flow.txid }}</a>`), `{{ render_pagination(holdings_page, 'browser.address_view', 'page') }}`, empty → "none"; a **Transactions** card: table of `txns_page.items` (txid link + `timestamp | utc_datetime`), `{{ render_pagination(txns_page, 'browser.address_view', 'txn_page') }}`, empty → "none". Guard the whole body on `holdings_page is not none` (chain present) else show balance 0 + "none".

- [ ] **Step 5: Run** → PASS. **Commit** — `feat(browser): per-address holdings + transactions detail page`.

### Task 5: Seam tests + gates

**Files:** Modify `tests/test_ui_seam.py`

- [ ] **Step 1:** Add seam tests for `/addresses` (empty → `SKINNED` + "No addresses with a balance yet") and `/address/<valid-addr>` (→ `SKINNED` + 200), mirroring the existing `test_consumer_base_html_reskins_*` tests.

- [ ] **Step 2: Full gates** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest` → green.

- [ ] **Step 3: Commit + open PR** — `test(browser): seam coverage for address pages`, push, `gh pr create`.

---

## PR 2 — Mempool / pending

Branch: `feat/egu-203-mempool` off fresh `main` (after PR 1 merges).

### Task 6: `PendingTxnDAO.pending_q` (TDD)

**Files:**
- Modify: `src/gumptionchain/models.py` (on `PendingTxnDAO`, near `json_datas`)
- Test: `tests/test_mempool_page.py` (new)

- [ ] **Step 1: Failing test** — add two pending txns to the pool (post via `ApiClient(host, wallet).post_transaction(txn)` WITHOUT milling, so they stay pending; or use `node.pending_txns.add(txn)`), assert `db.session.scalars(PendingTxnDAO.pending_q()).all()` returns both ordered by `received` desc; with `expired=<future cutoff>` an old-timestamp txn is excluded; assert `pending_q(expired=...)` does NOT delete rows (count unchanged after the query).

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement:**

```python
@classmethod
def pending_q(
    cls,
    expired: datetime.datetime | None = None,
) -> Select[tuple[PendingTxnDAO]]:
    stmt = db.select(cls)
    if expired is not None:
        # open-boundary expiry, read-only (no prune): keep timestamp >= cutoff
        stmt = stmt.where(cls.timestamp >= expired)
    return stmt.order_by(cls.received.desc(), cls.txid)
```

> `received` is nullable (`Mapped[datetime | None]`); the column has a default of
> utcnow on insert, so real rows have it set. Tie-break on `txid` for stability.

- [ ] **Step 4: Run** → PASS. `uv run mypy`.

- [ ] **Step 5: Commit** — `feat(models): PendingTxnDAO.pending_q paginatable read-only select`.

### Task 7: Mempool view + template (TDD)

**Files:**
- Modify: `src/gumptionchain/browser.py`, `base.html` (nav)
- Create: `src/gumptionchain/templates/mempool.html`
- Test: `tests/test_mempool_page.py`

- [ ] **Step 1: Failing test** — `/mempool` 200; empty pool → "Mempool is empty"; with a pending txn → its txid present, an inflow/outflow count and total-out shown; no `/transaction/` link rendered for it (link-free).

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Add view** (parse each row's `json_data` into a `Transaction` for the summary):

```python
@blueprint.route('/mempool')
def mempool_view() -> Any:
    try:
        pending_page = db.paginate(
            PendingTxnDAO.pending_q(expired=expiry_cutoff(now()))
        )
        entries = []
        for row in pending_page.items:
            txn = Transaction.from_json(row.json_data)
            entries.append(
                {
                    'txid': txn.txid,
                    'timestamp': txn.timestamp_dt,
                    'inflows': len(txn.inflows),
                    'outflows': len(txn.outflows),
                    'total_out': sum(o.amount or 0 for o in txn.outflows),
                }
            )
    except HTTPException as e:
        return e
    except Exception as e:
        current_app.logger.exception(e)
        abort(500)
    return render_template(
        'mempool.html',
        title='Mempool',
        pending_page=pending_page,
        entries=entries,
    )
```

> Imports: `PendingTxnDAO` (from `gumptionchain.models`), `expiry_cutoff` (from `gumptionchain.block`), `now` (from `gumptionchain.util`), `Transaction` (already imported). `o.amount` is `int | None` → guard with `or 0`.

- [ ] **Step 4: Create `mempool.html`** — extends base; card "Mempool" with a count header (`pending_page.total` pending); `{% include "mempool/extra.html" ignore missing %}`; table over `entries` (txid (plain text, font-monospace, NOT linked), `timestamp | utc_datetime`, inflows, outflows, total_out grains); `{{ render_pagination(pending_page, 'browser.mempool_view') }}`; empty (`not pending_page.total`) → "Mempool is empty."

- [ ] **Step 5: Nav link** in `base.html` after Addresses: `<a class="navbar-nav" href="{{ url_for('browser.mempool_view') }}">Mempool</a>`.

- [ ] **Step 6: Run** → PASS. **Commit** — `feat(browser): mempool pending-transactions page`.

### Task 8: Home pending count (TDD)

**Files:**
- Modify: `src/gumptionchain/browser.py` (`index_view`), `src/gumptionchain/templates/index.html`
- Test: `tests/test_home_page.py`

- [ ] **Step 1: Failing test** — extend a home test: with a pending txn in the pool, `/` shows a "Pending" stat card with the count.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Update `index_view`** to also pass `pending_count=PendingTxnDAO.count()` (independent of `lc`; compute inside the try). Add a "Pending" card to the stats strip in `index.html` (only meaningful when `lc`, but `pending_count` is always available — place it in the existing `{% if lc %}` stats row).

- [ ] **Step 4: Run** → PASS. **Commit** — `feat(browser): pending-txn count on the home stats strip`.

### Task 9: Seam test + gates

**Files:** Modify `tests/test_ui_seam.py`

- [ ] **Step 1:** Add a seam test for `/mempool` (empty → `SKINNED` + "Mempool is empty").

- [ ] **Step 2: Full gates** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest` → green.

- [ ] **Step 3: Commit + open PR** — push, `gh pr create`.

---

## Final

After both PRs merge: dispatch a final reviewer over the combined diff; update the EGU checklist (#190) to mark #203 done; close #203.
