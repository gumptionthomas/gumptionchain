# Subject Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a subject-search primitive to base gumptionchain so consumer web apps can offer a typeahead of on-chain subjects ranked by stake.

**Architecture:** Four thin layers. A decoded-plaintext column pair on `OutflowDAO` (`subject_plain` canonical + `subject_lower` indexed) populated at the constructor choke point → a `search_subjects` query method on `ChainDAO` (prefix, case-insensitive, ranked by total stake) delegated from `Chain` → a READER node endpoint `GET /api/subjects/search` → an `ApiClient.get_subject_search` method + a `node_proxy_blueprint` relay route that converts grains→GRIT.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0 (`Mapped[]`), Flask-Migrate/Alembic, pytest, httpx, uv.

---

## Background the engineer needs

- **Subjects are stored base64url-encoded** in `OutflowDAO.opposition` / `OutflowDAO.support` (`String(500)`, nullable; a row sets at most one). `encode_subject` / `decode_subject` live in `gumptionchain.payload`; `decode_subject` is bijective for valid subjects.
- **The constructor is the single creation choke point.** `OutflowDAO.__init__` (`src/gumptionchain/models.py:146-167`) is where `opposition`/`support` get set; the only construction sites are that class and `src/gumptionchain/transaction.py:315`, both via `OutflowDAO(...)`. Populating the new columns there covers every write path.
- **Query methods exist on BOTH `Chain` and `ChainDAO`.** `ChainDAO` (`models.py`) implements (`subject_leaderboard`, `opposition_balance`); `Chain` (`chain.py`) has thin delegating wrappers (`chain.py:558` → `self.to_dao().subject_leaderboard(limit)`). The API and browser call them on the `Chain`. The new `search_subjects` follows the same dual placement.
- **`node_lc_dao()`** (`api.py:82`) returns `(node, lc, dao)` where `lc` is the `Chain` (or `None` for an empty chain). Existing read views (`OppositionBalanceView`, `api.py:746`) do `_, lc, _ = node_lc_dao()` then call methods on `lc`.
- **Greenfield migrations:** per project practice, fold schema changes into the single baseline migration `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py` rather than stacking a new revision. There is no production data to back-fill. Tests build via `db.create_all()`. The `gumptionchain db check` CI gate enforces that `create_all()` (model metadata) matches the migration.
- **Proxy conventions** (`src/gumptionchain/node_proxy.py`): `_grit(grains)` → `{'grit': grains/100, 'grains': grains}`; `_call(fn, *args)` invokes `fn(*args, raise_for_status=False)` and maps `httpx.RequestError`→502; `_ok(r)` maps node 4xx/5xx; routes are CSRF-exempt with a `rate_limit` hook.
- **Run the full suite** with `uv run pytest`; lint `uv run ruff check src tests` + `uv run ruff format --check src tests`; types `uv run mypy`; schema gate `uv run gumptionchain db check`.

---

## Task 1: `OutflowDAO` plaintext columns + baseline migration

**Files:**
- Modify: `src/gumptionchain/models.py` (`OutflowDAO`: columns, index, `__init__`, new import)
- Modify: `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py` (outflow table)
- Test: `tests/test_subject_search.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_subject_search.py`:

```python
from gumptionchain.database import db
from gumptionchain.models import OutflowDAO
from gumptionchain.payload import encode_subject


def test_outflow_populates_plaintext_columns_for_a_stake(app):
    with app.app_context():
        enc = encode_subject('Tabs > Spaces')
        row = OutflowDAO('txid1', 0, 100, support=enc)
        assert row.subject_plain == 'Tabs > Spaces'
        assert row.subject_lower == 'tabs > spaces'


def test_outflow_plaintext_columns_none_for_non_stake(app):
    with app.app_context():
        row = OutflowDAO('txid2', 0, 100, address='GCwhoeverGC')
        assert row.subject_plain is None
        assert row.subject_lower is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_subject_search.py -v`
Expected: FAIL — `AttributeError: 'OutflowDAO' object has no attribute 'subject_plain'`.

- [ ] **Step 3: Add the columns, index, and constructor population**

In `src/gumptionchain/models.py`, add an import near the other `gumptionchain.payload` usage (top of file):

```python
from gumptionchain.payload import decode_subject
```

In `OutflowDAO`, add the two columns after `support` (around `models.py:125`):

```python
    subject_plain: Mapped[str | None] = mapped_column(String(500))
    subject_lower: Mapped[str | None] = mapped_column(String(500))
```

