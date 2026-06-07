# EGU #3 / #176a — transaction provenance lookup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /api/transaction/<txid>` returning a stake's on-chain provenance (address, outflows, canonical status, confirmations), so the verifiable stake card (#176b) can check "this stake really happened on-chain."

**Architecture:** A domain method `ChainDAO.transaction_provenance` (wrapped by `Chain.transaction_provenance`) composes the existing `ChainDAO.get_transaction` (canonical, via materialized `LongestChainBlockDAO` ancestry), `TransactionDAO.get` (orphaned), and `PendingTxnDAO.json_data` (pending). A READER-authed Flask view exposes it, cached under the tip hash. No schema/consensus change.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0, pytest, ruff/mypy strict.

**Spec:** `docs/superpowers/specs/2026-06-06-egu-3-txn-provenance-lookup-design.md`

**IMPORTANT (shell cwd):** run all commands from the repository root (the top level of your clone); use repo-root-relative paths in `git add`.

---

## File Structure

- **Modify** `src/gumptionchain/models.py` — `_outflow_view`, `_pending_provenance` helpers; `ChainDAO.transaction_provenance` + `ChainDAO.pending_provenance`; `import json` if not present.
- **Modify** `src/gumptionchain/chain.py` — `Chain.transaction_provenance` wrapper.
- **Modify** `src/gumptionchain/api.py` — `TransactionProvenanceView` + GET route; `from gumptionchain.models import ChainDAO`.
- **Test** `tests/test_chain.py` — domain-method tests.
- **Test** `tests/test_api.py` — endpoint tests.

Test command: `uv run pytest tests/test_chain.py tests/test_api.py -q`; lint/type: `uv run ruff check src tests`, `uv run mypy`.

---

### Task 1: Domain — `ChainDAO.transaction_provenance`

**Files:**
- Modify: `src/gumptionchain/models.py`, `src/gumptionchain/chain.py`
- Test: `tests/test_chain.py`

- [ ] **Step 1: Write the failing domain tests** — append to `tests/test_chain.py` (reuses existing fixtures `app`, `wallet`, `subject`, `mill_block`, `add_chain_block`, `host`; imports at top of file already include `Chain`, `Wallet`, `db`; add `from gumptionchain.api_client import ApiClient` and `from gumptionchain.models import ChainDAO` if not already imported, and `import httpx` is not needed here):

```python
def test_transaction_provenance_canonical(
    app, host, mill_block, requests_proxy, subject, wallet
):
    from gumptionchain.api_client import ApiClient
    from gumptionchain.models import ChainDAO

    with app.app_context():
        m, _b1 = mill_block(wallet)  # coinbase funds `wallet`
        txn = m.longest_chain.create_opposition(wallet, 300, subject)
        txn.sign()
        ApiClient(host, wallet).post_transaction(txn)
        m, _b2 = mill_block(wallet)  # mines txn into the tip block

        prov = ChainDAO.longest().transaction_provenance(txn.txid)
        assert prov is not None
        assert prov['status'] == 'canonical'
        assert prov['address'] == wallet.address
        assert prov['outflows'] == [
            {'kind': 'opposition', 'subject': subject, 'amount': 300}
        ]
        assert prov['confirmations'] == 1
        assert prov['block_hash'] == _b2.block_hash

        m, _b3 = mill_block(wallet)  # tip advances
        prov2 = ChainDAO.longest().transaction_provenance(txn.txid)
        assert prov2['confirmations'] == 2


def test_transaction_provenance_pending(
    app, host, mill_block, requests_proxy, subject, wallet
):
    from gumptionchain.api_client import ApiClient
    from gumptionchain.models import ChainDAO

    with app.app_context():
        m, _b1 = mill_block(wallet)
        txn = m.longest_chain.create_opposition(wallet, 7, subject)
        txn.sign()
        ApiClient(host, wallet).post_transaction(txn)  # left in mempool

        prov = ChainDAO.longest().transaction_provenance(txn.txid)
        assert prov is not None
        assert prov['status'] == 'pending'
        assert prov['block_hash'] is None
        assert prov['confirmations'] == 0
        assert prov['outflows'] == [
            {'kind': 'opposition', 'subject': subject, 'amount': 7}
        ]


def test_transaction_provenance_unknown_returns_none(
    app, host, mill_block, requests_proxy, wallet
):
    from gumptionchain.chain import mill_hash_str
    from gumptionchain.models import ChainDAO

    with app.app_context():
        mill_block(wallet)
        absent = mill_hash_str('no-such-transaction')
        assert ChainDAO.longest().transaction_provenance(absent) is None


def test_transaction_provenance_orphaned(
    add_chain_block, app, host, mill_block, requests_proxy, wallet
):
    # Mine main chain genesis->b2; b2's coinbase txn is canonical. Then build
    # a longer fork off genesis that excludes b2, sync the materialization
    # (Chain.to_db -> sync_longest_chain_blocks), and assert b2's coinbase txn
    # is now orphaned. Fork construction mirrors tests/test_chain.py::test_dao.
    from gumptionchain.models import ChainDAO

    with app.app_context():
        wallet2 = Wallet()
        m, b1 = mill_block(wallet)            # genesis
        m, b2 = mill_block(wallet)            # b2 on main (coinbase txn)
        coinbase_txid = b2.txns[-1].txid

        # canonical first
        assert ChainDAO.longest().transaction_provenance(
            coinbase_txid
        )['status'] == 'canonical'

        # build a strictly-longer fork off b1 that excludes b2
        alt = Chain(block_hash=b1.block_hash)
        add_chain_block(chain=alt, milling_wallet=wallet2)   # alt-a
        _, _ = add_chain_block(chain=alt, milling_wallet=wallet2)  # alt-b
        alt.to_db()  # sync_longest_chain_blocks -> alt becomes canonical

        prov = ChainDAO.longest().transaction_provenance(coinbase_txid)
        assert prov is not None
        assert prov['status'] == 'orphaned'
        assert prov['height'] is None
        assert prov['confirmations'] == 0
```

