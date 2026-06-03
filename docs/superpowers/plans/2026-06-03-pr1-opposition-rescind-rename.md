# PR 1 — Opposition / Rescind pure rename — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the `subject` (cancel-flavored) outflow kind to `opposition` and the `forgive` outflow kind to `rescind` across the entire codebase, with zero behavior change.

**Architecture:** Pure identifier rename. The on-chain serialization (`Outflow.data_csv`) keeps field *positions*, so the rename produces byte-identical block hashes — this PR is **not** a hard fork and does not touch consensus. The coinbase sentiment weights (`schadenfreude`/`grace`/`mudita`) are unchanged; only the fields they read are renamed. The follow-on PR 2 (support rescindability, `rescind_kind`, `regret`, `mudita`→½) carries all consensus changes.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0 (`Mapped[]`), Pydantic v2, Alembic/Flask-Migrate, pytest, uv, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-06-03-opposition-support-rescind-design.md`

---

## CRITICAL GUARDRAIL — "subject" is two different things

The word `subject` is overloaded. **Only the opposition outflow kind is renamed.** The
target-string noun is preserved. Get this wrong and you will break unrelated code.

### RENAME — the opposition outflow *kind* (`subject` → `opposition`)
- `Outflow.subject` field, `OutflowModel.subject` field, `OutflowDAO.subject` column
- `Outflow.schadenfreude` reads `self.subject`
- `Outflow(subject=...)` / `OutflowDAO(subject=...)` keyword arguments
- `.subject` attribute access on an outflow object (e.g. `o.subject`, `outflow.subject`, `ioflow` has none)
- `Chain.create_subject` → `create_opposition`
- `ChainDAO.subject_balance` → `opposition_balance`
- API `SubjectTxnView`, `SubjectBalanceView`, routes `/transaction/subject`, `/subject/<…>/balance`
- `ApiClient.get_subject_transaction`, `get_subject_balance`
- CLI `txn subject`, `subject balance`

### RENAME — the forgive outflow *kind* (`forgive` → `rescind`)
- `Outflow.forgive`, `OutflowModel.forgive`, `OutflowDAO.forgive` column
- `Outflow.grace` reads `self.forgive`
- `Outflow(forgive=...)` keyword args, `.forgive` attribute access
- `Chain.create_forgive` → `create_rescind`
- `ChainDAO.unforgiven_outflows` → `unrescinded_outflows`, `unforgiven_address_outflows` → `unrescinded_address_outflows`
- API `ForgiveTxnView`, route `/transaction/forgive`
- `ApiClient.get_forgive_transaction`
- CLI `txn forgive`

### RENAME — support balance accessor (name only; logic unchanged in PR 1)
- `ChainDAO.subject_support` → `support_balance`, `Chain.subject_support` → `support_balance`
- `ApiClient.get_subject_support` → `get_support_balance`
- (route `/subject/<…>/support`, CLI `subject support`, and `Outflow.support`/`mudita` are **unchanged**)

### PRESERVE — "subject" the target string (DO NOT TOUCH)
- `encode_subject`, `decode_subject`, `validate_subject`, `validate_raw_subject`, `_check_raw_subject`, `_RawSubjectField`, the `Subject` type, `MIN/MAX_SUBJECT_LENGTH`
- `SubjectConverter`, the URL converter key `'subject'`, the path converter `<subject:subject>`, `human_subject` template filter
- the `'subject'` **JSON query-param name** in API requests/clients
- `SubjectTxnQueryModel` (shared model carrying the subject string — keep the name)
- the `subject_cli` command group (`gc subject …`) and `SubjectSupportView` class name
- every function parameter / local variable named `subject` that holds the target string
- test fixtures `subject`, `subject_raw`; constants `SUBJECT_1`, `SUBJECT_2`

When in doubt: if the value is a UTF-8 target string, it's the noun → **preserve**. If it's an
outflow destination field / kind / the txn that creates one → **rename**.

---

## File map (PR 1)

| File | Change |
|---|---|
| `src/gumptionchain/payload.py` | rename `Outflow`/`OutflowModel` fields, `data_csv`, `schadenfreude`/`grace` reads, `validate_destinations` |
| `src/gumptionchain/models.py` | rename `OutflowDAO` columns + ctor; rename DAO methods `subject_balance`/`subject_support`/`unforgiven_*` |
| `src/gumptionchain/transaction.py` | rename `Outflow(subject=…, forgive=…)` kwargs in `from_dao`/`from_db` |
| `src/gumptionchain/chain.py` | rename `create_subject`/`create_forgive`; `subject_support` wrapper; `validate_block_txn`/`validate_txn_inflow` attr reads + Outflow kwargs + local dict |
| `src/gumptionchain/api.py` | rename views, routes, endpoint names, `lc.*` call sites |
| `src/gumptionchain/api_client.py` | rename client methods + request paths |
| `src/gumptionchain/command.py` | rename CLI verbs + command fns + client call sites |
| `src/gumptionchain/templates/transaction.html` | `o.subject`→`o.opposition`, `o.forgive`→`o.rescind` |
| `src/gumptionchain/migrations/versions/<new>.py` | new migration: rename `outflow` columns |
| `tests/conftest.py` + `tests/test_*.py` | update kwargs/attr/method/route/verb references |

Each task renames one identifier (or one tightly-coupled set) **across its definition, every
call site, and its tests in the same commit**, so the full suite stays green after every task.

---

## Task 1: Rename the two outflow-kind fields (`subject`→`opposition`, `forgive`→`rescind`)

This is the atomic core: the field rename touches every layer at once, because a half-renamed
field leaves dangling attribute access. Do it all in one commit.

**Files:**
- Modify: `src/gumptionchain/payload.py`
- Modify: `src/gumptionchain/models.py` (columns + ctor only; method renames are Tasks 2–3)
- Modify: `src/gumptionchain/transaction.py`
- Modify: `src/gumptionchain/chain.py` (Outflow kwargs + attr reads in `validate_block_txn`/`validate_txn_inflow` and `create_*` bodies; method *names* stay until Tasks 2–3)
- Modify: `src/gumptionchain/templates/transaction.html`
- Create: `src/gumptionchain/migrations/versions/<generated>.py`
- Modify: `tests/conftest.py`, `tests/test_payload.py`, `tests/test_transaction.py`, `tests/test_models.py`, and any other test building `Outflow(subject=…/forgive=…)` or reading `.subject`/`.forgive`

- [ ] **Step 1: Update the payload unit tests to the new field names (red first)**

In `tests/test_payload.py`, change every `Outflow(subject=…)` → `Outflow(opposition=…)`,
`Outflow(forgive=…)` → `Outflow(rescind=…)`, and every `.subject`/`.forgive` attribute
assertion on an outflow → `.opposition`/`.rescind`. The `schadenfreude`/`grace`/`mudita`
assertions and their expected values stay the same (weights unchanged in PR 1). Example:

```python
def test_outflow_schadenfreude(subject):
    outflow = Outflow(amount=9, opposition=subject)   # was subject=subject
    assert outflow.schadenfreude == 4

