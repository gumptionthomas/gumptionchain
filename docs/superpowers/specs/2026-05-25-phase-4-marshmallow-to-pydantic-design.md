# Phase 4 — Marshmallow → Pydantic v2

**Status:** Draft for review
**Date:** 2026-05-25
**Scope:** Replace the Marshmallow-based validation and serialization layer with Pydantic v2. The dataclass domain layer (`Block`, `Transaction`, `Outflow`, `Inflow`, `Chain`) is unchanged; only the `Schema` subclasses get rewritten as `BaseModel` subclasses. After Phase 4, `marshmallow` is no longer a runtime dependency and Phase 3's Marshmallow-related mypy workarounds come out.

## Goal

Modernize the validation/serialization layer onto Pydantic v2, the de-facto Python standard for typed data validation. Removes a runtime dependency, eliminates the untyped-boundary leaks that forced file-level `# mypy: disable-error-code` directives in `schema.py` and `transaction.py`, and aligns the project with the broader Python ecosystem.

Concretely: after Phase 4, `[project.dependencies]` no longer contains `marshmallow`, `[[tool.mypy.overrides]]` for `marshmallow.*` is gone, and the existing test suite (177 passing) still passes through the same JSON round-trips.

**Test-message risk.** A small number of negative-path tests assert on Marshmallow-specific error message text (e.g., `tests/test_block.py:158` checks `match='Length must be between 1 and 100'`). The Pydantic→messages adapter (`pydantic_errors_to_messages` in PR-1) preserves the dict *shape* downstream consumers expect, but it does not normalize message *text* — Pydantic phrases length violations as `'List should have at most 100 items after validation, not 101'`. These assertions must be updated to the equivalent Pydantic wording in the PR that introduces the swap for their domain object (PR-3 for transaction tests, PR-4 for block tests). Tests that match on stable application-level constants (e.g., `'Address/public key mismatch'`, `'Invalid destinations'`, `'Missed target'`) are unaffected.

## Non-goals (deferred to Phase 5+)

- **No changes to the dataclass domain layer.** `Block`, `Transaction`, `Outflow`, `Inflow`, `Chain` keep their stdlib `@dataclass` definitions. They retain their staged-construction lifecycle (`Block` starts mostly-empty, fills in over `link` → `add_txn` → `seal` → `mill` → `solve`). Pydantic v2's strict-on-construction model would conflict with that flow without a deeper architectural change; the two-layer dataclass-domain + Pydantic-IO split is preserved deliberately.
- **No changes to API URLs, request shapes, or wire formats.** JSON output before and after must be byte-equivalent for the round-trip tests to pass.
- **No introduction of `pydantic.dataclasses.dataclass`.** Same staged-construction conflict.
- **No `requests` → `httpx` swap** (Phase 5).
- **No `pycryptodome` → `cryptography` swap** (Phase 5).
- **No SA `.query.X()` → `db.session.execute(...)` modernization** (Phase 6).
- **No Alembic** (Phase 7).

## Decisions taken during brainstorming

- **Scope: Path B (swap-in-place).** Considered Path A (replace dataclasses with `BaseModel`) and Path C (`pydantic.dataclasses.dataclass`); both conflict with the staged-construction lifecycle the domain types use. Path B is the minimum scope change that achieves the stated Phase 4 goal.
- **PR strategy: bottom-up by dependency.** 6 PRs in `schema → payload → transaction → block → api → cleanup` order. Each PR is self-contained — no long-lived dual implementations.
- **Pydantic version: `>=2.10`**. Pydantic v2.10 (2024-Q4) added the model-validator return-type contract and `Annotated[..., AfterValidator(...)]` ergonomics this design relies on.
- **`@post_load` removal.** Marshmallow's `@post_load` makes `Schema().load(d)` return a domain instance. Pydantic v2 doesn't have a clean equivalent (a `@model_validator(mode='after')` that returns a different type would be confusing). Callers do the conversion explicitly: `Transaction(**TransactionModel.model_validate(d).model_dump())`. 3-4 call sites total; the explicit conversion clarifies the boundary.
- **`SansNoneSchema` retirement.** The "drop None on dump" behavior moves to the call site via `model.model_dump(exclude_none=True)` where needed; the existing `asdict_sans_none(dc)` utility (for dataclass `.to_dict()`) is unchanged.
- **No long-lived parallel Marshmallow + Pydantic.** Each PR swaps its file's Schema(s) for Model(s) in one step; the file goes from "all Marshmallow" to "all Pydantic" in one commit. No transient "both work" gap.