NOTE on the orphaned test: `add_chain_block` builds blocks but does not itself sync the longest-chain materialization; `alt.to_db()` is what calls `sync_longest_chain_blocks()`. The fork must be strictly longer than the main `genesis→b2` for `_is_longest()` to favor it — two added blocks vs. one does that. If the exact fork mechanics need adjustment, mirror `tests/test_chain.py::test_dao` (the canonical fork pattern) but keep these assertions.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_chain.py -k transaction_provenance -q`
Expected: FAIL — `transaction_provenance` does not exist.

- [ ] **Step 3: Add the helpers + method** — in `src/gumptionchain/models.py`. Ensure `import json` is present at the top (add it if missing). Add these two module-level helpers (place them above `class ChainDAO`):

```python
def _outflow_view(
    *,
    amount: int,
    address: str | None = None,
    opposition: str | None = None,
    support: str | None = None,
    rescind: str | None = None,
    rescind_kind: str | None = None,
) -> dict[str, Any]:
    if opposition is not None:
        return {'kind': 'opposition', 'subject': opposition, 'amount': amount}
    if support is not None:
        return {'kind': 'support', 'subject': support, 'amount': amount}
    if rescind is not None:
        return {
            'kind': 'rescind',
            'subject': rescind,
            'rescind_kind': rescind_kind,
            'amount': amount,
        }
    return {'kind': 'transfer', 'address': address, 'amount': amount}


def _pending_provenance(txid: str) -> dict[str, Any] | None:
    pending = PendingTxnDAO.get(txid)
    if pending is None:
        return None
    data = json.loads(pending.json_data)
    outflows = [
        _outflow_view(
            amount=o['amount'],
            address=o.get('address'),
            opposition=o.get('opposition'),
            support=o.get('support'),
            rescind=o.get('rescind'),
            rescind_kind=o.get('rescind_kind'),
        )
        for o in data.get('outflows', [])
    ]
    return {
        'address': data.get('address'),
        'outflows': outflows,
        'timestamp': data.get('timestamp'),
        'status': 'pending',
        'block_hash': None,
        'height': None,
        'confirmations': 0,
    }