def test_outflow_grace(subject):
    outflow = Outflow(amount=9, rescind=subject)       # was forgive=subject
    assert outflow.grace == 4
```

- [ ] **Step 2: Run payload tests to confirm they fail**

Run: `uv run pytest tests/test_payload.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'opposition'`.

- [ ] **Step 3: Rename the fields in `payload.py`**

Edit the `Outflow` dataclass fields and `data_csv` (keep positions — byte-identical output):

```python
@dataclass
class Outflow:
    amount: int | None = None
    address: str | None = None
    opposition: str | None = None
    rescind: str | None = None
    support: str | None = None

    @property
    def data_csv(self) -> str:
        return ','.join(
            [
                str(self.amount),
                self.address if self.address is not None else '',
                self.opposition if self.opposition is not None else '',
                self.rescind if self.rescind is not None else '',
                self.support if self.support is not None else '',
            ]
        )

    @property
    def schadenfreude(self) -> int:
        if self.opposition is not None and self.amount is not None:
            return int(self.amount / 2)
        return 0

    @property
    def grace(self) -> int:
        if self.rescind is not None and self.amount is not None:
            return int(self.amount / 2)
        return 0
```

(`mudita` is unchanged.) Then edit `OutflowModel`:

```python
class OutflowModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    amount: int = Field(ge=1)
    address: AddressType | None = None
    opposition: Subject | None = None
    rescind: Subject | None = None
    support: Subject | None = None

    @model_validator(mode='after')
    def validate_destinations(self) -> Self:
        options = [
            v
            for v in (self.opposition, self.rescind, self.support)
            if v is not None
        ]
        if not (
            (self.address and not options)
            or (options and len(options) == 1 and not self.address)
        ):
            raise ValueError(INVALID_DESTINATION_MSG)
        return self