## Changes — the PR train

Phase 4 ships as **six sequential PRs**, each squash-mergeable and individually revertible. Order is dictated by the dependency chain (`schema.py` defines custom types used by every later file).

### PR-1. Pydantic v2 + custom types in `schema.py`

**Files:**
- Modify: `pyproject.toml` (`[project.dependencies]`)
- Modify: `src/cancelchain/schema.py`

**Changes:**

In `pyproject.toml`, add to `[project.dependencies]`:
```toml
"pydantic>=2.10",
```
Keep `marshmallow>=3.19` for now — it's still imported by `payload.py`, `transaction.py`, `block.py`, `api.py` at this point. PR-6 removes it after the swap is complete.

In `schema.py`:
- Drop the file-level `# mypy: disable-error-code="no-untyped-call,no-any-return"` directive (no Marshmallow imports remain in this file after the swap — but only if PR-1 actually removes all of them; if some validator function still touches Marshmallow types, defer the directive removal to PR-6).
- Remove the Marshmallow imports (`from marshmallow import Schema, fields, post_dump, validate`).
- Remove the `SansNoneSchema(Schema)` class entirely. The "drop None on dump" semantic moves to call sites that need it via `model.model_dump(exclude_none=True)`.
- Replace the 5 custom field-type subclasses with `Annotated` type aliases:

  ```python
  from __future__ import annotations
  from dataclasses import asdict
  from typing import Annotated, Any

  from pydantic import AfterValidator

  from cancelchain.util import iso_2_dt
  from cancelchain.wallet import (
      ADDRESS_TAG,
      Wallet,
      b58decode,
      b64decode,
      b64encode,
  )

  # `asdict_sans_none` stays — used by domain dataclasses' to_dict methods.
  def asdict_sans_none(dc: Any) -> dict[str, Any]:
      return asdict(
          dc,
          dict_factory=lambda x: {k: v for (k, v) in x if v is not None},
      )

  # ... validate_address, validate_base64, validate_signature, etc. — unchanged ...

  def _check_address_format(s: str) -> str:
      if not validate_address_format(s):
          msg = f'Invalid address format: {s!r}'
          raise ValueError(msg)
      return s

  def _check_base64(s: str) -> str:
      if not validate_base64(s):
          msg = f'Invalid base64 value: {s!r}'
          raise ValueError(msg)
      return s

  def _check_mill_hash(s: str) -> str:
      if not validate_base64(s) or len(s) != 64:
          msg = f'Invalid mill hash: {s!r}'
          raise ValueError(msg)
      return s

  def _check_timestamp(s: str) -> str:
      if not validate_timestamp(s):
          msg = f'Invalid timestamp: {s!r}'
          raise ValueError(msg)
      return s

  def _check_public_key(s: str) -> str:
      if not validate_public_key(s):
          msg = f'Invalid public key: {s!r}'
          raise ValueError(msg)
      return s

  Address = Annotated[str, AfterValidator(_check_address_format)]
  Base64 = Annotated[str, AfterValidator(_check_base64)]
  MillHash = Annotated[str, AfterValidator(_check_mill_hash)]
  Timestamp = Annotated[str, AfterValidator(_check_timestamp)]
  PublicKey = Annotated[str, AfterValidator(_check_public_key)]
  ```

  Pydantic's `AfterValidator` callback receives the post-coercion value and either returns it unchanged or raises `ValueError`. Pydantic wraps the `ValueError` into a `ValidationError` for the caller.

- The base validator functions (`validate_address`, `validate_address_format`, `validate_base64`, `validate_public_key`, `validate_signature`, `validate_timestamp`) stay untouched — they're used by code outside the Marshmallow path (e.g., the transaction schema validation hook in PR-3).

**Acceptance:** `uv run mypy` exit 0; `uv run pytest` exit 0 (existing tests unchanged because no callers are using the new types yet). `pydantic` is in `uv.lock`. `marshmallow` is still in `uv.lock` (used by other files).

### PR-2. `payload.py`: `Outflow`/`Inflow` schemas

