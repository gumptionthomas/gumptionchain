# create_split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `create_split` primitive that mints `count` same-address UTXO "chips" of `denomination` grains (+ change) in one transaction, exposed end-to-end (domain → node API → ApiClient → node-proxy → CLI).

**Architecture:** Mirror `create_transfer` exactly — same input gather (`unspent_outflows(..., filter_pending=True)`) and `_funds_error`, differing only in the outputs (N self-addressed chips + change). Bounded by `MAX_FLOWS=50` (count ≤ 49, reserving one change slot). No new domain concepts; it's an ordinary regular transaction.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0, pydantic, click, pytest, uv.

---

## Background the engineer needs

- **`Chain.create_transfer`** (`src/gumptionchain/chain.py:605`) is the exact template:
  ```python
  unspent = self.unspent_outflows(address, limit=amount, filter_pending=True)
  for txid, index, outflow in unspent:
      balance += outflow.amount or 0
      t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=index))
  if balance < amount:
      raise self._funds_error(address, amount)
  t.add_outflow(Outflow(amount=amount, address=dest_address))
  if balance - amount:
      t.add_outflow(Outflow(amount=balance - amount, address=address))
  t.set_signing_key(signing_key); t.seal(); return t
  ```
  `unspent_outflows(address, limit, filter_pending)` (chain.py:463) is a generator that yields confirmed-unspent (optionally pending-filtered) outflows until the accumulated amount ≥ `limit`. `Inflow`, `Outflow`, `Transaction`, `SigningKey` are already imported in chain.py.