```

- [ ] **Step 4: Run payload tests to confirm they pass**

Run: `uv run pytest tests/test_payload.py -q`
Expected: PASS.

- [ ] **Step 5: Rename the `OutflowDAO` columns + constructor in `models.py`**

Rename the two mapped columns and their constructor params/assignments (keep `support`):

```python
    opposition: Mapped[str | None] = mapped_column(String(500))
    rescind: Mapped[str | None] = mapped_column(String(500))
    support: Mapped[str | None] = mapped_column(String(500))
```

In `OutflowDAO.__init__`, rename params `subject`→`opposition`, `forgive`→`rescind` and the
corresponding `self.opposition = opposition` / `self.rescind = rescind` assignments. Do **not**
touch the `subject_balance`/`subject_support`/`unforgiven_*` *methods* yet (Tasks 2–3).

- [ ] **Step 6: Rename the `Outflow(...)` kwargs in `transaction.py`**

In `from_dao` and `from_db` (the two `Outflow(...)` constructions), rename:

```python
                    opposition=outflow.opposition,   # was subject=outflow.subject
                    rescind=outflow.rescind,          # was forgive=outflow.forgive
```
and in the `from_dao` DAO variant: `opposition=outflow_dao.opposition`, `rescind=outflow_dao.rescind`.

- [ ] **Step 7: Rename Outflow kwargs + attribute reads in `chain.py` (method names unchanged here)**

In `create_subject`'s body: `Outflow(amount=amount, subject=subject)` → `Outflow(amount=amount, opposition=subject)`.
In `create_forgive`'s body: `Outflow(amount=amount, forgive=subject)` → `Outflow(amount=amount, rescind=subject)`, and the excess `Outflow(amount=balance - amount, subject=subject)` → `opposition=subject`.
(`subject` here is the target-string variable — keep it; only the kwarg name changes.)

In `validate_block_txn`, rename the attribute reads and the local accumulator for clarity:

```python
        # add inflow amounts
        opposition_amounts: dict[str, int] = {}
        other_amounts = 0
        for i in txn.inflows:
            amount, subject = self.validate_txn_inflow(
                block, txn, i, txn_in_block=txn_in_block
            )
            if subject:
                opposition_amount: int | None = opposition_amounts.get(subject, 0)
                opposition_amounts[subject] = (opposition_amount or 0) + amount
            else:
                other_amounts += amount
        # subtract outflow amounts
        for o in txn.outflows:
            if o.rescind:
                rescind_amount = opposition_amounts.get(o.rescind, 0)
                opposition_amounts[o.rescind] = rescind_amount - (o.amount or 0)
            elif o.opposition:
                opposition_amount = opposition_amounts.get(o.opposition)
                if opposition_amount and opposition_amount > 0:
                    if (o.amount or 0) > opposition_amount:
                        opposition_amounts[o.opposition] = 0
                        other_amounts -= (o.amount or 0) - opposition_amount
                    else:
                        opposition_amounts[o.opposition] = opposition_amount - (
                            o.amount or 0
                        )
                else:
                    other_amounts -= o.amount or 0
            else:
                other_amounts -= o.amount or 0
        if other_amounts != 0:
            raise ImbalancedTransactionError()
        for _, amount in opposition_amounts.items():
            if amount != 0:
                raise ImbalancedTransactionError()