Add the index to `__table_args__` (after `ix_outflow_support`):

```python
        db.Index('ix_outflow_subject_lower', 'subject_lower'),
```

Add a static helper and populate in `__init__` (inside the `with db.session.no_autoflush:` block, after `self.support = support`):

```python
    @staticmethod
    def _derive_subject_plain(
        opposition: str | None, support: str | None
    ) -> str | None:
        encoded = opposition if opposition is not None else support
        if encoded is None:
            return None
        try:
            return decode_subject(encoded)
        except Exception:
            # Subjects are validated upstream; a decode failure must never
            # break row construction. Leave the searchable columns null.
            return None
```

```python
            plain = self._derive_subject_plain(opposition, support)
            self.subject_plain = plain
            self.subject_lower = plain.lower() if plain is not None else None
```

In `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py`, add the two columns inside `op.create_table('outflow', ...)` after the `support` column (`63d32cd7621a_initial_schema.py:117`):

```python
    sa.Column('subject_plain', sa.String(length=500), nullable=True),
    sa.Column('subject_lower', sa.String(length=500), nullable=True),
```

And add the index inside the `with op.batch_alter_table('outflow', ...)` block after `ix_outflow_support` (`63d32cd7621a_initial_schema.py:129`):

```python
        batch_op.create_index('ix_outflow_subject_lower', ['subject_lower'], unique=False)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_subject_search.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Verify the schema gate and types**

Run: `uv run gumptionchain db check`
Expected: no pending model/migration diff (exit 0). If it reports the new columns/index as a diff, the migration edit doesn't match the model — reconcile names/types until clean.

Run: `uv run mypy`
Expected: no new errors.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/models.py src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py tests/test_subject_search.py
git commit -m "feat(#287): OutflowDAO decoded-subject columns for search"
```

---

## Task 2: `search_subjects` DAO query + `Chain` delegate

**Files:**
- Modify: `src/gumptionchain/models.py` (`ChainDAO`: module constants, helper, `search_subjects`)
- Modify: `src/gumptionchain/chain.py` (`Chain.search_subjects` delegate)
- Test: `tests/test_subject_search.py`

Reuse the existing leaderboard test harness pattern (`tests/test_subject_leaderboard.py`): the `app, host, mill_block, requests_proxy, signing_key` fixtures, and a local `_stake` helper. Note `create_opposition` / `create_support` take an **encoded** subject.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_subject_search.py`:

```python
from gumptionchain.api_client import ApiClient


def _stake(host, chain, signing_key, *, oppose=None, support=None):
    if oppose is not None:
        subject, amount = oppose
        txn = chain.create_opposition(signing_key, amount, subject)
    else:
        subject, amount = support
        txn = chain.create_support(signing_key, amount, subject)
    txn.sign()
    ApiClient(host, signing_key).post_transaction(txn)
    return txn


def test_search_prefix_is_case_insensitive_returns_canonical(
    app, host, mill_block, requests_proxy, signing_key
):
    tabs = encode_subject('Tabs')
    table = encode_subject('TABLE')
    zebra = encode_subject('Zebra')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(tabs, 300))
        mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, support=(table, 100))
        mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(zebra, 999))
        mill_block(signing_key)

        dao = m.longest_chain.to_dao()
        rows = db.session.execute(dao.search_subjects('tab', 8)).all()
        subjects = [r.subject for r in rows]
        # prefix 'tab' matches 'Tabs' and 'TABLE' (case-insensitive), not 'Zebra'
        assert set(subjects) == {'Tabs', 'TABLE'}
        # canonical strings returned verbatim (not lowercased)
        assert 'Tabs' in subjects
        assert 'TABLE' in subjects


def test_search_ranks_by_total_and_caps(
    app, host, mill_block, requests_proxy, signing_key
):
    ta = encode_subject('Tango')
    tb = encode_subject('Tankard')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(ta, 100))
        mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(tb, 500))
        mill_block(signing_key)

        dao = m.longest_chain.to_dao()
        rows = db.session.execute(dao.search_subjects('tan', 8)).all()
        assert [r.subject for r in rows] == ['Tankard', 'Tango']  # 500 before 100
        top = db.session.execute(dao.search_subjects('tan', 1)).all()
        assert [r.subject for r in top] == ['Tankard']