- **`_funds_error(address, amount)`** (chain.py:521): returns `PendingFundsError` if `balance(address) >= amount` (funds locked in pending), else `InsufficientFundsError`. Reuse unchanged.
- **`MAX_FLOWS = 50`** (`src/gumptionchain/transaction.py:55`).
- **Node build endpoint pattern** (`api.py`): `TransferTxnQueryModel` (`public_key: PublicKeyType`, `amount: int = Field(ge=1)`, `address: AddressType`) + `TransferTxnView.get` (validate model → `SigningKey(b64ks=public_key)` → `node_lc_dao()` → `lc.create_transfer(...)` → `make_json_response(txn.to_json())`; `except GCError → make_error_response`, `Exception → exception_response`) + `blueprint.add_url_rule('/transaction/transfer', authorize_transactor(TransferTxnView.as_view('txn_transfer_transactor')), methods=['GET'])`. `_pydantic_validation_error`, `node_lc_dao`, `EmptyChainError`, `SigningKey`, `make_json_response` are in api.py.
- **`ApiClient.get_transfer_transaction(public_key, amount, address, ...)`** (`api_client.py:143`) → `self.get('/api/transaction/transfer', params={...str values...})`.
- **node_proxy build routes** (`node_proxy.py`): `_grit_to_grains`, `_ok`, `_call`, `_ProxyError`; the `/txn/transfer` route reads `{public_key, amount_grit, to_address}`. (Added in #294.)
- **CLI `txn transfer`** (`command.py:665-742`): `@txn_cli.command('transfer')` with args + `-t/--txn-signing_key`, `-h/--host`, `-w/--signing_key`, `-y/--yes`; body uses `address_signing_key(addr, signing_key_file=...)`, `host_api_client(...)`, `grit_to_grains(amount)`, `Transaction.from_json(r.text)`, confirm, `txn.set_signing_key/sign`, `client.post_transaction(txn)`. `grit_to_grains` (command.py:57), `address_signing_key` (command.py:117).
- **Fixtures** (`tests/conftest.py`): `app`, `host`, `mill_block` (returns `(Miller, block)`; funds the key via coinbase `REWARD`), `requests_proxy`, `runner`, `signing_key`, `transactor_signing_key`. `tests/test_command.py` has `REWARD`, `run_txn_transfer`.
- **Commands:** `uv run pytest <path>`, `uv run mypy`, `uv run ruff check src tests`, `uv run ruff format --check src tests`.

---

## Task 1: Domain — `Chain.create_split`

**Files:**
- Modify: `src/gumptionchain/chain.py`
- Test: `tests/test_split.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_split.py`:

```python
import pytest

from gumptionchain.api_client import ApiClient
from gumptionchain.exceptions import (
    InsufficientFundsError,
    PendingFundsError,
)


def test_create_split_mints_chips_plus_change(app, mill_block, signing_key):
    with app.app_context():
        m, _ = mill_block(signing_key)
        lc = m.longest_chain
        txn = lc.create_split(signing_key, denomination=100, count=3)
        chips = [o for o in txn.outflows if o.amount == 100]
        change = [o for o in txn.outflows if o.amount != 100]
        assert len(chips) == 3
        assert all(o.address == signing_key.address for o in txn.outflows)
        assert len(change) == 1  # leftover reward as one change UTXO
        assert len(txn.outflows) <= 50


def test_create_split_exact_has_no_change(app, mill_block, signing_key):
    with app.app_context():
        m, _ = mill_block(signing_key)
        lc = m.longest_chain
        bal = lc.balance(signing_key.address)
        txn = lc.create_split(signing_key, denomination=bal, count=1)
        assert len(txn.outflows) == 1  # whole balance, no remainder
        assert txn.outflows[0].amount == bal


def test_create_split_49_chips_within_max_flows(app, mill_block, signing_key):
    with app.app_context():
        m, _ = mill_block(signing_key)
        lc = m.longest_chain
        txn = lc.create_split(signing_key, denomination=1, count=49)
        assert sum(1 for o in txn.outflows if o.amount == 1) == 49
        assert len(txn.outflows) <= 50  # 49 chips + 1 change


def test_create_split_insufficient_funds(app, mill_block, signing_key):
    with app.app_context():
        m, _ = mill_block(signing_key)
        lc = m.longest_chain
        bal = lc.balance(signing_key.address)
        with pytest.raises(InsufficientFundsError):
            lc.create_split(signing_key, denomination=bal, count=2)  # 2x balance


def test_create_split_pending_funds(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        m, _ = mill_block(signing_key)
        lc = m.longest_chain
        bal = lc.balance(signing_key.address)
        # Lock the only UTXO in a pending transfer, then split must see the
        # confirmed balance but no spendable (non-pending) funds.
        xfer = lc.create_transfer(signing_key, 1, signing_key.address)
        xfer.sign()
        ApiClient(host, signing_key).post_transaction(xfer)
        with pytest.raises(PendingFundsError):
            lc.create_split(signing_key, denomination=bal, count=1)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_split.py -v`
Expected: FAIL — `AttributeError: 'Chain' object has no attribute 'create_split'`.

- [ ] **Step 3: Implement `create_split`**

In `src/gumptionchain/chain.py`, immediately after `create_transfer`:

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

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_split.py -v`
Expected: PASS (all 5).

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy` (clean), `uv run ruff check src tests` + `uv run ruff format --check src tests` (clean).

```bash
git add src/gumptionchain/chain.py tests/test_split.py
git commit -m "feat: Chain.create_split chip-minting primitive"
```

---

## Task 2: Node endpoint — `GET /api/transaction/split`

**Files:**
- Modify: `src/gumptionchain/api.py` (model + view + route + `MAX_FLOWS` import)
- Test: `tests/test_split.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_split.py`:

```python
def test_split_endpoint_returns_unsigned_txn(
    app, host, mill_block, requests_proxy, transactor_signing_key
):
    with app.app_context():
        mill_block(transactor_signing_key)
    client = ApiClient(host, transactor_signing_key)
    r = client.get(
        '/api/transaction/split',
        params={
            'public_key': transactor_signing_key.public_key_b64,
            'denomination': '100',
            'count': '3',
        },
    )
    assert r.status_code == 200
    body = r.json()
    chips = [o for o in body['outflows'] if o.get('amount') == 100]
    assert len(chips) == 3


def test_split_endpoint_rejects_count_over_49(
    app, host, mill_block, requests_proxy, transactor_signing_key
):
    with app.app_context():
        mill_block(transactor_signing_key)
    r = ApiClient(host, transactor_signing_key).get(
        '/api/transaction/split',
        params={
            'public_key': transactor_signing_key.public_key_b64,
            'denomination': '1',
            'count': '50',
        },
        raise_for_status=False,
    )
    assert r.status_code == 400


def test_split_endpoint_rejects_zero_denomination(
    app, host, mill_block, requests_proxy, transactor_signing_key
):
    with app.app_context():
        mill_block(transactor_signing_key)
    r = ApiClient(host, transactor_signing_key).get(
        '/api/transaction/split',
        params={
            'public_key': transactor_signing_key.public_key_b64,
            'denomination': '0',
            'count': '3',
        },
        raise_for_status=False,
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_split.py -k endpoint -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Implement the endpoint**

In `src/gumptionchain/api.py`, add `MAX_FLOWS` to the transaction import (find the existing `from gumptionchain.transaction import ...`; if none, add `from gumptionchain.transaction import MAX_FLOWS`). Add the model + view next to `TransferTxnView`, and register the route next to the transfer rule:

```python
class SplitTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    public_key: PublicKeyType
    denomination: int = Field(ge=1)
    count: int = Field(ge=1, le=MAX_FLOWS - 1)


class SplitTxnView(MethodView):
    def get(self, **kwargs: Any) -> Response:
        try:
            model = SplitTxnQueryModel.model_validate(
                request.args.to_dict(flat=True)
            )
        except ValidationError as e:
            return make_error_response(_pydantic_validation_error(e))
        try:
            args = model.model_dump(exclude_none=True)
            signing_key = SigningKey(b64ks=args['public_key'])
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            return make_json_response(
                lc.create_split(
                    signing_key, args['denomination'], args['count']
                ).to_json()
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/transaction/split',
    view_func=authorize_transactor(
        SplitTxnView.as_view('txn_split_transactor')
    ),
    methods=['GET'],
)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_split.py -v`
Expected: PASS (all).

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy`, `uv run ruff check src tests`, `uv run ruff format --check src tests` (all clean).

```bash
git add src/gumptionchain/api.py tests/test_split.py
git commit -m "feat: GET /api/transaction/split build endpoint"
```

---

## Task 3: `ApiClient.get_split_transaction`

**Files:**
- Modify: `src/gumptionchain/api_client.py`
- Test: `tests/test_split.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_split.py`:

```python
def test_api_client_get_split_transaction(
    app, host, mill_block, requests_proxy, transactor_signing_key
):
    with app.app_context():
        mill_block(transactor_signing_key)
    r = ApiClient(host, transactor_signing_key).get_split_transaction(
        transactor_signing_key.public_key_b64, 100, 4
    )
    assert r.status_code == 200
    chips = [o for o in r.json()['outflows'] if o.get('amount') == 100]
    assert len(chips) == 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_split.py -k api_client -v`
Expected: FAIL — `AttributeError: 'ApiClient' object has no attribute 'get_split_transaction'`.

- [ ] **Step 3: Implement the method**

In `src/gumptionchain/api_client.py`, after `get_transfer_transaction`:

```python
    def get_split_transaction(
        self,
        public_key: str,
        denomination: int,
        count: int,
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
            timeout=timeout,
            raise_for_status=raise_for_status,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_split.py -k api_client -v`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy`, `uv run ruff check src tests`, `uv run ruff format --check src tests` (clean).

```bash
git add src/gumptionchain/api_client.py tests/test_split.py
git commit -m "feat: ApiClient.get_split_transaction"
```

---

## Task 4: node-proxy route — `POST /api/node/txn/split`

**Files:**
- Modify: `src/gumptionchain/node_proxy.py`
- Test: `tests/test_node_proxy.py` (`FakeClient` stub + tests)

- [ ] **Step 1: Write the failing tests**

In `tests/test_node_proxy.py`, add to `FakeClient` (after `get_transfer_transaction`):

```python
    def get_split_transaction(
        self, pk, denomination, count, *, raise_for_status=True
    ):
        return self._resp('build_split', pk, denomination, count)
```

Add tests (after the transfer-build tests):

```python
def test_build_split_converts_grit_and_passes_count():
    unsigned = {'txid': 's1', 'outflows': [{'amount': 200, 'address': 'GCxGC'}]}
    client = FakeClient(build_split=FakeResponse(200, unsigned))
    resp = _app(client).post(
        '/api/node/txn/split',
        json={'public_key': 'PUB', 'denomination_grit': 2, 'count': 30},
    )
    assert resp.status_code == 200
    assert resp.get_json() == unsigned
    name, args, _ = client.calls[0]
    assert name == 'build_split'
    assert args == ('PUB', 200, 30)   # 2 GRIT -> 200 grains; count passthrough


def test_build_split_rejects_bad_amount():
    client = FakeClient()
    resp = _app(client).post(
        '/api/node/txn/split',
        json={'public_key': 'P', 'denomination_grit': 0, 'count': 5},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_node_proxy.py -k split -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Implement the proxy route**

In `src/gumptionchain/node_proxy.py`, after the `/txn/transfer` route:

```python
    @bp.post('/txn/split')
    def txn_split() -> Response:
        # Build an unsigned self-split: mint `count` chips of denomination_grit
        # each (back to the signer's own address). Client signs + submits.
        data = request.get_json(silent=True) or {}
        public_key = data.get('public_key')
        if not isinstance(public_key, str) or not public_key:
            raise _ProxyError(400, 'public_key required')
        denomination = _grit_to_grains(data.get('denomination_grit'))
        count = data.get('count')
        if not isinstance(count, int) or count < 1:
            raise _ProxyError(400, 'count must be a positive integer')
        return jsonify(
            _ok(
                _call(
                    make_client().get_split_transaction,
                    public_key,
                    denomination,
                    count,
                )
            ).json()
        )
```

(Note: `count` upper-bound enforcement lives at the node endpoint via `SplitTxnQueryModel` — the proxy relays and the node returns the 400 if `count > 49`; the proxy only guards the obviously-bad `count < 1` for a clean early error.)

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_node_proxy.py -k split -v`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy`, `uv run ruff check src tests`, `uv run ruff format --check src tests` (clean).

```bash
git add src/gumptionchain/node_proxy.py tests/test_node_proxy.py
git commit -m "feat: node_proxy /txn/split build relay route"
```

---

## Task 5: CLI — `gc txn split`

**Files:**
- Modify: `src/gumptionchain/command.py`
- Test: `tests/test_command.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_command.py`:

```python
def test_txn_split(app, mill_block, runner, requests_proxy, signing_key):
    with app.app_context():
        from_signing_key = SigningKey()
        fwf = from_signing_key.to_file(
            signing_keydir=app.config.get('SIGNING_KEY_DIR')
        )
        m, _ = mill_block(from_signing_key)
        result = runner.invoke(
            args=[
                'txn', 'split', from_signing_key.address, '3', '1',
                '--txn-signing_key', fwf, '-y',
            ],
        )
        assert 'Split created.' in result.output
        assert len(m.pending_txns) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_command.py::test_txn_split -v`
Expected: FAIL — `Error: No such command 'split'` (nonzero exit).

- [ ] **Step 3: Implement the command**

In `src/gumptionchain/command.py`, add after the `create_transfer` command (mirroring its decorators + flow):

```python
@txn_cli.command('split')
@click.argument('from_address')
@click.argument('count', type=click.INT)
@click.argument('denomination_grit', type=click.FLOAT)
@click.option(
    '-t',
    '--txn-signing_key',
    type=click.Path(exists=True),
    default=None,
    help='SigningKey file to use for transaction source.',
)
@click.option(
    '-h',
    '--host',
    default=None,
    help='The API host to use (default from app config).',
)
@click.option(
    '-w',
    '--signing_key',
    type=click.Path(exists=True),
    default=None,
    help='SigningKey file to use for API auth.',
)
@click.option(
    '-y',
    '--yes',
    is_flag=True,
    default=False,
    help='Assume "yes" as answer to all prompts and run non-interactively.',
)
@with_appcontext
def create_split(
    from_address: str,
    count: int,
    denomination_grit: float,
    txn_signing_key: str | None,
    host: str | None,
    signing_key: str | None,
    yes: bool,  # noqa: FBT001
) -> None:
    """Split your balance into COUNT same-address chips of DENOMINATION_GRIT each.

    \b
    FROM_ADDRESS is the key whose balance is sharded.
    COUNT is how many chips to mint (1-49).
    DENOMINATION_GRIT is each chip's size in GRIT.
    """
    try:
        txn_signing_key_obj = address_signing_key(
            from_address, signing_key_file=txn_signing_key
        )
        client = host_api_client(host=host, signing_key_file=signing_key)
        r = client.get_split_transaction(
            txn_signing_key_obj.public_key_b64,
            grit_to_grains(denomination_grit),
            count,
        )
        txn = Transaction.from_json(r.text)
        if not (confirm := yes):
            console.print(f'Split transaction created: {txn.txid}')
            confirm = Confirm.ask(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_signing_key(txn_signing_key_obj)
            txn.sign()
            client.post_transaction(txn)
            console.print('Split created.', style='success')
        else:
            console.print('Split aborted.', style='error')
    except httpx.HTTPStatusError as e:
        console.print(f'Split failed: {http_error_message(e)}', style='error')
    except Exception as e:
        console.print(f'Split failed: {e}', style='error')
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_command.py::test_txn_split -v`
Expected: PASS.

- [ ] **Step 5: Full gate sweep + commit**

Run: `uv run pytest` (all pass), `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy` (clean).

```bash
git add src/gumptionchain/command.py tests/test_command.py
git commit -m "feat(cli): gc txn split command"
```

---

## Final verification (after all tasks)

- [ ] `uv run pytest` → all pass; `ruff check` + `ruff format --check` + `mypy` clean.
- [ ] Dispatch a final whole-branch code review.
- [ ] Open the PR; report the merge SHA for gumptactoe to pin (the proxy `/txn/split` route + `denomination_grit`/`count` shape) so the house can pre-shard its balance into award-sized chips.

---

## Plan self-review

- **Spec coverage:** domain `create_split` → Task 1; `MAX_FLOWS` 49-cap + endpoint → Task 2; `ApiClient` → Task 3; proxy route → Task 4; CLI → Task 5. Validation (count 1..49, denomination ≥1) → Task 2 model + tests; funds errors (`PendingFundsError`/`InsufficientFundsError`) → Task 1 tests. All spec sections covered.
- **Type consistency:** `create_split(signing_key, denomination, count)` identical across domain (Task 1), endpoint (`SplitTxnQueryModel` denomination/count, Task 2), `get_split_transaction(public_key, denomination, count)` (Task 3), proxy `get_split_transaction(public_key, grains, count)` (Task 4), CLI (Task 5). Grains everywhere except the proxy boundary (`denomination_grit`) and CLI (`DENOMINATION_GRIT`), converted via `_grit_to_grains`/`grit_to_grains`. `le=MAX_FLOWS - 1` (49) consistent with the 50-output bound.
- **Placeholder scan:** none; complete code in every step.
- **Minor refinement noted:** the CLI takes a leading `FROM_ADDRESS` (to locate the signing key, exactly like `txn transfer`) — the spec sketched `COUNT DENOMINATION_GRIT`; `FROM_ADDRESS` is required for `address_signing_key`, matching the transfer command.