```

In `validate_txn_inflow`, rename the guard attribute read (forgive→rescind; support stays):

```python
        # inflow's outflow can't be for rescind or support
        if ioflow.rescind is not None or ioflow.support is not None:
            raise InvalidInflowOutflowError()
```

(The `subject` return value of `validate_txn_inflow` is the opposition target string and stays
named `subject` for now; it is internal.)

- [ ] **Step 8: Update the template**

`src/gumptionchain/templates/transaction.html` lines ~101–102:

```html
                <td class="col-2">{{ o.opposition }}{% if o.opposition %} <em>({{ o.opposition | human_subject }})</em>{% endif %}</td>
                <td class="col-2">{{ o.rescind }}{% if o.rescind %} <em>({{ o.rescind | human_subject }})</em>{% endif %}</td>
```
(Line ~103 `o.support` is unchanged. `human_subject` filter is unchanged.)

- [ ] **Step 9: Update remaining test/conftest field references**

In `tests/conftest.py` (the outflow-building fixtures around lines 231–232 and 250–251):
`subject=request.param[2]` → `opposition=request.param[2]`, `forgive=request.param[3]` →
`rescind=request.param[3]`. Then sweep the rest of the suite:

Run: `grep -rn "Outflow(.*subject=\|Outflow(.*forgive=\|\.subject\b\|\.forgive\b" tests`
Update each hit that builds/reads an **outflow** kind (rename) — leave subject-string fixtures
and `encode_subject(...)` etc. untouched. Update `test_transaction.py` and `test_models.py`
outflow constructions/reads the same way.

- [ ] **Step 10: Create the column-rename migration**

Scaffold a migration (auto-sets revision id + `down_revision = '63d32cd7621a'`):

Run: `uv run gumptionchain db migrate -m "rename outflow kinds subject->opposition forgive->rescind"`

Then **replace** the generated `upgrade()`/`downgrade()` bodies (autogenerate emits drop+add for
renames — wrong) with explicit SQLite-safe batch renames:

```python
def upgrade() -> None:
    with op.batch_alter_table('outflow', schema=None) as batch_op:
        batch_op.alter_column('subject', new_column_name='opposition')
        batch_op.alter_column('forgive', new_column_name='rescind')


def downgrade() -> None:
    with op.batch_alter_table('outflow', schema=None) as batch_op:
        batch_op.alter_column('opposition', new_column_name='subject')
        batch_op.alter_column('rescind', new_column_name='forgive')