**Files:**
- Modify: `src/cancelchain/payload.py`

**Changes:**

**Add** `OutflowModel(BaseModel)` and `InflowModel(BaseModel)` alongside the existing `OutflowSchema(SansNoneSchema)` and `InflowSchema(SansNoneSchema)`. The Marshmallow versions stay in place for this PR because `TransactionSchema.outflows = fields.List(fields.Nested(OutflowSchema), ...)` in `transaction.py` requires a Marshmallow `Schema` subclass — `fields.Nested` cannot bridge to a Pydantic `BaseModel`. PR-3 deletes the Marshmallow versions (and the `Subject(fields.String)` local class) in the same commit it swaps `TransactionSchema` over.

Pattern (illustrative — the actual code follows existing field/validator constraints):

```python
from __future__ import annotations
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cancelchain.schema import Address, MillHash
# Subject is defined locally below as an `Annotated` alias (it stays in
# payload.py for the same reason it was a `fields.String` subclass here
# in the Marshmallow version — it's specific to payload validation).


class OutflowModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    amount: int = Field(ge=1)
    address: Address | None = None
    subject: Subject | None = None
    forgive: Subject | None = None
    support: Subject | None = None
    # ... whatever the existing OutflowSchema has ...


class InflowModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    outflow_txid: MillHash
    outflow_idx: int = Field(ge=0)
```

- `validate.Range(min=N)` → `Field(ge=N)`.
- `validate.Length(equal=N)` → `Field(min_length=N, max_length=N)`.
- `validate.Length(min=N, max=M)` → `Field(min_length=N, max_length=M)`.
- `validate.Equal(value)` → `Literal[value]`.
- Marshmallow's `required=True` becomes "no default" in Pydantic.
- Marshmallow's `required=False` becomes `Field(default=None)` or `... | None = None`.
- `extra='forbid'` matches Marshmallow's default of rejecting unknown fields.

Drop the `@post_load make_outflow` / `make_inflow` hooks. The `Outflow` / `Inflow` dataclasses stay; no caller in PR-2 needs to convert from the model yet (the conversion happens at higher layers in later PRs).

**Acceptance:** `mypy` + `ruff` clean; tests green. `OutflowModel` / `InflowModel` are exported. The Marshmallow `OutflowSchema` / `InflowSchema` / `Subject(fields.String)` are still present (deleted by PR-3).

### PR-3. `transaction.py`: txn schemas + call sites

**Files:**
- Modify: `src/cancelchain/transaction.py`

**Changes:**

Replace `TransactionSchema(SansNoneSchema)`, `RegularTransactionSchema(TransactionSchema)`, and `CoinbaseTransactionSchema(TransactionSchema)` with corresponding `BaseModel` subclasses:

```python
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cancelchain.payload import Inflow, InflowModel, Outflow, OutflowModel
from cancelchain.schema import Address, Base64, MillHash, PublicKey, Timestamp


class TransactionModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    timestamp: Timestamp
    txid: MillHash
    address: Address
    public_key: PublicKey
    signature: Base64 | None = None
    inflows: Annotated[list[InflowModel], Field(min_length=0, max_length=MAX_FLOWS)]
    outflows: Annotated[list[OutflowModel], Field(min_length=1, max_length=MAX_FLOWS)]
    version: Literal[VERSION_1]

    @model_validator(mode='after')
    def validate_pk_address(self) -> Self:
        if not validate_address(self.public_key, self.address):
            raise ValueError(ADDRESS_MISMATCH_MSG)
        return self


class RegularTransactionModel(TransactionModel):
    inflows: Annotated[list[InflowModel], Field(min_length=1, max_length=MAX_FLOWS)]


class CoinbaseTransactionModel(TransactionModel):
    inflows: Annotated[list[InflowModel], Field(min_length=0, max_length=0)]
    outflows: Annotated[list[OutflowModel], Field(min_length=1, max_length=4)]
```

Updates to call sites within `transaction.py`:

- `errors = CoinbaseTransactionSchema().validate(self.to_dict())` →
  ```python
  try:
      CoinbaseTransactionModel.model_validate(self.to_dict())
  except ValidationError as e:
      raise InvalidTransactionError(_pydantic_errors(e)) from e
  ```
  where `_pydantic_errors(e)` is a small helper that converts Pydantic's `e.errors()` list-of-dicts into the dict-shape that `InvalidTransactionError(message=...)` already expects (matching the Marshmallow behavior the downstream consumers like `api.py:make_error_response` rely on).