def test_search_blank_query_returns_nothing(
    app, host, mill_block, requests_proxy, signing_key
):
    sub = encode_subject('Anything')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(sub, 100))
        mill_block(signing_key)
        dao = m.longest_chain.to_dao()
        assert db.session.execute(dao.search_subjects('', 8)).all() == []
        assert db.session.execute(dao.search_subjects('   ', 8)).all() == []


def test_search_escapes_like_metacharacters(
    app, host, mill_block, requests_proxy, signing_key
):
    pct = encode_subject('50% off')
    plain = encode_subject('500 dollars')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(pct, 100))
        mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(plain, 100))
        mill_block(signing_key)
        dao = m.longest_chain.to_dao()
        # '50%' must match literally — the '%' is escaped, not a wildcard
        rows = db.session.execute(dao.search_subjects('50%', 8)).all()
        assert [r.subject for r in rows] == ['50% off']
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_subject_search.py -k search -v`
Expected: FAIL — `AttributeError: 'ChainDAO' object has no attribute 'search_subjects'`.

- [ ] **Step 3: Implement `search_subjects` on `ChainDAO`**

In `src/gumptionchain/models.py`, add module-level constants and a helper near the top (after imports):

```python
_SEARCH_LIMIT_DEFAULT = 8
_SEARCH_LIMIT_MAX = 50


def _search_like_pattern(query: str) -> str:
    """A left-anchored LIKE pattern with metacharacters escaped (escape '\\')."""
    escaped = (
        query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    )
    return f'{escaped}%'
```

Add `search_subjects` to `ChainDAO`, immediately after `subject_leaderboard` (`models.py:893`):

```python
    def search_subjects(
        self, query: str, limit: int = _SEARCH_LIMIT_DEFAULT
    ) -> Select[Any]:
        q = (query or '').strip()
        bounded = max(1, min(int(limit), _SEARCH_LIMIT_MAX))

        def _leg(stake_col: Any, kind: StakeKind) -> Select[Any]:
            stmt = self.outflows.where(stake_col.is_not(None))
            stmt = stmt.where(self._unspent_clause())
            if q:
                stmt = stmt.where(
                    OutflowDAO.subject_lower.like(
                        _search_like_pattern(q.lower()), escape='\\'
                    )
                )
            else:
                # Blank query → match nothing; never dump the whole set.
                stmt = stmt.where(db.literal(False))
            stmt = stmt.with_only_columns(
                OutflowDAO.subject_plain.label('subject'),
                OutflowDAO.amount.label('amount'),
                db.literal(kind).label('kind'),
            )
            return stmt.order_by(None)

        opp = _leg(OutflowDAO.opposition, 'opposition')
        sup = _leg(OutflowDAO.support, 'support')
        union = opp.union_all(sup).subquery()
        stmt = db.select(
            union.c.subject,
            db.func.sum(
                db.case((union.c.kind == 'opposition', union.c.amount), else_=0)
            ).label('opposition'),
            db.func.sum(
                db.case((union.c.kind == 'support', union.c.amount), else_=0)
            ).label('support'),
            db.func.sum(union.c.amount).label('total'),
        )
        stmt = stmt.group_by(union.c.subject)
        stmt = stmt.order_by(db.desc('total'), union.c.subject)
        stmt = stmt.limit(bounded)
        return db.select(db.aliased(stmt.subquery()))  # type: ignore[no-any-return]
```

- [ ] **Step 4: Add the `Chain` delegate**

In `src/gumptionchain/chain.py`, after `subject_leaderboard` (`chain.py:559`):

```python
    def search_subjects(self, query: str, limit: int = 8) -> Select[Any]:
        return self.to_dao().search_subjects(query, limit)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_subject_search.py -v`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/models.py src/gumptionchain/chain.py tests/test_subject_search.py
git commit -m "feat(#287): ChainDAO.search_subjects prefix query"
```

---

## Task 3: node API endpoint `GET /api/subjects/search`

**Files:**
- Modify: `src/gumptionchain/api.py` (`SubjectSearchView` + route; ensure `db` import)
- Test: `tests/test_subject_search.py`

- [ ] **Step 1: Write the failing tests**

The repo's API tests sign requests as a configured signing key. Mirror the existing reader-endpoint test style. Append to `tests/test_subject_search.py`:

```python
def test_search_endpoint_returns_shape(
    app, host, mill_block, requests_proxy, signing_key, reader_signing_key
):
    tabs = encode_subject('Tabs')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(tabs, 250))
        mill_block(signing_key)

    client = ApiClient(host, reader_signing_key)
    resp = client.get('/api/subjects/search', params={'q': 'tab', 'limit': '8'})
    body = resp.json()
    assert resp.status_code == 200
    assert body['subjects'] == [
        {'subject': 'Tabs', 'opposition': 250, 'support': 0}
    ]
    assert 'as_of_block' in body


def test_search_endpoint_blank_query_is_empty(
    app, host, mill_block, requests_proxy, signing_key, reader_signing_key
):
    sub = encode_subject('Tabs')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(sub, 100))
        mill_block(signing_key)
    client = ApiClient(host, reader_signing_key)
    resp = client.get('/api/subjects/search', params={'q': '', 'limit': '8'})
    assert resp.status_code == 200
    assert resp.json()['subjects'] == []
```

Confirm the reader-key fixture name against `tests/conftest.py` (the canonical READER key). If it is exposed under a different fixture name, use that name; do not invent a new fixture.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_subject_search.py -k endpoint -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Implement the view + route**

In `src/gumptionchain/api.py`, ensure `db` is importable (add `from gumptionchain.database import db` with the other imports if not already present). Add the view alongside the other subject views (near `SubjectSupportView`, ~`api.py:775`):

```python
class SubjectSearchView(MethodView):
    def get(self, **kwargs: Any) -> Response:
        try:
            _, lc, _ = node_lc_dao()
            if lc is None:
                raise EmptyChainError()
            q = request.args.get('q', default='', type=str)
            limit = request.args.get('limit', default=8, type=int)
            if limit is None:
                limit = 8
            rows = db.session.execute(lc.search_subjects(q, limit)).all()
            subjects = [
                {
                    'subject': r.subject,
                    'opposition': int(r.opposition),
                    'support': int(r.support),
                }
                for r in rows
            ]
            return make_json_response(
                {'subjects': subjects, 'as_of_block': lc.block_hash}
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)
```

Register the route (near the other subject `add_url_rule` calls):

```python
blueprint.add_url_rule(
    '/subjects/search',
    view_func=authorize_reader(
        SubjectSearchView.as_view('subjects_search_reader')
    ),
    methods=['GET'],
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_subject_search.py -k endpoint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gumptionchain/api.py tests/test_subject_search.py
git commit -m "feat(#287): GET /api/subjects/search reader endpoint"
```

---

## Task 4: `ApiClient.get_subject_search`

**Files:**
- Modify: `src/gumptionchain/api_client.py`
- Test: `tests/test_subject_search.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subject_search.py`:

```python
def test_api_client_get_subject_search_round_trips(
    app, host, mill_block, requests_proxy, signing_key, reader_signing_key
):
    tabs = encode_subject('Tabs')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(tabs, 75))
        mill_block(signing_key)
    resp = ApiClient(host, reader_signing_key).get_subject_search('tab', 8)
    assert resp.status_code == 200
    assert resp.json()['subjects'] == [
        {'subject': 'Tabs', 'opposition': 75, 'support': 0}
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_subject_search.py -k api_client -v`
Expected: FAIL — `AttributeError: 'ApiClient' object has no attribute 'get_subject_search'`.

- [ ] **Step 3: Implement the method**

In `src/gumptionchain/api_client.py`, after `get_support_balance` (`api_client.py:332`):

```python
    def get_subject_search(
        self,
        query: str,
        limit: int = 8,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            '/api/subjects/search',
            params={'q': query, 'limit': str(limit)},
            timeout=timeout,
            raise_for_status=raise_for_status,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_subject_search.py -k api_client -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gumptionchain/api_client.py tests/test_subject_search.py
git commit -m "feat(#287): ApiClient.get_subject_search"
```

---

## Task 5: proxy route `GET /api/node/subject/search`

**Files:**
- Modify: `src/gumptionchain/node_proxy.py` (new route)
- Test: `tests/test_node_proxy.py` (new test + `FakeClient` method)

- [ ] **Step 1: Write the failing test**

In `tests/test_node_proxy.py`, add a `get_subject_search` method to `FakeClient` (after `get_opposition_balance`, ~line 41):

```python
    def get_subject_search(self, query, limit, *, raise_for_status=True):
        return self._resp('search', query, limit)
```

Add a test (after `test_subject_balances_normalizes_and_converts`):