```

- [ ] **Step 11: Verify schema parity and run the full suite**

Run: `uv run gumptionchain db check`
Expected: no error (model metadata from `create_all` matches the migration head).

Run: `uv run pytest -q`
Expected: PASS (full suite). The renamed Outflow `data_csv` is byte-identical, so any
hash/merkle assertions still pass unchanged.

- [ ] **Step 12: Lint, format, type-check**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
Expected: all clean.

- [ ] **Step 13: Commit**

```bash
git add -A
git commit -m "refactor(rename): outflow kinds subject->opposition, forgive->rescind (fields)"
```

---

## Task 2: Rename `create_subject`→`create_opposition` and `create_forgive`→`create_rescind`

**Files:**
- Modify: `src/gumptionchain/chain.py` (method defs)
- Modify: `src/gumptionchain/api.py` (`lc.create_subject` / `lc.create_forgive` call sites)
- Modify: `src/gumptionchain/command.py` if it calls `lc.create_*` directly (it calls the client, not chain — verify)
- Modify: `tests/test_chain.py`, `tests/test_miller.py`, `tests/test_verification_audit.py`, and any test calling `create_subject`/`create_forgive`

- [ ] **Step 1: Update tests to call the new method names (red first)**

Run: `grep -rn "create_subject\|create_forgive" tests`
Rename each call: `create_subject(` → `create_opposition(`, `create_forgive(` → `create_rescind(`.
(Signatures are unchanged in PR 1: `create_rescind(wallet, amount, subject)`.)

- [ ] **Step 2: Run affected tests to confirm failure**

Run: `uv run pytest tests/test_chain.py -q`
Expected: FAIL — `AttributeError: 'Chain' object has no attribute 'create_opposition'`.

- [ ] **Step 3: Rename the method definitions in `chain.py`**

`def create_subject(` → `def create_opposition(`; `def create_forgive(` → `def create_rescind(`.
Bodies already updated in Task 1; only the `def` line changes.

- [ ] **Step 4: Update call sites in `api.py`**

In `SubjectTxnView`: `lc.create_subject(wallet, amount, subject)` → `lc.create_opposition(...)`.
In `ForgiveTxnView`: `lc.create_forgive(wallet, amount, subject)` → `lc.create_rescind(...)`.

- [ ] **Step 5: Confirm no stragglers**

Run: `grep -rn "create_subject\|create_forgive" src tests`
Expected: no matches.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(rename): Chain.create_subject->create_opposition, create_forgive->create_rescind"
```

---

## Task 3: Rename DAO/Chain query methods

`subject_balance`→`opposition_balance`, `subject_support`→`support_balance`,
`unforgiven_outflows`→`unrescinded_outflows`, `unforgiven_address_outflows`→`unrescinded_address_outflows`.
(Logic is unchanged in PR 1 — `support_balance` still sums all support; the unspent-only change is PR 2.)

**Files:**
- Modify: `src/gumptionchain/models.py` (`ChainDAO` method defs)
- Modify: `src/gumptionchain/chain.py` (`Chain` wrapper methods + `create_rescind`'s `unrescinded_address_outflows` call)
- Modify: `src/gumptionchain/api.py` (`lc.subject_balance` / `lc.subject_support` call sites)
- Modify: `tests/test_models.py`, `tests/test_chain.py`, and any test calling these

- [ ] **Step 1: Update tests to the new method names (red first)**

Run: `grep -rn "subject_balance\|subject_support\|unforgiven_outflows\|unforgiven_address_outflows" tests`
Rename: `subject_balance`→`opposition_balance`, `subject_support`→`support_balance`,
`unforgiven_outflows`→`unrescinded_outflows`, `unforgiven_address_outflows`→`unrescinded_address_outflows`.

- [ ] **Step 2: Run affected tests to confirm failure**

Run: `uv run pytest tests/test_models.py tests/test_chain.py -q`
Expected: FAIL — `AttributeError` on the renamed methods.

- [ ] **Step 3: Rename the definitions and internal call sites**

In `models.py` rename the four `ChainDAO` method defs. In `chain.py` rename the `Chain`
wrapper methods (`subject_balance`/`subject_support`) and update `create_rescind`'s body call
`self.unforgiven_address_outflows(...)` → `self.unrescinded_address_outflows(...)`.

- [ ] **Step 4: Update call sites in `api.py`**

`SubjectBalanceView`: `lc.subject_balance(subject)` → `lc.opposition_balance(subject)`.
`SubjectSupportView`: `lc.subject_support(subject)` → `lc.support_balance(subject)`.

- [ ] **Step 5: Confirm no stragglers**

Run: `grep -rn "subject_balance\|subject_support\|unforgiven_" src tests`
Expected: no matches.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(rename): ChainDAO subject_balance->opposition_balance, subject_support->support_balance, unforgiven_*->unrescinded_*"
```

---

## Task 4: Rename the API surface (views, routes, endpoint names, client methods)

**Files:**
- Modify: `src/gumptionchain/api.py`
- Modify: `src/gumptionchain/api_client.py`
- Modify: `tests/test_api.py`, `tests/test_network_audit.py`, `tests/test_browser.py`, and any test/template using `url_for(...)` on the renamed endpoints

- [ ] **Step 1: Update API tests to the new routes/client methods (red first)**

In `tests/test_api.py` (and others hitting these), rename request paths and client calls:
- `/transaction/subject` → `/transaction/opposition`; `/transaction/forgive` → `/transaction/rescind`
- `/subject/<…>/balance` → `/subject/<…>/opposition` (`/support` unchanged)
- `get_subject_transaction` → `get_opposition_transaction`; `get_forgive_transaction` → `get_rescind_transaction`
- `get_subject_balance` → `get_opposition_balance`; `get_subject_support` → `get_support_balance`

The `'subject'` JSON query-param key sent in request bodies is **unchanged** (it carries the
target string).

- [ ] **Step 2: Run API tests to confirm failure**

Run: `uv run pytest tests/test_api.py -q`
Expected: FAIL (404s on new routes / missing client methods).

- [ ] **Step 3: Rename views, routes, and endpoint names in `api.py`**

- `class SubjectTxnView` → `class OppositionTxnView`; its `route('/transaction/subject', …)`
  → `route('/transaction/opposition', …)`; `as_view('txn_subject_transactor')` →
  `as_view('txn_opposition_transactor')`.
- `class ForgiveTxnView` → `class RescindTxnView`; `route('/transaction/forgive', …)` →
  `route('/transaction/rescind', …)`; `as_view('txn_forgive_transactor')` →
  `as_view('txn_rescind_transactor')`.
- `class SubjectBalanceView` → `class OppositionBalanceView`;
  `route('/subject/<subject:subject>/balance', …)` →
  `route('/subject/<subject:subject>/opposition', …)`;
  `as_view('subject_balance_transactor')` → `as_view('opposition_balance_transactor')`.
- `SubjectSupportView` **keep** (route `/subject/<…>/support` unchanged; it already calls
  `lc.support_balance` from Task 3). Keep `SubjectTxnQueryModel` (shared subject-string query).

Keep the `<subject:subject>` converter usage verbatim (preserved noun).

- [ ] **Step 4: Rename client methods + paths in `api_client.py`**

- `get_subject_transaction` → `get_opposition_transaction`, path `/api/transaction/subject` → `/api/transaction/opposition`
- `get_forgive_transaction` → `get_rescind_transaction`, path `/api/transaction/forgive` → `/api/transaction/rescind`
- `get_subject_balance` → `get_opposition_balance`, path `/api/subject/{subject}/balance` → `/api/subject/{subject}/opposition`
- `get_subject_support` → `get_support_balance`, path `/api/subject/{subject}/support` (path unchanged)

Keep the `'subject': subject` entry in the request `data` dicts (target-string param).

- [ ] **Step 5: Update any `url_for` references to renamed endpoints**

Run: `grep -rn "txn_subject_transactor\|txn_forgive_transactor\|subject_balance_transactor" src tests`
Update each (templates and tests) to the new endpoint names. Expected after: no matches for the
old names.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(rename): API routes/views/client for opposition & rescind"
```

---

## Task 5: Rename the CLI verbs and command functions

**Files:**
- Modify: `src/gumptionchain/command.py`
- Modify: `tests/test_command.py`

- [ ] **Step 1: Update CLI tests to the new verbs (red first)**

In `tests/test_command.py`, rename invocations: `txn subject` → `txn opposition`,
`txn forgive` → `txn rescind`, `subject balance` → `subject opposition`. (`txn support` and
`subject support` are unchanged.) Update any references to the renamed client methods from
Task 4 if the tests assert on them.

- [ ] **Step 2: Run CLI tests to confirm failure**

Run: `uv run pytest tests/test_command.py -q`
Expected: FAIL (no such command `opposition`).

- [ ] **Step 3: Rename the commands + functions in `command.py`**

- `@txn_cli.command('subject')` → `@txn_cli.command('opposition')`; `def create_subject(` →
  `def create_opposition(`; inside it `client.get_subject_transaction(...)` →
  `client.get_opposition_transaction(...)`.
- `@txn_cli.command('forgive')` → `@txn_cli.command('rescind')`; `def create_forgive(` →
  `def create_rescind(`; inside it `client.get_forgive_transaction(...)` →
  `client.get_rescind_transaction(...)`.
- `@subject_cli.command('balance')` → `@subject_cli.command('opposition')`; `def subject_balance(` →
  `def opposition_balance(`; inside it `client.get_subject_balance(...)` →
  `client.get_opposition_balance(...)`.
- `@subject_cli.command('support')` (keep); inside `support_balance(...)`:
  `client.get_subject_support(...)` → `client.get_support_balance(...)`.

Keep the `subject_cli` group name and all `subject` argument names (target strings).

- [ ] **Step 4: Confirm no stragglers**

Run: `grep -rn "get_subject_transaction\|get_forgive_transaction\|get_subject_balance\|get_subject_support" src tests`
Expected: no matches.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(rename): CLI txn opposition/rescind and subject opposition"
```

---

## Task 6: Final verification sweep

**Files:** none expected (verification only; fix stragglers if any).

- [ ] **Step 1a: Grep for identifiers that must be fully gone (expect zero)**

These have no preserved-noun meaning — every hit is a straggler. `forgive` never refers to the
noun, so all `forgive` forms must vanish:

```bash
grep -rn "\.forgive\b\|forgive=\|create_subject\|create_forgive\|/transaction/subject\|/transaction/forgive\|/subject/<subject:subject>/balance\|subject_balance\|subject_support\|unforgiven\|get_subject_transaction\|get_forgive_transaction\|get_subject_balance\|get_subject_support\|ForgiveTxnView\|SubjectTxnView\|SubjectBalanceView\|txn_subject_transactor\|txn_forgive_transactor\|subject_balance_transactor" src tests
```
Expected: **no matches.** Rename any hit.

- [ ] **Step 1b: Advisory grep for `.subject` / `subject=` (manual review — both meanings exist)**

```bash
grep -rn "\.subject\b\|subject=" src tests
```
Expected hits are **only** the preserved target-string noun: `SubjectTxnQueryModel.subject`,
`model.subject`, client/CLI calls passing `subject=<string>`, and similar. There must be **no**
hit that is an `Outflow`/`OutflowDAO` kind field or kwarg (those are now `opposition=`). Review
each; rename only true outflow-kind hits.

- [ ] **Step 2: Confirm preserved nouns are intact (sanity)**

Run: `grep -rn "encode_subject\|decode_subject\|SubjectConverter\|human_subject\|<subject:subject>\|SUBJECT_1" src tests | head`
Expected: these still exist (they must NOT have been renamed).

- [ ] **Step 3: Full gate — tests, schema, lint, types**

Run:
```bash
uv run pytest -q
uv run gumptionchain db check
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Expected: all clean.

- [ ] **Step 4: Commit any straggler fixes (if Steps 1–3 required edits)**

```bash
git add -A
git commit -m "refactor(rename): clean up residual subject/forgive references"
```

---

## Definition of done (PR 1)

- `subject` outflow kind is `opposition`, `forgive` outflow kind is `rescind`, end-to-end (domain,
  DAO, migration, chain, API routes/views, client, CLI, template).
- `subject_support` accessor is now `support_balance` (logic unchanged).
- The target-string noun ("subject") is untouched everywhere it appears.
- Full suite + `db check` + ruff + ruff format + mypy all green.
- Block/merkle hashes unchanged (no consensus impact) — PR 1 is not a hard fork.
- PR 2 (support rescindability, `rescind_kind`, `--kind`, `regret`, `mudita`→½) builds on this.