- `TransactionSchema().dumps(self.to_dict())` → `TransactionModel(**self.to_dict()).model_dump_json(exclude_none=True)`. The `exclude_none=True` replicates `SansNoneSchema`'s old `@post_dump`.

- `TransactionSchema().load(d)` → explicit dataclass construction that replaces the old `@post_load make_transaction` hook. **Nested reconstruction is required:** `TransactionModel.model_dump()` returns `inflows`/`outflows` as `list[dict]`, but the `Transaction` dataclass expects `list[Inflow]` / `list[Outflow]`. Marshmallow's `@post_load` cascade did the conversion implicitly; in Pydantic we do it explicitly:
  ```python
  data = TransactionModel.model_validate(d).model_dump()
  data['inflows'] = [Inflow(**i) for i in data['inflows']]
  data['outflows'] = [Outflow(**o) for o in data['outflows']]
  return Transaction(**data)
  ```
  Without this, attribute access (`outflow.amount`, `inflow.outflow_txid`) breaks at runtime because the list elements are plain dicts.

- `TransactionSchema().loads(j)` → `TransactionModel.model_validate_json(j)` then the same nested-reconstruction conversion.

- `from marshmallow import ...` import line is removed; `from pydantic import ...` replaces it.

- File-level `# mypy: disable-error-code="no-untyped-call,no-any-return"` directive: leave it for now. PR-6 verifies whether the directive can be removed entirely or narrowed.

