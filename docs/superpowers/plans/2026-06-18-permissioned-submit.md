# Permissioned Submit + Per-Transactor Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make transaction submission permissioned-and-accountable: a per-transactor in-flight cap on the mempool plus node-local per-app submission attribution and a stats leaderboard, building on the existing TRANSACTOR role.

**Architecture:** A new node-local `submission` table attributes each newly-admitted txn to its authenticated submitter. The submit view enforces an in-flight cap (count of a transactor's still-pending submissions) for TRANSACTOR-role callers only; a READER stats endpoint + browser page aggregate the table. No consensus/validation/txid changes; the gate itself is operator config + a docs rewrite.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0 (`Mapped[]`), Flask-Migrate/Alembic, pytest, uv.

---

## Background the engineer needs

- **The submit view** is `TxnView.post` (`src/gumptionchain/api.py:420-448`), registered via `authorize_transactor`. `authorize` (`api.py:256-291`) verifies the signature, then injects `kwargs['_address']` (the gc-sig address) and `kwargs['_role']` (a `Role` enum) into the view. `Role` is defined in `api.py:191`.
- `node.receive_transaction(...)` returns the txn object **only when newly admitted** to the pending pool; it returns `None` for a duplicate/already-known txn (that's how the view picks its 200/201/202 status). So `txn is not None` == "newly admitted".
- A full global pool raises `MempoolFullError` → `503` (unchanged). The new cap is a separate `429`.
- **Pending pool:** `PendingTxnDAO` (`models.py:1284`), table `pending_txn`, has a unique-indexed `txid` column. "Still pending" == a row exists in `pending_txn` for that txid.
- **Config:** `EnvAppSettings` dataclass (`src/gumptionchain/config.py:31`), fields like `MAX_PENDING_TXNS: int = field(default=10000)`. Values land in `app.config` via `from_object`; env override is `GC_<NAME>`.
- **Leaderboard pattern:** `ChainDAO.subject_leaderboard` (`models.py:891`) returns a `Select`; the browser `subjects_view` (`browser.py:162`) does `paginate_rows(lc.subject_leaderboard())` → renders `templates/subjects.html`. Mirror this for stats.
- **Greenfield migrations:** fold new tables into the baseline `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py` — do NOT add a revision. Tests use `db.create_all()`; `gumptionchain db check` enforces parity on a fresh DB.
- **Test fixtures** (`tests/conftest.py`): `app`, `runner`, `host`, `mill_block`, `requests_proxy`, and signed keys `signing_key` (ADMIN), `transactor_signing_key` (TRANSACTOR), `miller_signing_key` (MILLER), `reader_signing_key` (READER). `app.config[...]` is mutable inside a test for per-test overrides.
- **Commands:** `uv run pytest <path>`, `uv run mypy`, `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run gumptionchain db check`.

---

## Task 1: config — `MAX_PENDING_PER_TRANSACTOR`

**Files:**
- Modify: `src/gumptionchain/config.py`
- Test: `tests/test_config.py` (create if absent; else append)

- [ ] **Step 1: Write the failing test**

Append to (or create) `tests/test_config.py`:

```python
def test_max_pending_per_transactor_default(app):
    assert app.config['MAX_PENDING_PER_TRANSACTOR'] == 100
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_config.py::test_max_pending_per_transactor_default -v`
Expected: FAIL — `KeyError: 'MAX_PENDING_PER_TRANSACTOR'`.

- [ ] **Step 3: Add the field**

In `src/gumptionchain/config.py`, in `EnvAppSettings`, after the `MAX_PENDING_TXNS` line:

```python
    MAX_PENDING_PER_TRANSACTOR: int = field(default=100)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_config.py::test_max_pending_per_transactor_default -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gumptionchain/config.py tests/test_config.py
git commit -m "feat: add MAX_PENDING_PER_TRANSACTOR config (default 100)"
```

---

## Task 2: `SubmissionDAO` — table, baseline migration, `record`, `pending_count`

**Files:**
- Modify: `src/gumptionchain/models.py` (new `SubmissionDAO`)
- Modify: `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py`
- Test: `tests/test_submission.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_submission.py`:

```python
import datetime

from gumptionchain.database import db
from gumptionchain.models import PendingTxnDAO, SubmissionDAO


def _pending(txid):
    PendingTxnDAO(
        txid=txid, timestamp=datetime.datetime(2026, 1, 1), json_data='{}'
    ).commit()


def test_record_is_first_submitter_wins(app):
    with app.app_context():
        SubmissionDAO.record('txA', 'GCappOneGC')
        SubmissionDAO.record('txA', 'GCappTwoGC')  # later submitter ignored
        rows = db.session.execute(db.select(SubmissionDAO)).scalars().all()
        assert len(rows) == 1
        assert rows[0].txid == 'txA'
        assert rows[0].transactor_address == 'GCappOneGC'


def test_pending_count_only_counts_still_pending(app):
    with app.app_context():
        # txA + txB submitted by app one; only txA is still in the pool.
        _pending('txA')
        SubmissionDAO.record('txA', 'GCappOneGC')
        SubmissionDAO.record('txB', 'GCappOneGC')  # not in pending_txn
        SubmissionDAO.record('txC', 'GCappTwoGC')  # different transactor
        _pending('txC')
        assert SubmissionDAO.pending_count('GCappOneGC') == 1
        assert SubmissionDAO.pending_count('GCappTwoGC') == 1
        assert SubmissionDAO.pending_count('GCnobodyGC') == 0


def test_transactor_leaderboard_ranks_by_count(app):
    with app.app_context():
        for txid in ('t1', 't2', 't3'):
            SubmissionDAO.record(txid, 'GCbusyGC')
        SubmissionDAO.record('t4', 'GCquietGC')
        rows = db.session.execute(SubmissionDAO.transactor_leaderboard()).all()
        by_addr = {r.address: r for r in rows}
        assert by_addr['GCbusyGC'].count == 3
        assert by_addr['GCquietGC'].count == 1
        assert rows[0].address == 'GCbusyGC'  # ranked by count desc
        assert by_addr['GCbusyGC'].last_submit_at is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_submission.py -v`
Expected: FAIL — `ImportError`/`AttributeError` (no `SubmissionDAO`).

- [ ] **Step 3: Add the model**

In `src/gumptionchain/models.py`, add (near the other DAOs; `datetime`, `Integer`, `String`, `DateTime`, `Select`, `db`, `Any` are already imported):

```python
class SubmissionDAO(Base):
    __tablename__ = 'submission'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    txid: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    transactor_address: Mapped[str] = mapped_column(String(100), index=True)
    submitted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, index=True, default=datetime.datetime.utcnow
    )

    @classmethod
    def record(cls, txid: str, transactor_address: str) -> None:
        # First-submitter-wins: the unique txid is the dedupe key. Only the
        # first relay to get a txn newly admitted is recorded as its origin.
        if db.session.scalar(
            db.select(cls.id).where(cls.txid == txid)
        ) is not None:
            return
        db.session.add(
            cls(txid=txid, transactor_address=transactor_address)
        )
        db.session.commit()

    @classmethod
    def pending_count(cls, transactor_address: str) -> int:
        # In-flight footprint: this transactor's submissions whose txid is
        # still in the pending pool (self-clears as txns confirm/expire).
        stmt = (
            db.select(db.func.count())
            .select_from(cls)
            .join(PendingTxnDAO, cls.txid == PendingTxnDAO.txid)
            .where(cls.transactor_address == transactor_address)
        )
        return db.session.scalar(stmt) or 0

    @classmethod
    def transactor_leaderboard(cls) -> Select[Any]:
        stmt = db.select(
            cls.transactor_address.label('address'),
            db.func.count().label('count'),
            db.func.max(cls.submitted_at).label('last_submit_at'),
        )
        stmt = stmt.group_by(cls.transactor_address)
        stmt = stmt.order_by(db.desc('count'), cls.transactor_address)
        return stmt  # type: ignore[no-any-return]
```

- [ ] **Step 4: Fold the table into the baseline migration**

In `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py`, add a `create_table` block alongside the other tables (e.g. after the `pending_ioflow` table create), and the matching `drop_table` in `downgrade()`:

```python
    op.create_table('submission',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('txid', sa.String(length=100), nullable=False),
    sa.Column('transactor_address', sa.String(length=100), nullable=False),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_submission'))
    )
    with op.batch_alter_table('submission', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_submission_txid'), ['txid'], unique=True)
        batch_op.create_index(batch_op.f('ix_submission_transactor_address'), ['transactor_address'], unique=False)
        batch_op.create_index(batch_op.f('ix_submission_submitted_at'), ['submitted_at'], unique=False)
```

In `downgrade()` add (mirroring the other drops): `op.drop_table('submission')` (with its `drop_index` calls if the file's downgrade lists indexes explicitly — follow the file's existing downgrade style).

- [ ] **Step 5: Run tests + schema parity**

Run: `uv run pytest tests/test_submission.py -v` → PASS (all three).
Run: `TMPDB=$(mktemp -u --suffix=.sqlite); FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///$TMPDB" uv run gumptionchain init && FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///$TMPDB" uv run gumptionchain db check; rm -f $TMPDB`
Expected: "No new upgrade operations detected." (A stale local `gumptionchain.sqlite` gives a false positive — use this fresh-DB check.)
Run: `uv run mypy` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/models.py src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py tests/test_submission.py
git commit -m "feat: SubmissionDAO + submission table (attribution, pending_count, leaderboard)"
```

---

## Task 3: in-flight cap + attribution in the submit view

**Files:**
- Modify: `src/gumptionchain/api.py` (`TxnView.post` + import)
- Test: `tests/test_submit_cap.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_submit_cap.py`:

```python
from gumptionchain.api_client import ApiClient
from gumptionchain.database import db
from gumptionchain.models import SubmissionDAO


def _post_transfer(host, chain, key, to_key):
    # Build + post a self/transfer-style txn that gets newly admitted.
    txn = chain.create_transfer(key, 1, to_key.address)
    txn.sign()
    return ApiClient(host, key).post(
        f'/api/transaction/{txn.txid}',
        data=txn.to_json(),
        headers={'Content-Type': 'application/json'},
        raise_for_status=False,
    )


def test_transactor_over_cap_gets_429(
    app, host, mill_block, requests_proxy, transactor_signing_key, signing_key
):
    with app.app_context():
        app.config['MAX_PENDING_PER_TRANSACTOR'] = 1
        m, _ = mill_block(transactor_signing_key)  # fund the transactor
        lc = m.longest_chain
        r1 = _post_transfer(host, lc, transactor_signing_key, signing_key)
        assert r1.status_code in (200, 201, 202)
        # second in-flight submission by the same transactor exceeds cap=1
        r2 = _post_transfer(host, lc, transactor_signing_key, signing_key)
        assert r2.status_code == 429
        assert 'quota' in r2.json()['error']


def test_under_cap_admits_and_records_submission(
    app, host, mill_block, requests_proxy, transactor_signing_key, signing_key
):
    with app.app_context():
        app.config['MAX_PENDING_PER_TRANSACTOR'] = 10
        m, _ = mill_block(transactor_signing_key)
        lc = m.longest_chain
        r = _post_transfer(host, lc, transactor_signing_key, signing_key)
        assert r.status_code in (200, 201, 202)
        rows = db.session.execute(db.select(SubmissionDAO)).scalars().all()
        assert len(rows) == 1
        assert rows[0].transactor_address == transactor_signing_key.address


def test_miller_is_exempt_from_cap(
    app, host, mill_block, requests_proxy, miller_signing_key, signing_key
):
    with app.app_context():
        app.config['MAX_PENDING_PER_TRANSACTOR'] = 1
        m, _ = mill_block(miller_signing_key)
        lc = m.longest_chain
        r1 = _post_transfer(host, lc, miller_signing_key, signing_key)
        r2 = _post_transfer(host, lc, miller_signing_key, signing_key)
        # MILLER role bypasses the cap entirely
        assert r1.status_code in (200, 201, 202)
        assert r2.status_code in (200, 201, 202)
```

(Note: if `create_transfer` requires more funds than one coinbase reward for two txns, mill an extra block per submission, mirroring `tests/test_subject_leaderboard.py`'s mill-between-stakes pattern. The behavior under test is the cap/role/attribution, not balance.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_submit_cap.py -v`
Expected: FAIL — second TRANSACTOR submission is admitted (no cap yet), no `429`; no `submission` rows.

- [ ] **Step 3: Implement the cap + attribution**

In `src/gumptionchain/api.py`, add the import (with the other model imports):

```python
from gumptionchain.models import SubmissionDAO
```

Modify `TxnView.post` — add the cap check before `receive_transaction` and the attribution after admission:

```python
    def post(
        self,
        txid: str,
        process: str | bool = False,  # noqa: FBT001
        **kwargs: Any,
    ) -> Response:
        try:
            process = process == 'process'
            if not process:
                process = not current_app.config.get('API_ASYNC_PROCESSING')
            node, _, _ = node_lc_dao()
            address = kwargs['_address']
            role = kwargs['_role']
            cap = current_app.config['MAX_PENDING_PER_TRANSACTOR']
            if (
                role == Role.TRANSACTOR
                and SubmissionDAO.pending_count(address) >= cap
            ):
                return make_json_response(
                    {'error': 'transactor pending quota exceeded'}, 429
                )
            vhosts = visited_hosts()
            received = now_iso()
            txn = node.receive_transaction(
                txid, request.data, visited_hosts=vhosts, process=process
            )
            if txn is not None:
                SubmissionDAO.record(txid, address)
                if process is False:
                    queue_txn_post_process(txn, vhosts)
        except MempoolFullError:
            return make_json_response({'error': 'mempool full'}, 503)
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)
        status_code = 200 if txn is None else 201 if process else 202
        return make_json_response(
            {'received': received}, status_code=status_code
        )
```

(The only behavioral additions: the `429` cap gate, the `SubmissionDAO.record` on admission, and folding the existing `queue_txn_post_process` call under the same `txn is not None` block — it already only fired when `txn is not None`.)

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_submit_cap.py -v` → PASS (all three).

- [ ] **Step 5: Gates**

Run: `uv run mypy` → clean. `uv run ruff check src tests` + `uv run ruff format --check src tests` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/api.py tests/test_submit_cap.py
git commit -m "feat: per-transactor in-flight cap (429) + submission attribution"
```

---

## Task 4: stats endpoint + ApiClient method

**Files:**
- Modify: `src/gumptionchain/api.py` (new `TransactorStatsView` + route)
- Modify: `src/gumptionchain/api_client.py` (`get_transactor_stats`)
- Test: `tests/test_submit_cap.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_submit_cap.py`:

```python
def test_transactor_stats_endpoint(
    app, host, mill_block, requests_proxy,
    transactor_signing_key, reader_signing_key, signing_key,
):
    with app.app_context():
        app.config['MAX_PENDING_PER_TRANSACTOR'] = 10
        m, _ = mill_block(transactor_signing_key)
        _post_transfer(host, m.longest_chain, transactor_signing_key, signing_key)

    body = ApiClient(host, reader_signing_key).get_transactor_stats().json()
    addrs = {t['address']: t for t in body['transactors']}
    assert transactor_signing_key.address in addrs
    assert addrs[transactor_signing_key.address]['count'] == 1
    assert addrs[transactor_signing_key.address]['last_submit_at'] is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_submit_cap.py::test_transactor_stats_endpoint -v`
Expected: FAIL — `AttributeError: 'ApiClient' object has no attribute 'get_transactor_stats'` (and the route 404s).

- [ ] **Step 3: Implement endpoint + client**

In `src/gumptionchain/api.py`, add the view near the other read views and register it:

```python
class TransactorStatsView(MethodView):
    def get(self, **kwargs: Any) -> Response:
        try:
            rows = db.session.execute(
                SubmissionDAO.transactor_leaderboard()
            ).all()
            transactors = [
                {
                    'address': r.address,
                    'count': int(r.count),
                    'last_submit_at': (
                        r.last_submit_at.isoformat()
                        if r.last_submit_at is not None
                        else None
                    ),
                }
                for r in rows
            ]
            return make_json_response({'transactors': transactors})
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/stats/transactors',
    view_func=authorize_reader(
        TransactorStatsView.as_view('transactor_stats_reader')
    ),
    methods=['GET'],
)
```

(Ensure `from gumptionchain.database import db` is imported in `api.py` — add it with the other imports if not already present.)

In `src/gumptionchain/api_client.py`, after `get_subject_search`:

```python
    def get_transactor_stats(
        self,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            '/api/stats/transactors',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_submit_cap.py::test_transactor_stats_endpoint -v` → PASS.

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy` + `uv run ruff check src tests` + `uv run ruff format --check src tests` → clean.

```bash
git add src/gumptionchain/api.py src/gumptionchain/api_client.py tests/test_submit_cap.py
git commit -m "feat: GET /api/stats/transactors + ApiClient.get_transactor_stats"
```

---

## Task 5: browser `/stats` explorer page

**Files:**
- Modify: `src/gumptionchain/browser.py` (new `stats_view`)
- Create: `src/gumptionchain/templates/stats.html`
- Modify: `src/gumptionchain/templates/_explorer_home.html` (nav link)
- Test: `tests/test_stats_page.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_stats_page.py`:

```python
from gumptionchain.api_client import ApiClient


def test_stats_page_lists_transactors(
    app, host, client, mill_block, requests_proxy,
    transactor_signing_key, signing_key,
):
    with app.app_context():
        app.config['MAX_PENDING_PER_TRANSACTOR'] = 10
        m, _ = mill_block(transactor_signing_key)
        txn = m.longest_chain.create_transfer(
            transactor_signing_key, 1, signing_key.address
        )
        txn.sign()
        ApiClient(host, transactor_signing_key).post(
            f'/api/transaction/{txn.txid}',
            data=txn.to_json(),
            headers={'Content-Type': 'application/json'},
        )
    resp = client.get('/stats')
    assert resp.status_code == 200
    assert transactor_signing_key.address.encode() in resp.data
```

(`client` is the Flask test client fixture; confirm its name in `tests/conftest.py` — use whatever the existing browser-view tests use, e.g. `tests/test_subjects_page.py`.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_stats_page.py -v`
Expected: FAIL — `/stats` 404s.

- [ ] **Step 3: Implement the view, template, nav**

In `src/gumptionchain/browser.py`, mirroring `subjects_view`:

```python
@blueprint.route('/stats')
def stats_view() -> Any:
    try:
        stats_page = paginate_rows(SubmissionDAO.transactor_leaderboard())
    except HTTPException as e:
        return e
    except Exception as e:
        current_app.logger.exception(e)
        abort(500)
    return render_template(
        'stats.html', title='Submission stats', stats_page=stats_page
    )
```

(Add `from gumptionchain.models import SubmissionDAO` to `browser.py` imports.)

Create `src/gumptionchain/templates/stats.html`:

```html
{% extends "base.html" %}
{% from "_pagination.html" import render_pagination %}

{% block content -%}
<div class="container-fluid">
  <div class="row my-3"><div class="col">
    <div class="card bg-light"><div class="card-body">
      <div class="card-title h5">Submission stats</div>
      {%- if stats_page and stats_page.total %}
      <table class="table table-hover">
        <thead><tr><th>#</th><th>Transactor</th><th>Submissions</th><th>Last submit</th></tr></thead>
        <tbody>
        {%- for row in stats_page.items %}
          <tr>
            <td>{{ loop.index + (stats_page.page - 1) * stats_page.per_page }}</td>
            <td class="font-monospace">{{ row.address }}</td>
            <td>{{ row.count }}</td>
            <td>{{ row.last_submit_at }}</td>
          </tr>
        {%- endfor %}
        </tbody>
      </table>
      {{ render_pagination(stats_page, 'browser.stats_view') }}
      {%- else %}
      <p>No submissions recorded yet.</p>
      {%- endif %}
    </div></div>
  </div></div>
</div>
{%- endblock %}
```

In `src/gumptionchain/templates/_explorer_home.html`, next to the existing `Subjects` link (line ~65), add:

```html
          <a href="{{ url_for('browser.stats_view') }}">Submission stats</a>
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_stats_page.py -v` → PASS.

- [ ] **Step 5: Gates + commit**

Run: `uv run mypy` + `uv run ruff check src tests` + `uv run ruff format --check src tests` → clean.

```bash
git add src/gumptionchain/browser.py src/gumptionchain/templates/stats.html src/gumptionchain/templates/_explorer_home.html tests/test_stats_page.py
git commit -m "feat: /stats explorer page (submission leaderboard)"
```

---

## Task 6: docs — permissioned-submit posture in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` ("Open transacting & anti-spam (EGU)" section)

- [ ] **Step 1: Rewrite the section**

Replace the existing "Open transacting & anti-spam (EGU)" section in `CLAUDE.md` so it documents the new posture. The new text must state:
- `TRANSACTOR_ADDRESSES` should be an **exact allowlist** of relay addresses (the hub, each game's house key); the `"*"` wildcard is no longer the recommended posture for submission. `READER_ADDRESSES` may still use `"*"` (open reads, gated writes).
- A **per-transactor in-flight cap** (`GC_MAX_PENDING_PER_TRANSACTOR`, default 100) bounds how many unconfirmed txns one relay may hold; over-cap submits get `429` (distinct from the global `MAX_PENDING_TXNS` `503`). The cap applies to TRANSACTOR-role submitters only; MILLER/ADMIN (infra/gossip) are exempt.
- Each newly-admitted txn is attributed to its submitter in the node-local `submission` table (not consensus); `GET /api/stats/transactors` and the `/stats` page surface per-relay submission counts.
- The cap runs **after** signature verification, so it bounds the mempool, not request CPU; keep the reverse-proxy per-IP rate limit, and the hashcash submit-PoW (#151) remains the separate, still-unbuilt CPU-DoS escalation.

Keep it concise (a paragraph or two + a short bullet list), matching the surrounding CLAUDE.md style.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: permissioned-submit posture + per-transactor cap/stats"
```

---

## Final verification (after all tasks)

- [ ] `uv run pytest` → all pass, no unexpected new skips.
- [ ] `uv run ruff check src tests` + `uv run ruff format --check src tests` → clean.
- [ ] `uv run mypy` → clean.
- [ ] Fresh-DB `gumptionchain init` + `gumptionchain db check` → "No new upgrade operations detected."
- [ ] Dispatch a final whole-branch code review.
- [ ] Open the PR. Body must flag: this is a **posture change** (operators must set an exact `GC_TRANSACTOR_ADDRESSES` — dropping `"*"` — to actually gate submission; the code ships the cap/attribution/stats but the gate is the operator's config choice), and note the deferred items (cross-gossip origin propagation, `?since=` windowing, app-name labels, #151 submit-PoW).

---

## Plan self-review

- **Spec coverage:** gate (config + docs) → Task 1 + Task 6; attribution table + DAO → Task 2; in-flight cap (TRANSACTOR-only, 429, attribution-on-admission) → Task 3; stats endpoint + ApiClient → Task 4; `/stats` page → Task 5. All spec sections covered.
- **Type consistency:** `SubmissionDAO.record(txid, transactor_address)`, `pending_count(transactor_address) -> int`, `transactor_leaderboard() -> Select` are defined in Task 2 and used identically in Tasks 3–5. Leaderboard row labels (`address`, `count`, `last_submit_at`) are consistent across the DAO, the endpoint (Task 4), and the template (Task 5). `MAX_PENDING_PER_TRANSACTOR` name consistent (Task 1 ↔ Task 3). `Role.TRANSACTOR` comparison matches `kwargs['_role']` (a `Role`) set by `authorize`.
- **Placeholder scan:** no TBDs; every code step has complete code. The two "confirm the fixture name" notes (Task 5 `client`, Task 3 funding) point at concrete existing test files to copy from, not vague instructions.
- **Deviation note:** the gate is intentionally config+docs (no code task creates the allowlist) — this matches the spec's "no app code for the gate" decision; Task 6 documents it and the PR body flags the operator action.