```

Then add to `class ChainDAO` (alongside `get_transaction`):

```python
    @classmethod
    def pending_provenance(cls, txid: str) -> dict[str, Any] | None:
        return _pending_provenance(txid)

    def transaction_provenance(self, txid: str) -> dict[str, Any] | None:
        txn = self.get_transaction(txid)  # canonical (longest chain) or None
        if txn is not None:
            block = (
                db.session.execute(
                    db.select(BlockDAO)
                    .join(
                        LongestChainBlockDAO,
                        LongestChainBlockDAO.block_id == BlockDAO.id,
                    )
                    .join(BlockDAO.transactions)
                    .where(TransactionDAO.txid == txid)
                )
                .scalars()
                .first()
            )
            tip_height = self.block.idx
            height = block.idx if block is not None else None
            confirmations = (
                tip_height - height + 1 if height is not None else 0
            )
            return {
                'address': txn.address,
                'outflows': [
                    _outflow_view(
                        amount=o.amount,
                        address=o.address,
                        opposition=o.opposition,
                        support=o.support,
                        rescind=o.rescind,
                        rescind_kind=o.rescind_kind,
                    )
                    for o in txn.outflows
                ],
                'timestamp': txn.timestamp.isoformat(),
                'status': 'canonical',
                'block_hash': block.block_hash if block is not None else None,
                'height': height,
                'confirmations': confirmations,
            }
        orphan = TransactionDAO.get(txid)
        if orphan is not None:
            block_hash = (
                orphan.blocks[0].block_hash if orphan.blocks else None
            )
            return {
                'address': orphan.address,
                'outflows': [
                    _outflow_view(
                        amount=o.amount,
                        address=o.address,
                        opposition=o.opposition,
                        support=o.support,
                        rescind=o.rescind,
                        rescind_kind=o.rescind_kind,
                    )
                    for o in orphan.outflows
                ],
                'timestamp': orphan.timestamp.isoformat(),
                'status': 'orphaned',
                'block_hash': block_hash,
                'height': None,
                'confirmations': 0,
            }
        return _pending_provenance(txid)
```

(`Any` is already imported in models.py; confirm and add to the `typing` import if not.)

- [ ] **Step 4: Add the `Chain` wrapper** — in `src/gumptionchain/chain.py`, alongside `balance`:

```python
    def transaction_provenance(self, txid: str) -> dict[str, Any] | None:
        dao = self.to_dao()
        if dao is not None:
            return dao.transaction_provenance(txid)
        return ChainDAO.pending_provenance(txid)
```

Ensure `ChainDAO` and `Any` are imported in `chain.py` (it already references `ChainDAO` in `to_dao`; add `Any` to the typing import if not present).

- [ ] **Step 5: Run domain tests + lint/type**

Run: `uv run pytest tests/test_chain.py -k transaction_provenance -q && uv run ruff check src/gumptionchain/models.py src/gumptionchain/chain.py && uv run mypy`
Expected: tests PASS; ruff/mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/models.py src/gumptionchain/chain.py tests/test_chain.py
git commit -m "feat(chain): transaction_provenance — canonical/orphaned/pending + confirmations"
```

---

### Task 2: API — `GET /api/transaction/<txid>`