**Acceptance:** all existing Transaction tests pass (including the regression tests added in P3 PR-7.5 for `Transaction.to_dao()`'s fail-fast paths and PendingTxnSet.add). `tests/test_schema.py`'s validators (validate_address, validate_signature, validate_public_key) still work — they're called by both the new Pydantic boundary and by Transaction.validate_signature.

### PR-4. `block.py`: Block schema + call sites

**Files:**
- Modify: `src/cancelchain/block.py`

**Changes:**

Replace `BlockSchema(SansNoneSchema)` with `BlockModel(BaseModel)`:

```python
class BlockModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    idx: int = Field(ge=0)
    timestamp: Timestamp
    block_hash: MillHash
    prev_hash: MillHash
    target: MillHash
    proof_of_work: int = Field(ge=0, le=2**64 - 1)
    merkle_root: MillHash
    txns: list[TransactionModel] = Field(min_length=1, max_length=MAX_TRANSACTIONS)
    version: Literal[VERSION_1]

    @model_validator(mode='after')
    def check_proof(self) -> Self:
        # Validates proof-of-work satisfies target (the existing @validates_schema body).
        if not validate_hash_diff(self.block_hash, self.target):
            raise ValueError(MISSED_TARGET_MSG)
        return self
```

The `txns` field's `list[TransactionModel]` inheritance — pick either `TransactionModel`, `RegularTransactionModel`, or a discriminated union. Current Marshmallow code uses `fields.Nested(TransactionSchema)` which is the base form; replicate with `TransactionModel` here. Subtype enforcement happens at `Block.add_txn(txn, is_coinbase=...)` runtime, not at schema-load time.

Call-site updates in `block.py`:
- `BlockSchema().validate(self.to_dict())` → `BlockModel.model_validate(self.to_dict())` in try/except.
- `BlockSchema().dumps(self.to_dict())` → `BlockModel(**self.to_dict()).model_dump_json(exclude_none=True)`.
- `BlockSchema().load(d)` → explicit reconstruction. `BlockModel.model_dump()` returns `txns` as `list[dict]`, but `Block` expects `list[Transaction]`. Reconstruct nested `Transaction` instances (which in turn reconstruct their nested `Inflow`/`Outflow` — share the helper from PR-3) before passing to `Block(**data)`:
  ```python
  data = BlockModel.model_validate(d).model_dump()
  data['txns'] = [_txn_from_dump(t) for t in data['txns']]
  return Block(**data)
  ```
  where `_txn_from_dump(t)` rebuilds the `Inflow`/`Outflow` lists and constructs a `Transaction`. Without this, downstream `block.txns[0].sign()` / `txn.outflows[0].amount` access breaks at runtime.
- `BlockSchema().loads(j)` → `BlockModel.model_validate_json(j)` then same nested reconstruction.

Drop `from marshmallow import ValidationError`. Catch sites use `pydantic.ValidationError`.

**Acceptance:** all Block tests pass (`tests/test_block.py`'s 13 tests including the P3 PR-7.5 `test_to_dao_partial_block_raises` regression). Merkle root validation still works.

### PR-5. `api.py`: query schemas

**Files:**
- Modify: `src/cancelchain/api.py`

**Changes:**

Three query schemas (`TransferTxnQuerySchema`, `SubjectTxnQuerySchema`, `PendingTxnQuerySchema`) become Pydantic models. These differ from the domain schemas above because they:
- Read from `flask.request.args` (a `MultiDict`)
- Have no `@post_load` (callers just use the validated dict, never a domain object)
- One of them (`PendingTxnQuerySchema.earliest`) uses `fields.Function` for symmetric ciso-datetime parse/format — see translation note below.

Pattern:

```python
class TransferTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    address: Address
    limit: int | None = Field(default=None, ge=1)
    # ... existing fields ...


# at the call site (was: `args = TransferTxnQuerySchema().load(request.args)`):
try:
    args = TransferTxnQueryModel.model_validate(
        request.args.to_dict(flat=True)
    ).model_dump(exclude_none=True)
except ValidationError as e:
    abort(make_error_response(e))
```

The `request.args.to_dict(flat=True)` flattens the `MultiDict` to a plain dict (each key's first value). None of the existing query schemas have list-valued fields (verified by grep), so flattening is safe.

For `PendingTxnQueryModel.earliest` (originally `fields.Function(serialize=lambda o: dt_2_ciso(o.earliest), deserialize=ciso_2_dt)`):

```python
from datetime import datetime
from pydantic import BeforeValidator, PlainSerializer

CisoTimestamp = Annotated[
    datetime,
    BeforeValidator(lambda v: ciso_2_dt(v) if isinstance(v, str) else v),
    PlainSerializer(lambda dt: dt_2_ciso(dt), return_type=str),
]


class PendingTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    earliest: CisoTimestamp | None = None
```

The `BeforeValidator` runs `ciso_2_dt` on input strings; `PlainSerializer` runs `dt_2_ciso` on output. Same parse/format symmetry as `fields.Function` provided.

`make_error_response(e)` is the existing api.py helper. It currently expects a `marshmallow.ValidationError` with `.messages`. Update it (or add a sibling) to format Pydantic `ValidationError.errors()` output into the same response shape.

**Acceptance:** API endpoint tests pass; query-parameter validation rejects bad inputs with the same status codes (400) as before.

### PR-6. Remove `marshmallow` from runtime dependencies

**Files:**
- Modify: `pyproject.toml` (`[project.dependencies]`, `[[tool.mypy.overrides]]`)
- Modify: `src/cancelchain/schema.py` (if not already done in PR-1)
- Modify: `src/cancelchain/transaction.py` (mypy directive)
- Possibly modify: `src/cancelchain/models.py` (verify mypy directive scope)

**Changes:**

- Remove `"marshmallow>=3.19",` from `[project.dependencies]`.
- Remove the `[[tool.mypy.overrides]]` block for `module = ["marshmallow", "marshmallow.*"]`.
- Drop any stale `from marshmallow import ...` lines (should be none after PRs 1–5 but verify).
- Drop the file-level `# mypy: disable-error-code="no-untyped-call,no-any-return"` directives in `schema.py` and `transaction.py` — verify each removal individually with `uv run mypy`. If `transaction.py`'s directive is still needed for non-Marshmallow Any leaks (e.g., a pycryptodome-typed call), narrow it (e.g., `"no-any-return"` only) rather than dropping entirely.
- Verify `models.py`'s directive — currently `"no-untyped-call,no-any-return,name-defined,misc"`. The Marshmallow portions don't apply there (it's the SA `db.Model` portion). Leave that file as-is; Phase 6 revisits when query modernization happens.
- Run `uv lock --upgrade-package marshmallow` to confirm Marshmallow is fully removed from the lockfile (the resolver should drop it once no `[project.dependencies]` constraint pulls it in).

**Acceptance:** `grep -r marshmallow src/` returns nothing. `marshmallow` no longer in `uv.lock`. `uv run mypy` exit 0. `uv run ruff check src tests` exit 0. `uv run pytest` exit 0. `uv run cancelchain --help` works.

## Non-source changes summary

| File | Touched by |
|---|---|
| `pyproject.toml` | PR-1 (add pydantic), PR-6 (drop marshmallow + overrides) |
| `uv.lock` | PR-1, PR-6 |

## Source files touched

| File | Touched by |
|---|---|
| `src/cancelchain/schema.py` | PR-1 (custom types), PR-6 (directive cleanup verification) |
| `src/cancelchain/payload.py` | PR-2 |
| `src/cancelchain/transaction.py` | PR-3, PR-6 (directive cleanup) |
| `src/cancelchain/block.py` | PR-4 |
| `src/cancelchain/api.py` | PR-5 |
| `src/cancelchain/models.py` | none — Phase 6 |

## Translation reference (Marshmallow → Pydantic v2)

| Marshmallow | Pydantic v2 |
|---|---|
| `class X(Schema):` | `class X(BaseModel):` |
| `class X(SansNoneSchema):` | `class X(BaseModel):` + `.model_dump(exclude_none=True)` at use site |
| `fields.String(required=True)` | `field: str` |
| `fields.String(required=False)` | `field: str \| None = None` |
| `fields.Integer(validate=validate.Range(min=N))` | `field: int = Field(ge=N)` |
| `fields.String(validate=validate.Length(equal=N))` | `field: str = Field(min_length=N, max_length=N)` |
| `fields.String(validate=validate.Equal(value))` | `field: Literal[value]` |
| `fields.List(fields.Nested(X), validate=validate.Length(min=N, max=M))` | `field: list[X] = Field(min_length=N, max_length=M)` |
| `fields.Nested(X)` | `field: X` |
| Custom field class (`MillHash(Base64)`) | `Annotated[str, AfterValidator(...)]` type alias |
| `fields.Function(serialize=f_dump, deserialize=f_load)` | `Annotated[X, BeforeValidator(f_load), PlainSerializer(f_dump)]` |
| `@validates_schema def f(self, data, **kw):` | `@model_validator(mode='after') def f(self) -> Self:` |
| `@post_load def make_x(self, data, **kw):` | **Removed** — caller does `X(**Model.model_validate(data).model_dump())` |
| `@post_dump def remove_none_values(self, data, **kw):` | **Removed** — caller does `model.model_dump(exclude_none=True)` |
| `Schema().load(d)` | `Model.model_validate(d)` |
| `Schema().loads(j)` | `Model.model_validate_json(j)` |
| `Schema().dumps(obj)` | `Model(**obj).model_dump_json(exclude_none=True)` |
| `Schema().validate(d)` | `try: Model.model_validate(d) except ValidationError: ...` |
| `marshmallow.ValidationError` (caught) | `pydantic.ValidationError` (caught) |
| `e.messages` (Marshmallow) | `e.errors()` (Pydantic) — different shape; adapter helper recommended |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Pydantic vs Marshmallow JSON output drift (field order, datetime format, integer-as-string). | Each PR runs the full test suite, which round-trips JSON through `from_json` / `to_json`. The `Timestamp` and `MillHash` custom types preserve string formats because they're declared as `str`-based. Any drift surfaces immediately in `test_from`, `test_db`, `test_pending_txns` etc. |
| `e.messages` (Marshmallow) → `e.errors()` (Pydantic) shape difference cascades to `api.py:make_error_response` and `InvalidBlockError({...: e.messages})` wrappers. | Add a small `_pydantic_errors_to_messages(e)` adapter that converts Pydantic's list-of-dicts to Marshmallow's nested-dict shape. Apply at every catch site in PR-3 / PR-4 / PR-5. Existing API consumers don't see a change. |
| `@post_load` removal changes the call surface from `Schema().load(d) → Transaction` to `Transaction(**Model.model_validate(d).model_dump())`. | The 3-4 call sites are all in the same PR as the schema swap (PR-3 for txn, PR-4 for block). Pre-existing tests exercise the call sites and would fail if conversion is wrong. |
| `request.args.to_dict(flat=True)` discards repeated query parameters. | Verified by grep that no query schema currently uses list-valued fields. If a future query schema adds one, switch its specific call site to `request.args.to_dict(flat=False)` and accept the list value type. |
| `extra='forbid'` rejects unknown fields (matching Marshmallow's default). If a peer sends an extra field, it would now fail. | This is the *current* Marshmallow behavior; PR-1's swap preserves it. If we discover peer messages sneaking in extras, switch specific Models to `extra='ignore'` with a clear comment. |
| Pydantic strict-vs-coercive parsing differs from Marshmallow. E.g., Marshmallow accepts `"10"` for an int field; Pydantic v2 with `strict=False` (default) also coerces. | Use Pydantic's default mode (lax/coercive) — `model_config = ConfigDict(strict=False)` is implicit. If a specific field needs strict typing (e.g., reject strings in int fields), use `Field(strict=True)` per-field. |
| Removing the `marshmallow.*` mypy override might re-surface a non-Marshmallow Any leak. | PR-6 verifies removal one directive at a time and runs full mypy. If a directive is still needed for a non-Marshmallow reason, narrow it rather than dropping entirely. |
| `Annotated[str, AfterValidator(...)]` type aliases may have subtle type-checker behavior under mypy strict (e.g., aliases not recognized as proper types in all contexts). | Phase 3 verified mypy strict on the existing custom-type usage. The new aliases are simpler (no field subclassing) so this is a lower risk, but PR-1 verifies under strict before merging. |
| Pydantic v2 ecosystem moves quickly (subtle behavior changes between minor versions). | Pin `>=2.10` with no upper bound. Dependabot's Monday cadence will surface major-version bumps; we revisit if v3 ships. |

## Acceptance criteria for Phase 4 as a whole

- [ ] All six PRs squash-merged to `main`.
- [ ] `grep -rn "marshmallow" src/` returns no matches.
- [ ] `grep marshmallow uv.lock` returns no matches.
- [ ] `grep marshmallow pyproject.toml` returns no matches in `[project.dependencies]` or `[[tool.mypy.overrides]]`.
- [ ] `uv run pytest` passes (177/178 — same as post-Phase-3).
- [ ] `uv run mypy` exits 0 under `[tool.mypy] strict = true` (hard CI gate).
- [ ] `uv run ruff check src tests` exits 0 (hard CI gate).
- [ ] `uv run cancelchain --help` prints the full command tree.
- [ ] `docker build .` succeeds.
- [ ] JSON round-trip tests (`test_from`, `test_db`, `test_pending_txns`) pass — wire format byte-equivalent before/after.

## Open decisions (resolve at PR time)

- PR-1: where exactly to put the `Annotated` type aliases (in `schema.py`, or a new `schema_types.py`?). Default: `schema.py`.
- PR-3 / PR-4 / PR-5: format of the Pydantic `ValidationError` → Marshmallow-shaped error adapter. Default: build a small `_pydantic_errors_to_messages(e: ValidationError) -> dict[str, Any]` helper in `schema.py` and import at catch sites.
- PR-4: whether `BlockModel.txns` uses `list[TransactionModel]` (base, matches current Marshmallow `fields.Nested(TransactionSchema)`) or a discriminated union of `RegularTransactionModel` + `CoinbaseTransactionModel`. Default: base model — matches existing behavior, no semantic change.
- PR-6: whether the file-level mypy directive in `transaction.py` can be removed entirely, or must be narrowed to e.g. `"no-any-return"` only. Decided after running `uv run mypy` with the directive removed.

## What comes next (Phase 5+)

- **Phase 5 — Supply-chain swaps.** `requests` → `httpx` and `pycryptodome` → `cryptography`. The `cryptography` swap removes the broad `[[tool.mypy.overrides]] module = ["Crypto", "Crypto.*"]` directive added in Phase 3.
- **Phase 6 — SA query-style modernization.** `.query.filter_by(...)` → `db.session.execute(db.select(...))` across `models.py` and call sites. Removes the SA 2.0 legacy-API deprecation warning suppression and the `models.py` file-level mypy directive. Pairs with tightening `Chain.to_dao() -> ChainDAO | None` (deferred from Phase 3 PR-7.5).
- **Phase 7 — Alembic.** Introduce migration framework.
- **Future — Observability.** OpenTelemetry, Sentry, structured logging.

Each subsequent phase gets its own design doc and implementation plan.