```python
def test_subject_search_converts_grains_to_grit():
    client = FakeClient(
        search=FakeResponse(200, {'subjects': [
            {'subject': 'Tabs', 'opposition': 300, 'support': 150},
            {'subject': 'Tango', 'opposition': 0, 'support': 50},
        ], 'as_of_block': 'b1'})
    )
    resp = _app(client).get('/api/node/subject/search?q=ta&limit=8')
    assert resp.status_code == 200
    assert resp.get_json() == {'subjects': [
        {'subject': 'Tabs',
         'support': {'grit': 1.5, 'grains': 150},
         'opposition': {'grit': 3.0, 'grains': 300}},
        {'subject': 'Tango',
         'support': {'grit': 0.5, 'grains': 50},
         'opposition': {'grit': 0.0, 'grains': 0}},
    ]}
    # q and limit are forwarded to the client
    assert client.calls[0][1] == ('ta', '8')


def test_subject_search_maps_node_down_to_502():
    client = FakeClient(search=httpx.RequestError('boom'))
    resp = _app(client).get('/api/node/subject/search?q=ta')
    assert resp.status_code == 502
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_node_proxy.py -k subject_search -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Implement the proxy route**

In `src/gumptionchain/node_proxy.py`, add after the `subject_balances` route (~line 138):

```python
    @bp.get('/subject/search')
    def subject_search() -> Response:
        q = request.args.get('q', '')
        limit = request.args.get('limit', '8')
        r = _ok(_call(make_client().get_subject_search, q, limit))
        body = r.json()
        subjects = [
            {
                'subject': row['subject'],
                'support': _grit(int(row['support'])),
                'opposition': _grit(int(row['opposition'])),
            }
            for row in body.get('subjects', [])
        ]
        return jsonify({'subjects': subjects})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_node_proxy.py -k subject_search -v`
Expected: PASS.

- [ ] **Step 5: Full gate sweep**

Run: `uv run pytest`
Expected: all pass (existing + new), no unexpected skips.

Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean.

Run: `uv run mypy`
Expected: no new errors.

Run: `uv run gumptionchain db check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/node_proxy.py tests/test_node_proxy.py
git commit -m "feat(#287): node_proxy subject/search relay route"
```

---

## Final verification (after all tasks)

- [ ] Full sweep green: `uv run pytest`, `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy`, `uv run gumptionchain db check`.
- [ ] Dispatch a final whole-branch code review.
- [ ] Open the PR with the report-back for the gumptactoe session:
  1. **Proxy route:** `GET /api/node/subject/search?q=&limit=8` → `{"subjects":[{"subject","support":{grit,grains},"opposition":{grit,grains}}]}`. Tallies are `{grit, grains}` objects (NOT whole-int — deviation from the issue sketch, chosen for consistency with the existing balance routes; consumer formats whole-GRIT itself).
  2. **Match semantics:** prefix, case-insensitive (ASCII-fold via Python `.lower()`), index-accelerated; ranked by total stake desc; default limit 8, clamped 1–50; blank query → empty.
  3. **Index added:** `OutflowDAO.subject_plain` (canonical) + `subject_lower` (indexed `ix_outflow_subject_lower`); folded into the baseline migration; no backfill (greenfield).
  4. The **merge commit SHA** to pin in gumptactoe's `[tool.uv.sources]`.
  5. **Deviation:** substring left as a future toggle; `subject_lower` column + plain index used instead of a `lower()` expression index (avoids `db check` reflection noise; Unicode-correct folding).

---

## Plan self-review

- **Spec coverage:** Layer 1 → Task 1; Layer 2 → Task 2; Layer 3 → Task 3; Layer 4 → Tasks 4–5. Match semantics (prefix, case-insensitive, ranked, capped, blank→empty, LIKE-escape) → Task 2 tests. `_grit()` tally shape → Task 5. READER auth → Task 3. Report-back → Final. No gaps.
- **Type consistency:** `search_subjects(query: str, limit: int=8) -> Select[Any]` identical on `ChainDAO` and `Chain`; `get_subject_search(query, limit=8, ...)` matches the `FakeClient` stub `(query, limit, *, raise_for_status)` and the proxy call `_call(make_client().get_subject_search, q, limit)`; node JSON keys (`subject`/`opposition`/`support`/`as_of_block`) consistent across Tasks 3–5.
- **Deviation from spec noted:** the spec's "`lower()` expression index" is implemented as a stored `subject_lower` column + plain index (same effect, lower CI risk) — recorded in the report-back.