**Files:**
- Modify: `src/gumptionchain/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing API tests** — append to `tests/test_api.py` (the file already imports `ApiClient`, `signing`, `httpx`, `TIMEOUT`, fixtures `app`/`host`/`mill_block`/`requests_proxy`/`subject`/`wallet`; add `from gumptionchain.chain import mill_hash_str` locally in the unknown test):

```python
def test_transaction_provenance_endpoint_canonical(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b1 = mill_block(wallet)
        txn = m.longest_chain.create_opposition(wallet, 300, subject)
        txn.sign()
        ApiClient(host, wallet).post_transaction(txn)
        m, b2 = mill_block(wallet)

        resp = ApiClient(host, wallet).get(f'/api/transaction/{txn.txid}')
        assert resp.status_code == httpx.codes.OK
        body = resp.json()
        assert body['txid'] == txn.txid
        assert body['address'] == wallet.address
        assert body['status'] == 'canonical'
        assert body['confirmations'] == 1
        assert body['block_hash'] == b2.block_hash
        assert body['as_of_block'] == b2.block_hash
        assert body['outflows'] == [
            {'kind': 'opposition', 'subject': subject, 'amount': 300}
        ]


def test_transaction_provenance_endpoint_pending(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b1 = mill_block(wallet)
        txn = m.longest_chain.create_opposition(wallet, 5, subject)
        txn.sign()
        ApiClient(host, wallet).post_transaction(txn)

        resp = ApiClient(host, wallet).get(f'/api/transaction/{txn.txid}')
        assert resp.status_code == httpx.codes.OK
        assert resp.json()['status'] == 'pending'


def test_transaction_provenance_endpoint_unknown_404(
    app, host, mill_block, requests_proxy, wallet
):
    from gumptionchain.chain import mill_hash_str

    with app.app_context():
        mill_block(wallet)
        absent = mill_hash_str('absent-txn')
        resp = ApiClient(host, wallet).get(f'/api/transaction/{absent}')
        assert resp.status_code == httpx.codes.NOT_FOUND


def test_transaction_provenance_endpoint_requires_auth(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b1 = mill_block(wallet)
        txn = m.longest_chain.create_opposition(wallet, 1, subject)
        txn.sign()
        ApiClient(host, wallet).post_transaction(txn)
        mill_block(wallet)
        # unsigned request -> 401
        resp = requests_proxy.get(
            f'/api/transaction/{txn.txid}', timeout=TIMEOUT
        )
        assert resp.status_code == httpx.codes.UNAUTHORIZED
```

NOTE: if `ApiClient.get` raises `httpx.HTTPStatusError` on a 4xx (some client methods call `raise_for_status`), wrap the unknown-404 assertion as
`with pytest.raises(httpx.HTTPStatusError, match='404'): ApiClient(host, wallet).get(...)` — mirror whichever convention `ApiClient.get` already uses (see `get_block` vs `post_transaction` in `api_client.py`). The auth test uses `requests_proxy` directly so it never raises.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api.py -k transaction_provenance -q`
Expected: FAIL — route returns 404/405 for GET (no view yet) or the body assertions fail.

- [ ] **Step 3: Add the view + route** — in `src/gumptionchain/api.py`. Add `from gumptionchain.models import ChainDAO` to the imports. Add the view class near the other `MethodView`s (e.g. after `TxnView`):

```python
class TransactionProvenanceView(MethodView):
    def get(self, txid: str, **kwargs: Any) -> Response:
        try:
            _, lc, _ = node_lc_dao()
            tip = lc.block_hash if lc is not None else None
            key = f'{tip}.{txid}.txn-provenance'
            if (prov := cache.get(key)) is None:
                prov = (
                    lc.transaction_provenance(txid)
                    if lc is not None
                    else ChainDAO.pending_provenance(txid)
                )
                if prov is not None:
                    cache.set(key, prov)
            if prov is None:
                return make_json_response(
                    {'error': 'transaction not found'}, 404
                )
            return make_json_response(
                {'txid': txid, **prov, 'as_of_block': tip}
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)
```

Register the GET route (the POST receive route on the same path is unchanged):

```python
blueprint.add_url_rule(
    '/transaction/<mill_hash:txid>',
    view_func=authorize_reader(
        TransactionProvenanceView.as_view('transaction_provenance_reader')
    ),
    methods=['GET'],
)
```

Place this near the existing `/transaction/<mill_hash:txid>` POST rule. (`GCError` is already imported via the exceptions import block; confirm it is in scope — the other views catch it.)

- [ ] **Step 4: Run API tests + full suite + lint/type**

Run: `uv run pytest tests/test_api.py -k transaction_provenance -q`
Expected: PASS.

Then: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
Expected: full suite green; ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/gumptionchain/api.py tests/test_api.py
git commit -m "feat(api): GET /transaction/<txid> — provenance lookup (reader)"
```

---

## Final verification (before finishing the branch)

- [ ] `uv run pytest` — full suite green (new domain + API tests included).
- [ ] `uv run ruff check src tests && uv run ruff format --check src tests` — clean.
- [ ] `uv run mypy` — no new errors.
- [ ] Manual sanity (optional): the GET route returns 404 for an unknown txid and 200 with the documented JSON for a canonical txn.
- [ ] No schema/migration added (`git diff --stat` shows no `migrations/` change); no consensus/validation change.

## Self-review notes

- **Spec coverage:** endpoint + reader auth (Task 2); provenance view with confirmations (Task 1/2); status canonical/orphaned/pending + 404 (Task 1 method, Task 2 view); all four outflow kinds via `_outflow_view`; grains; `as_of_block` = tip; cache key `{tip}.{txid}.txn-provenance`. All mapped.
- **Name consistency:** `transaction_provenance` (Chain + ChainDAO), `pending_provenance`, `_outflow_view`, `_pending_provenance`; status strings `canonical`/`orphaned`/`pending`; outflow keys `kind`/`subject`/`address`/`amount`/`rescind_kind` identical to the spec.
- **No placeholders:** complete code for every method/test; the only adaptive note is the `ApiClient.get` raise-vs-return convention and the fork-sync mechanic, each with a concrete fallback + reference.
- **Additive:** only new methods/view/route + tests; existing POST route and behavior untouched; no schema/consensus change.
