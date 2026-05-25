# Phase 4 — Marshmallow → Pydantic v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the six-PR train laid out in `docs/superpowers/specs/2026-05-25-phase-4-marshmallow-to-pydantic-design.md`. After this plan completes, `marshmallow` is no longer in `[project.dependencies]` or `uv.lock`, the corresponding `[[tool.mypy.overrides]]` block is gone, and the file-level `# mypy: disable-error-code="no-untyped-call,no-any-return"` directives that Phase 3 added in `schema.py` and `transaction.py` have either been removed or narrowed.

**Architecture:** Path B (swap-in-place). Each Marshmallow `Schema` becomes a Pydantic v2 `BaseModel` used for validation + I/O. The domain dataclasses (`Block`, `Transaction`, `Outflow`, `Inflow`, `Chain`) keep their stdlib `@dataclass` definitions and their staged-construction lifecycle. The six PRs proceed in dependency order: `schema → payload → transaction → block → api → cleanup`, each self-contained with no long-lived dual implementations.

**Tech Stack:** Pydantic v2.10+, with `Annotated[str, AfterValidator(...)]` for custom field types, `@model_validator(mode='after')` for cross-field validation, and `model.model_dump(exclude_none=True)` for None-stripping serialization.

---

## Prerequisites

- Working directory: `/home/gumptionthomas/Development/cancelchain`. Use absolute paths or `cd` once.
- `uv --version` 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- Phase 3 fully merged. `git log --oneline -5` should show `525ccf5 fix(tests): harden FLASK_SECRET_KEY against pyjwt insecure-key warning (#49)` at or near the top.
- The branch `docs/phase-4-design` exists locally with commit `a644741` (the design spec). This plan adds the second commit on that branch and ships both as the docs PR.
- CI hard-gates `ruff check` and `mypy` (as of Phase 3 / PR-8). Every PR must keep both clean.
- Test baseline: **177 passed, 1 skipped**. Phase 4 should preserve that count (no new tests required, no regressions).
- Each impl PR ends with `wor` (Copilot review wait + reply) and `mwg` (merge when green); the controller (orchestrator) handles those, not the implementer subagent.
- Never push directly to `main`. Every change goes through a branch + PR.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-25-phase-4-marshmallow-to-pydantic.md` (this file) |
| 2 | PR-1 schema.py | `pyproject.toml`, `src/cancelchain/schema.py` |
| 3 | PR-2 payload.py | `src/cancelchain/payload.py` |
| 4 | PR-3 transaction.py | `src/cancelchain/transaction.py` |
| 5 | PR-4 block.py | `src/cancelchain/block.py` |
| 6 | PR-5 api.py | `src/cancelchain/api.py` |
| 7 | PR-6 cleanup | `pyproject.toml`, `src/cancelchain/schema.py`, `src/cancelchain/transaction.py`, `src/cancelchain/payload.py` (verify directive removal) |
| 8 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** Modify: nothing. The design spec is already committed on `docs/phase-4-design` as `a644741`. This task adds the implementation plan and ships them together.

- [ ] **Step 1: Confirm branch state**

Run:
```bash
git rev-parse --abbrev-ref HEAD
git log --oneline main..HEAD
```
Expected: branch is `docs/phase-4-design`; one commit above main: `a644741 docs(phase-4): add Phase 4 Marshmallow → Pydantic v2 design spec`.

- [ ] **Step 2: Verify the plan file is present**

Run:
```bash
ls -la docs/superpowers/plans/2026-05-25-phase-4-marshmallow-to-pydantic.md
git status docs/superpowers/plans/
```
Expected: file exists, untracked.

- [ ] **Step 3: Stage and commit**

Run:
```bash
git add docs/superpowers/plans/2026-05-25-phase-4-marshmallow-to-pydantic.md
git commit -m "$(cat <<'EOF'
docs(phase-4): add Phase 4 Marshmallow → Pydantic v2 implementation plan

Spells out the 6 sequential impl PRs (schema, payload, transaction,
block, api, cleanup) with exact files, commands, and the wor/mwg
cycle between each PR.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

Run:
```bash
git push -u origin docs/phase-4-design
```

- [ ] **Step 5: Open the docs PR**

Run:
```bash
gh pr create --base main --head docs/phase-4-design --title "docs(phase-4): add Phase 4 design + implementation plan" --body "$(cat <<'EOF'
## Summary
- Adds the Phase 4 design spec (\`docs/superpowers/specs/2026-05-25-phase-4-marshmallow-to-pydantic-design.md\`).
- Adds the Phase 4 implementation plan (\`docs/superpowers/plans/2026-05-25-phase-4-marshmallow-to-pydantic.md\`).
- No code changes.

Phase 4 ships as six small PRs in sequence: pydantic + schema.py custom types → payload → transaction → block → api → cleanup (remove marshmallow). Path B scope (Schemas → BaseModels; dataclasses preserved).

## Test plan
- [ ] Spec self-review passes (already done in the brainstorming session).
- [ ] Plan self-review passes (already done in the planning session).
- [ ] Reviewer confirms the PR list matches the spec's "Changes" section.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Stop — controller handles wor + mwg + sync**

---

## Task 2: PR-1 — Pydantic v2 + custom types in `schema.py`

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/cancelchain/schema.py`

This PR introduces Pydantic as a runtime dependency and converts the 5 Marshmallow custom field subclasses (`Address`, `Base64`, `MillHash`, `Timestamp`, `PublicKey`) into `Annotated[str, AfterValidator(...)]` type aliases. The `SansNoneSchema` base class is retired. Marshmallow stays in `[project.dependencies]` for now — PR-6 removes it after the swap is complete.

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/pydantic-schema-types
```
Expected: new branch from latest main (head should be the docs PR's squash commit).

- [ ] **Step 2: Add `pydantic>=2.10` to runtime dependencies**

Edit `pyproject.toml`. In `[project] dependencies`, insert `"pydantic>=2.10",` in alphabetical position — between `"pycryptodome>=3.20"` and `"pyjwt>=2.9"` (pycryptodome < pydantic < pyjwt).

- [ ] **Step 3: Lock and install**

```bash
uv lock --upgrade-package pydantic
uv sync --group dev
uv run python -c "from importlib.metadata import version; print('pydantic', version('pydantic'))"
```
Expected: `pydantic 2.10.x` or newer.

- [ ] **Step 4: Rewrite `schema.py`**

Replace the entire contents of `src/cancelchain/schema.py` with:

```python
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from pydantic import AfterValidator, ValidationError

from cancelchain.exceptions import InvalidKeyError
from cancelchain.util import iso_2_dt
from cancelchain.wallet import (
    ADDRESS_TAG,
    Wallet,
    b58decode,
    b64decode,
    b64encode,
)


def asdict_sans_none(dc: Any) -> dict[str, Any]:
    return asdict(
        dc, dict_factory=lambda x: {k: v for (k, v) in x if v is not None}
    )


def validate_address(public_key_b64: str | None, address: str | None) -> bool:
    try:
        wallet = Wallet(b64ks=public_key_b64)
    except InvalidKeyError:
        return False
    return bool((wallet is not None) and address == wallet.address)


def validate_address_format(address: str) -> bool:
    try:
        if (
            address.startswith(ADDRESS_TAG)
            and address.endswith(ADDRESS_TAG)
            and len(
                b58decode(
                    address.removeprefix(ADDRESS_TAG).removesuffix(ADDRESS_TAG)
                )
            )
            == 32
        ):
            return True
    except Exception:
        pass
    return False


def validate_base64(s: str) -> bool:
    try:
        return bool(b64encode(b64decode(s)) == s)
    except Exception:
        pass
    return False


def validate_public_key(public_key_b64: str) -> bool:
    try:
        wallet = Wallet(b64ks=public_key_b64)
    except InvalidKeyError:
        return False
    return wallet is not None and wallet.private_key is None


def validate_signature(
    public_key_b64: str | None,
    signing_data: bytes,
    signature: str | None,
) -> bool:
    try:
        wallet = Wallet(b64ks=public_key_b64)
    except InvalidKeyError:
        return False
    if wallet is not None:
        return bool(wallet.validate_signature(signing_data, signature))
    return False


def validate_timestamp(s: str) -> bool:
    try:
        _ = iso_2_dt(s)
        return True
    except Exception:
        pass
    return False


# --- Pydantic v2 custom type aliases ---
# AfterValidator runs after Pydantic's built-in coercion; the callback
# either returns the value (possibly transformed) or raises ValueError,
# which Pydantic wraps into a ValidationError for the caller.


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


def pydantic_errors_to_messages(e: ValidationError) -> dict[str, Any]:
    """Convert Pydantic ValidationError to Marshmallow-shaped messages.

    Marshmallow's ValidationError.messages is a nested dict keyed by
    field name; this matches what api.py's make_error_response and the
    InvalidBlockError({...: e.messages}) re-raise wrappers expect.
    """
    result: dict[str, Any] = {}
    for err in e.errors():
        loc = err.get('loc', ())
        msg = err.get('msg', 'invalid')
        if loc:
            key = '.'.join(str(part) for part in loc)
        else:
            key = '_schema'
        result.setdefault(key, []).append(msg)
    return result
```

Notes on the rewrite:
- `SansNoneSchema` class removed (lines 121–126 in original).
- 5 Marshmallow field subclasses removed (lines 91–118 in original).
- Marshmallow import removed; `pydantic` import added.
- All validator functions kept verbatim (they're used by both the new `AfterValidator` callbacks AND by `transaction.py:validate_signature`-style direct calls in PR-3).
- New `pydantic_errors_to_messages` helper for the Pydantic→Marshmallow-shaped error adapter (used in PRs 3, 4, 5).
- File-level `# mypy: disable-error-code` directive removed (no Marshmallow imports remain).

- [ ] **Step 5: Verify mypy + ruff still clean**

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
```
All three must exit 0.

If `mypy` reports new errors in `schema.py`, they're likely from removing the file-level directive. The most common cause is `validate_signature` calling `wallet.validate_signature(signing_data, signature)` where the wallet path returns `Any` (pycryptodome). If that surfaces, restore the directive narrowed to `"no-any-return"` only:
```python
# mypy: disable-error-code="no-any-return"
```

- [ ] **Step 6: Test suite**

```bash
uv run pytest
```
Expected: 177 passed, 1 skipped. The schema.py changes alone shouldn't break tests because no caller is using the new Pydantic types yet — but the removed `SansNoneSchema` class WOULD break callers if any imported it. Verify by grep:

```bash
grep -rn "SansNoneSchema" src/cancelchain/ tests/
```
Expected output: the only references are in `payload.py`, `transaction.py`, `block.py`'s import statements. Those will fail tests on this PR. So PR-1 MUST also remove those imports OR PR-1 must keep `SansNoneSchema` as a stub until PR-4.

**Decision: keep `SansNoneSchema` as a no-op `Schema` alias in this PR**, removed in PR-2/3/4 as each file gets converted. Add at the bottom of `schema.py`:

```python
# Temporary alias for the duration of the Phase 4 migration. Removed
# in PR-2/3/4 as each downstream file converts to BaseModel.
from marshmallow import Schema as _MarshmallowSchema


class SansNoneSchema(_MarshmallowSchema):
    """Legacy Marshmallow base — kept only while PR-2/3/4 land."""
```

This preserves the existing post_dump None-stripping behavior for any file that still uses it during the transition. PR-2/3/4 remove their `SansNoneSchema` import as each one converts.

- [ ] **Step 7: Re-verify tests**

```bash
uv run pytest
```
Expected: 177 passed, 1 skipped.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/cancelchain/schema.py
git commit -m "$(cat <<'EOF'
feat(deps): pydantic v2 + custom types as Annotated aliases

Adds pydantic>=2.10 to runtime dependencies. Rewrites schema.py:

- 5 custom Marshmallow field subclasses (Address, Base64, MillHash,
  Timestamp, PublicKey) become Annotated[str, AfterValidator(...)]
  type aliases. The existing validator functions (validate_address,
  validate_base64, etc.) become the AfterValidator callbacks via small
  _check_* wrappers that raise ValueError on failure (Pydantic re-wraps).
- Adds pydantic_errors_to_messages helper that converts Pydantic's
  list-of-dict ValidationError.errors() to the nested-dict shape
  Marshmallow's e.messages exposes. Used by PRs 3/4/5 at the
  domain-layer catch sites so the downstream api.make_error_response
  and InvalidBlockError({...: e.messages}) wrappers don't see a shape
  change.
- SansNoneSchema kept as a no-op Marshmallow alias only for the
  duration of PR-2/3/4 (each removes its own import).

File-level # mypy: disable-error-code directive removed (no Marshmallow
imports remain in schema.py).

Phase 4 / PR 1 of 6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 9: Push and open PR**

```bash
git push -u origin feat/pydantic-schema-types
gh pr create --base main --title "feat(deps): pydantic v2 + custom types as Annotated aliases" --body "$(cat <<'EOF'
## Summary
- Adds \`pydantic>=2.10\` to runtime deps.
- Converts schema.py's 5 custom field subclasses (Address, Base64, MillHash, Timestamp, PublicKey) to \`Annotated[str, AfterValidator(...)]\` aliases.
- Adds \`pydantic_errors_to_messages\` adapter helper for downstream PRs.
- Drops the file-level mypy disable directive from schema.py.
- Keeps \`SansNoneSchema\` as a transitional alias (removed by PR-2/3/4 as each downstream file converts).

Phase 4 / PR 1 of 6. Spec: \`docs/superpowers/specs/2026-05-25-phase-4-marshmallow-to-pydantic-design.md\`.

## Test plan
- [x] \`uv run mypy\` exits 0.
- [x] \`uv run pytest\` passes (177/178).
- [x] \`uv run ruff check\` + \`format --check\` pass.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 10: Stop — controller handles wor + mwg + sync**

---

## Task 3: PR-2 — `payload.py` schemas (additive)

**Files:**
- Modify: `src/cancelchain/payload.py`

**This PR adds Pydantic models alongside the existing Marshmallow schemas — it does NOT delete the Marshmallow versions.** The Marshmallow `OutflowSchema`, `InflowSchema`, and `Subject(fields.String)` classes stay in place because `TransactionSchema.outflows = fields.List(fields.Nested(OutflowSchema), ...)` in `transaction.py` still needs them — `fields.Nested` requires a Marshmallow `Schema` subclass and cannot bridge to a Pydantic `BaseModel`. PR-3 swaps `TransactionSchema` to `TransactionModel` AND deletes the Marshmallow `OutflowSchema` / `InflowSchema` / `Subject` in the same commit.

To avoid a name collision with the existing Marshmallow `Subject(fields.String)`, the new Pydantic subject alias is named **`SubjectType`** in this PR. PR-3 deletes the Marshmallow `Subject` class and renames `SubjectType` → `Subject` in the same commit.

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/pydantic-payload
```

- [ ] **Step 2: Add new imports at the top of `payload.py`**

The existing imports look like:
```python
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from typing import Any

from marshmallow import (
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)

from cancelchain.schema import Address, MillHash, SansNoneSchema
```

Add `Annotated` and `Self` to the typing import, and add the Pydantic block alongside (do NOT remove the Marshmallow imports):

```python
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from typing import Annotated, Any, Self

from marshmallow import (
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from cancelchain.schema import Address, MillHash, SansNoneSchema
```

- [ ] **Step 3: Append the Pydantic models at the end of `payload.py`**

After the existing `Inflow` dataclass at the bottom of the file, add:

```python
# --- Pydantic v2 models (used by PR-3 onwards). The Marshmallow
# Schemas above stay in place until PR-3 swaps transaction.py.


def _check_subject(s: str) -> str:
    if not validate_subject(s):
        msg = f'Invalid subject: {s!r}'
        raise ValueError(msg)
    return s


SubjectType = Annotated[str, AfterValidator(_check_subject)]


class OutflowModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    amount: int = Field(ge=1)
    address: Address | None = None
    subject: SubjectType | None = None
    forgive: SubjectType | None = None
    support: SubjectType | None = None

    @model_validator(mode='after')
    def validate_destinations(self) -> Self:
        options = [
            v
            for v in (self.subject, self.forgive, self.support)
            if v is not None
        ]
        if not (
            (self.address and not options)
            or (options and len(options) == 1 and not self.address)
        ):
            raise ValueError(INVALID_DESTINATION_MSG)
        return self


class InflowModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    outflow_txid: MillHash
    outflow_idx: int = Field(ge=0)
```

Notes:
- Marshmallow `OutflowSchema`, `InflowSchema`, `Subject(fields.String)`, `SansNoneSchema` import are all **kept** — PR-3 deletes them.
- `SubjectType` is named with the `Type` suffix to avoid colliding with the existing Marshmallow `Subject(fields.String)` class. PR-3 renames it to `Subject` once the Marshmallow class is gone.
- The file-level `# mypy: disable-error-code` directive **stays** in this PR (Marshmallow imports still present). PR-3 removes it.

- [ ] **Step 4: Verify and test**

```bash
uv run pytest
```
Expected: 177 passed, 1 skipped (no behavior change — the new Models exist but aren't used by anything yet).

- [ ] **Step 5: Commit**

```bash
git add src/cancelchain/payload.py
git commit -m "$(cat <<'EOF'
feat(deps): add Pydantic OutflowModel/InflowModel alongside Marshmallow

Adds Pydantic v2 OutflowModel and InflowModel (with SubjectType
Annotated alias) alongside the existing Marshmallow OutflowSchema /
InflowSchema. Both coexist; PR-3 (transaction.py) swaps over and PR-3
also removes the Marshmallow versions.

This dual-coexistence is necessary because Marshmallow's fields.Nested
can't bridge to a Pydantic BaseModel — TransactionSchema.outflows uses
fields.Nested(OutflowSchema), so OutflowSchema must remain a
Marshmallow Schema until TransactionSchema itself is swapped.

Phase 4 / PR 2 of 6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Push and open PR**

```bash
git push -u origin feat/pydantic-payload
gh pr create --base main --title "feat(deps): add Pydantic OutflowModel/InflowModel alongside Marshmallow" --body "$(cat <<'EOF'
## Summary
- Adds Pydantic v2 \`OutflowModel\`, \`InflowModel\`, and \`SubjectType\` alongside the existing Marshmallow Schemas.
- Marshmallow \`OutflowSchema\` / \`InflowSchema\` / \`Subject\` stay in place — \`fields.Nested\` in \`TransactionSchema\` still references them. PR-3 swaps the consumers and removes the Marshmallow versions.

Phase 4 / PR 2 of 6.

## Test plan
- [x] \`uv run pytest\` passes (177/178; no consumer of the new Models yet).
- [x] \`uv run mypy\` + ruff clean.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7: Stop — controller handles wor + mwg + sync**

---

## Task 4: PR-3 — `transaction.py` schemas + call sites

**Files:**
- Modify: `src/cancelchain/transaction.py`
- Modify: `src/cancelchain/payload.py` (remove Marshmallow Schemas and rename `SubjectType` → `Subject`)

This PR replaces the three transaction-related Marshmallow Schemas with Pydantic BaseModels and rewrites the four call sites (`validate`, `validate_coinbase`, `to_json`, `from_dict`, `from_json`). After this PR, the Marshmallow `OutflowSchema`/`InflowSchema`/`Subject` (added by PR-2) are gone.

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/pydantic-transaction
```

- [ ] **Step 2: Remove Marshmallow Schemas from `payload.py`**

Edit `src/cancelchain/payload.py`. Delete:
- The `class Subject(fields.String):` definition.
- The `class OutflowSchema(SansNoneSchema):` definition (including `@validates_schema validate_destinations` and `@post_load make_outflow` methods).
- The `class InflowSchema(SansNoneSchema):` definition (including `@post_load make_inflow`).
- The `from marshmallow import (...)` import line.
- The `from cancelchain.schema import Address, MillHash, SansNoneSchema` line — replace with `from cancelchain.schema import Address, MillHash` (drop `SansNoneSchema`).

Rename the `SubjectType` alias to `Subject`:
```python
# Before:
SubjectType = Annotated[str, AfterValidator(_check_subject)]
# After:
Subject = Annotated[str, AfterValidator(_check_subject)]
```

And update `OutflowModel` field annotations from `subject: SubjectType | None` → `subject: Subject | None`, same for `forgive` and `support`.

The file-level `# mypy: disable-error-code="no-untyped-call,no-any-return"` directive can be removed (no Marshmallow imports remain).

- [ ] **Step 3: Rewrite `TransactionSchema` family in `transaction.py`**

Edit `src/cancelchain/transaction.py`. Replace lines 1–101 (imports through `CoinbaseTransactionSchema`) with:

```python
from __future__ import annotations

from collections.abc import Generator, Iterator, MutableSet
from dataclasses import dataclass, field
from datetime import datetime
from json import JSONDecodeError
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from cancelchain.exceptions import (
    InvalidSignatureError,
    InvalidTransactionError,
    InvalidTransactionIdError,
    MissingWalletError,
    UnsealedTransactionError,
)
from cancelchain.milling import mill_hash_str
from cancelchain.models import (
    InflowDAO,
    OutflowDAO,
    PendingIOflowDAO,
    PendingTxnDAO,
    TransactionDAO,
)
from cancelchain.payload import Inflow, InflowModel, Outflow, OutflowModel
from cancelchain.schema import (
    Address,
    Base64,
    MillHash,
    PublicKey,
    Timestamp,
    asdict_sans_none,
    pydantic_errors_to_messages,
    validate_address,
    validate_signature,
)
from cancelchain.util import dt_2_iso, iso_2_dt, now_iso
from cancelchain.wallet import Wallet

VERSION_1 = '1'
MAX_FLOWS = 50
ADDRESS_MISMATCH_MSG = 'Address/public key mismatch'


class TransactionModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    timestamp: Timestamp
    txid: MillHash
    address: Address
    public_key: PublicKey
    signature: Base64 | None = None
    inflows: Annotated[
        list[InflowModel], Field(min_length=0, max_length=MAX_FLOWS)
    ]
    outflows: Annotated[
        list[OutflowModel], Field(min_length=1, max_length=MAX_FLOWS)
    ]
    version: Literal[VERSION_1]

    @model_validator(mode='after')
    def validate_pk_address(self) -> Self:
        if not validate_address(self.public_key, self.address):
            raise ValueError(ADDRESS_MISMATCH_MSG)
        return self


class RegularTransactionModel(TransactionModel):
    inflows: Annotated[
        list[InflowModel], Field(min_length=1, max_length=MAX_FLOWS)
    ]


class CoinbaseTransactionModel(TransactionModel):
    inflows: Annotated[
        list[InflowModel], Field(min_length=0, max_length=0)
    ]
    outflows: Annotated[
        list[OutflowModel], Field(min_length=1, max_length=4)
    ]
```

Notes:
- Import block reorganized: Marshmallow removed; `pydantic.ValidationError` added; `Annotated`, `Literal`, `Self` to typing; payload imports updated to `InflowModel`, `OutflowModel`; schema imports add `pydantic_errors_to_messages`.
- `Address`, `Base64`, `MillHash`, `PublicKey`, `Timestamp`, `SansNoneSchema` imports updated to drop `SansNoneSchema`.
- File-level `# mypy: disable-error-code` directive removed (no Marshmallow imports).

- [ ] **Step 4: Update call sites in `transaction.py`**

Find the `Transaction.validate` method (around line 206 originally):

```python
    def validate(self, coinbase: bool = False) -> None:  # noqa: FBT001
        if coinbase:
            errors = CoinbaseTransactionSchema().validate(self.to_dict())
        else:
            errors = RegularTransactionSchema().validate(self.to_dict())
        if errors:
            raise InvalidTransactionError(errors)
        self.validate_signature()
        self.validate_txid()
```

Replace with:

```python
    def validate(self, coinbase: bool = False) -> None:  # noqa: FBT001
        Model = (
            CoinbaseTransactionModel if coinbase else RegularTransactionModel
        )
        try:
            Model.model_validate(self.to_dict())
        except ValidationError as e:
            raise InvalidTransactionError(
                pydantic_errors_to_messages(e)
            ) from e
        self.validate_signature()
        self.validate_txid()
```

Find `Transaction.to_json`:

```python
    def to_json(self) -> str:
        return TransactionSchema().dumps(self.to_dict())
```

Replace with:

```python
    def to_json(self) -> str:
        return TransactionModel.model_validate(self.to_dict()).model_dump_json(
            exclude_none=True
        )
```

**Nested reconstruction is required for `from_dict` and `from_json`.** `TransactionModel.model_dump()` returns `inflows`/`outflows` as `list[dict]`, but the `Transaction` dataclass expects `list[Inflow]` / `list[Outflow]`. Marshmallow's `@post_load` cascade did the conversion implicitly; Pydantic does not, so we do it explicitly with a private helper.

Add this helper near the top of the file (just below the `*Model` class definitions):

```python
def _txn_from_model_data(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a TransactionModel.model_dump() dict's nested lists from
    list[dict] to list[Inflow] / list[Outflow] before passing to the
    Transaction dataclass constructor.
    """
    data['inflows'] = [Inflow(**i) for i in data.get('inflows', [])]
    data['outflows'] = [Outflow(**o) for o in data.get('outflows', [])]
    return data
```

Find `Transaction.from_dict`:

```python
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        try:
            return TransactionSchema().load(d)
        except ValidationError as e:
            raise InvalidTransactionError(e.messages) from e
```

Replace with:

```python
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        try:
            model = TransactionModel.model_validate(d)
        except ValidationError as e:
            raise InvalidTransactionError(
                pydantic_errors_to_messages(e)
            ) from e
        return cls(**_txn_from_model_data(model.model_dump()))
```

Find `Transaction.from_json`:

```python
    @classmethod
    def from_json(cls, j: str | bytes) -> Self:
        try:
            return TransactionSchema().loads(j)
        except (JSONDecodeError, ValidationError) as e:
            raise InvalidTransactionError(
                getattr(e, 'messages', str(e))
            ) from e
```

Replace with:

```python
    @classmethod
    def from_json(cls, j: str | bytes) -> Self:
        try:
            model = TransactionModel.model_validate_json(j)
        except ValidationError as e:
            raise InvalidTransactionError(
                pydantic_errors_to_messages(e)
            ) from e
        except JSONDecodeError as e:
            raise InvalidTransactionError(str(e)) from e
        return cls(**_txn_from_model_data(model.model_dump()))
```

Without the `_txn_from_model_data` step, `Transaction.inflows[0].outflow_txid` would raise `AttributeError` because the elements are plain dicts, not `Inflow` instances. Block-level reconstruction (PR-4) imports this helper too — see Task 5 Step 4.

- [ ] **Step 5: Verify**

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

Expected:
- mypy + ruff: 0 errors.
- pytest: 177 passed, 1 skipped. The key tests are `tests/test_transaction.py` (all 36+ tests including the P3-PR-7.5 regression set) and `tests/test_payload.py`.

If any test fails:
- `test_txn_from` / `test_db` / `test_pending_txns` — these round-trip JSON through `to_json` / `from_json`. If the JSON output differs from before (e.g., key ordering, integer-vs-string, datetime format), Pydantic vs Marshmallow output isn't byte-equivalent yet. Inspect the diff with `print(repr(txn.to_json()))` before and after.
- `test_txn_invalid` — exercises the validate error path. If Pydantic's error message wording differs from Marshmallow's, the test assertion needs adjustment (probably matching on `'Address/public key mismatch'` is what we want anyway since that's the constant).

- [ ] **Step 6: Commit**

```bash
git add src/cancelchain/transaction.py src/cancelchain/payload.py
git commit -m "$(cat <<'EOF'
feat(deps): transaction schemas → Pydantic v2 BaseModel

Replaces TransactionSchema / RegularTransactionSchema /
CoinbaseTransactionSchema with TransactionModel and two subclasses.

- @validates_schema validate_pk_address → @model_validator(mode='after').
- @post_load make_transaction → removed; callers do
  cls(**Model.model_validate(d).model_dump()) explicitly.
- validate.Equal(VERSION_1) → Literal[VERSION_1].
- validate.Length(min=N, max=M) → Field(min_length=N, max_length=M).
- TransactionSchema().validate(...) → Model.model_validate(...) wrapped
  in try/except ValidationError; pydantic_errors_to_messages adapter
  preserves the message-dict shape downstream consumers expect.
- TransactionSchema().dumps(...) → Model.model_validate(...).model_dump_json(exclude_none=True).
- TransactionSchema().load(...) → Model.model_validate(d) then dataclass conversion.
- TransactionSchema().loads(j) → Model.model_validate_json(j) then dataclass conversion.

payload.py: removes the now-unused Marshmallow OutflowSchema /
InflowSchema / Subject(fields.String); renames the Pydantic SubjectType
alias to Subject. File-level # mypy: disable-error-code directive
removed from both files.

Phase 4 / PR 3 of 6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin feat/pydantic-transaction
gh pr create --base main --title "feat(deps): transaction schemas → Pydantic v2" --body "$(cat <<'EOF'
## Summary
- TransactionSchema family (3 classes) → TransactionModel family.
- payload.py: removes Marshmallow OutflowSchema/InflowSchema/Subject; renames Pydantic SubjectType → Subject.
- @post_load removed — callers do \`cls(**Model.model_validate(d).model_dump())\`.
- @validates_schema → @model_validator(mode='after').
- Pydantic ValidationError caught at call sites; \`pydantic_errors_to_messages\` adapter preserves downstream consumers.
- File-level mypy disable directive removed from \`transaction.py\` and \`payload.py\`.

Phase 4 / PR 3 of 6.

## Test plan
- [x] \`uv run pytest\` passes (177/178), including round-trip JSON tests (test_txn_from, test_db, test_pending_txns).
- [x] \`uv run mypy\` + ruff clean.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Stop — controller handles wor + mwg + sync**

---

## Task 5: PR-4 — `block.py` schema + call sites

**Files:**
- Modify: `src/cancelchain/block.py`

Replace `BlockSchema(SansNoneSchema)` with `BlockModel(BaseModel)`. Rewrite the four call sites in `Block.validate`, `Block.to_json`, `Block.from_dict`, `Block.from_json`.

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/pydantic-block
```

- [ ] **Step 2: Rewrite `BlockSchema` → `BlockModel` in `block.py`**

Edit `src/cancelchain/block.py`. Replace lines 1–81 (imports through `BlockSchema`) with:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from json import JSONDecodeError
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from pymerkle import InmemoryTree, InvalidProof, verify_inclusion

from cancelchain.exceptions import (
    ExpiredTransactionError,
    FutureTransactionError,
    InvalidBlockError,
    InvalidBlockHashError,
    InvalidCoinbaseError,
    InvalidMerkleRootError,
    InvalidProofError,
    InvalidTransactionError,
    MissingCoinbaseError,
    OutOfOrderTransactionError,
    SealedBlockError,
    UnlinkedBlockError,
)
from cancelchain.milling import mill_hash_str, milling_generator
from cancelchain.models import BlockDAO
from cancelchain.schema import (
    MillHash,
    Timestamp,
    asdict_sans_none,
    pydantic_errors_to_messages,
)
from cancelchain.transaction import Transaction, TransactionModel
from cancelchain.util import dt_2_iso, iso_2_dt, now_iso
from cancelchain.wallet import Wallet

VERSION_1 = '1'
MAX_TRANSACTIONS = 100
TXN_TIMEOUT = timedelta(hours=4)
MISSED_TARGET_MSG = 'Missed target'


def validate_hash_diff(block_hash: str, target: str) -> bool:
    return int(block_hash, 16) < int(target, 16)


class BlockModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    idx: int = Field(ge=0)
    timestamp: Timestamp
    block_hash: MillHash
    prev_hash: MillHash
    target: MillHash
    proof_of_work: int = Field(ge=0)
    merkle_root: MillHash
    txns: Annotated[
        list[TransactionModel],
        Field(min_length=1, max_length=MAX_TRANSACTIONS),
    ]
    version: Literal[VERSION_1]

    @model_validator(mode='after')
    def validate_difficulty(self) -> Self:
        if not validate_hash_diff(self.block_hash, self.target):
            raise ValueError(MISSED_TARGET_MSG)
        return self
```

Notes:
- Marshmallow import removed (`from marshmallow import (ValidationError, ...)`).
- `SansNoneSchema` import removed.
- `TransactionSchema` import → `TransactionModel`.
- `pydantic_errors_to_messages` added to schema imports.
- File-level `# mypy: disable-error-code` directive removed.

- [ ] **Step 3: Update call sites in `block.py`**

Find the validate call in `Block.validate_transactions` (around line 278):

```python
        if errors := BlockSchema().validate(self.to_dict()):
            raise InvalidBlockError(errors)
```

Replace with:

```python
        try:
            BlockModel.model_validate(self.to_dict())
        except ValidationError as e:
            raise InvalidBlockError(
                pydantic_errors_to_messages(e)
            ) from e
```

Find `Block.to_json`:

```python
    def to_json(self) -> str:
        return BlockSchema().dumps(self.to_dict())
```

Replace with:

```python
    def to_json(self) -> str:
        return BlockModel.model_validate(self.to_dict()).model_dump_json(
            exclude_none=True
        )
```

**Nested reconstruction.** `BlockModel.model_dump()` returns `txns` as `list[dict]`, but `Block` expects `list[Transaction]`. Each of those nested dicts in turn has `inflows`/`outflows` as `list[dict]` that need to become `list[Inflow]` / `list[Outflow]`. The `_txn_from_model_data` helper from PR-3 (in `transaction.py`) does the inner conversion; reuse it for the inflow/outflow part and wrap with `Transaction(**...)` for each txn.

Add this private helper near the top of `block.py` (just below `BlockModel`):

```python
from cancelchain.transaction import _txn_from_model_data  # noqa: PLC2701


def _block_from_model_data(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a BlockModel.model_dump() dict's txns list from
    list[dict] to list[Transaction] (with nested Inflow/Outflow
    instances already reconstructed) before passing to the Block
    dataclass constructor.
    """
    data['txns'] = [
        Transaction(**_txn_from_model_data(t)) for t in data.get('txns', [])
    ]
    return data
```

If `noqa: PLC2701` (private-name import) feels unclean, an acceptable alternative is to expose `_txn_from_model_data` under a public name (e.g., `txn_from_model_data`) in PR-3 by removing the underscore; that's a one-line plan tweak you can do at implementation time.

Find `Block.from_dict`:

```python
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        try:
            return BlockSchema().load(d)
        except ValidationError as e:
            raise InvalidBlockError(e.messages) from e
```

Replace with:

```python
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        try:
            model = BlockModel.model_validate(d)
        except ValidationError as e:
            raise InvalidBlockError(
                pydantic_errors_to_messages(e)
            ) from e
        return cls(**_block_from_model_data(model.model_dump()))
```

Find `Block.from_json`:

```python
    @classmethod
    def from_json(cls, j: str | bytes) -> Self:
        try:
            return BlockSchema().loads(j)
        except (JSONDecodeError, ValidationError) as ve:
            raise InvalidBlockError(
                getattr(ve, 'messages', str(ve))
            ) from ve
```

Replace with:

```python
    @classmethod
    def from_json(cls, j: str | bytes) -> Self:
        try:
            model = BlockModel.model_validate_json(j)
        except ValidationError as e:
            raise InvalidBlockError(
                pydantic_errors_to_messages(e)
            ) from e
        except JSONDecodeError as e:
            raise InvalidBlockError(str(e)) from e
        return cls(**_block_from_model_data(model.model_dump()))
```

Also update the test at `tests/test_block.py:158` — Pydantic phrases the txn-overflow violation differently:

```python
# Before:
with pytest.raises(
    InvalidBlockError, match='Length must be between 1 and 100'
):
# After:
with pytest.raises(
    InvalidBlockError, match='List should have at most 100 items'
):
```

Plus any other Marshmallow-specific message assertions surfaced by the test run (see the spec's "Test-message risk" note). Run pytest first and fix message matches one at a time.

- [ ] **Step 4: Verify**

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

Expected: 177 passed, 1 skipped. Highest-risk tests: `tests/test_block.py` (13 tests including `test_to_dao_partial_block_raises`), `tests/test_chain.py` (24+ tests that exercise blocks end-to-end).

If `model_dump()`-related `AttributeError`s appear (e.g., `'dict' object has no attribute 'outflows'`), confirm `_block_from_model_data` is wired into both `from_dict` and `from_json` and that `_txn_from_model_data` was added in PR-3.

- [ ] **Step 5: Commit**

```bash
git add src/cancelchain/block.py
git commit -m "$(cat <<'EOF'
feat(deps): block schema → Pydantic v2 BaseModel

Replaces BlockSchema with BlockModel(BaseModel).

- @validates_schema validate_difficulty → @model_validator(mode='after').
- @post_load make_block → removed; from_dict/from_json explicitly
  reconstruct nested Transaction instances from the model_dump() dicts.
- fields.List(fields.Nested(TransactionSchema), validate=...) →
  Annotated[list[TransactionModel], Field(min_length=1, max_length=...)].
- BlockSchema().validate(...) → Model.model_validate(...) +
  pydantic_errors_to_messages adapter at the catch site.
- BlockSchema().dumps(...) → Model.model_validate(...).model_dump_json(exclude_none=True).

File-level # mypy: disable-error-code directive removed.

Phase 4 / PR 4 of 6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Push and open PR**

```bash
git push -u origin feat/pydantic-block
gh pr create --base main --title "feat(deps): block schema → Pydantic v2" --body "$(cat <<'EOF'
## Summary
- BlockSchema → BlockModel.
- @validates_schema validate_difficulty → @model_validator(mode='after').
- @post_load removed; from_dict / from_json explicitly reconstruct nested Transaction instances.
- pydantic_errors_to_messages adapter at catch sites.
- File-level mypy disable directive removed.

Phase 4 / PR 4 of 6.

## Test plan
- [x] \`uv run pytest\` passes (177/178), including merkle-tree validation and recursive-CTE-traversal tests.
- [x] \`uv run mypy\` + ruff clean.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7: Stop — controller handles wor + mwg + sync**

---

## Task 6: PR-5 — `api.py` query schemas

**Files:**
- Modify: `src/cancelchain/api.py`

Replace the 3 query schemas (`TransferTxnQuerySchema`, `SubjectTxnQuerySchema`, `PendingTxnQuerySchema`) with Pydantic BaseModels. Update the 5 call sites that invoke `.load(request.args)`.

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/pydantic-api-queries
```

- [ ] **Step 2: Update `api.py` imports and remove Marshmallow**

Edit `src/cancelchain/api.py`. Find the Marshmallow import block:

```python
from marshmallow import Schema, ValidationError, fields, validate
```

Replace with:

```python
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    ValidationError,
)
```

Add `pydantic_errors_to_messages` to the existing `from cancelchain.schema import (...)` line.

- [ ] **Step 3: Replace `TransferTxnQuerySchema` (around line 370)**

Replace:

```python
class TransferTxnQuerySchema(Schema):
    public_key = fields.String(required=True, validate=validate_public_key)
    amount = fields.Integer(required=True, validate=validate.Range(min=1))
    address = fields.String(required=True, validate=validate_address_format)
```

With:

```python
def _check_address_format_local(s: str) -> str:
    if not validate_address_format(s):
        msg = f'Invalid address format: {s!r}'
        raise ValueError(msg)
    return s


def _check_public_key_local(s: str) -> str:
    if not validate_public_key(s):
        msg = f'Invalid public key: {s!r}'
        raise ValueError(msg)
    return s


_PublicKeyField = Annotated[str, AfterValidator(_check_public_key_local)]
_AddressFormatField = Annotated[
    str, AfterValidator(_check_address_format_local)
]


class TransferTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    public_key: _PublicKeyField
    amount: int = Field(ge=1)
    address: _AddressFormatField
```

Notes:
- The query schemas use `validate_address_format` (different from `validate_address` which checks pk⇔address match). The custom `_AddressFormatField` reflects that.
- The `_PublicKeyField` here matches the existing `validate_public_key` behavior in the query context, which is structurally identical to `schema.PublicKey` BUT uses a different validator path — keep them parallel.

Alternative (cleaner): reuse `schema.PublicKey`. But that runs through `validate_public_key(public_key_b64)` which expects a full b64-encoded key. The query schema validates `public_key=...` from URL args which is also a b64 string. So they ARE the same. Use `schema.PublicKey` directly:

```python
from cancelchain.schema import Address as _AddressType
from cancelchain.schema import PublicKey as _PublicKeyType

# Note: `schema.Address` validates the CC-tagged address format, which
# is what `validate_address_format` does here. Reuse.

class TransferTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    public_key: _PublicKeyType
    amount: int = Field(ge=1)
    address: _AddressType
```

**Final decision: reuse `schema.Address` and `schema.PublicKey`** to avoid type duplication. Verify `schema.Address`'s `_check_address_format` matches the existing `validate_address_format` validator — yes, both run `validate_address_format(s)`. Same for `schema.PublicKey` ↔ `validate_public_key`.

- [ ] **Step 4: Replace `SubjectTxnQuerySchema` (around line 405)**

Replace:

```python
class SubjectTxnQuerySchema(Schema):
    public_key = fields.String(required=True, validate=validate_public_key)
    amount = fields.Integer(required=True, validate=validate.Range(min=1))
    subject = fields.String(required=True, validate=validate_raw_subject)
```

With:

```python
def _check_raw_subject(s: str) -> str:
    if not validate_raw_subject(s):
        msg = f'Invalid raw subject: {s!r}'
        raise ValueError(msg)
    return s


_RawSubjectField = Annotated[str, AfterValidator(_check_raw_subject)]


class SubjectTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    public_key: _PublicKeyType
    amount: int = Field(ge=1)
    subject: _RawSubjectField
```

(`validate_raw_subject` differs from `schema._check_address_format` etc. — it accepts the raw, pre-encoded user-supplied subject string. Keeping the local check makes the intent explicit.)

- [ ] **Step 5: Replace `PendingTxnQuerySchema` (around line 498)**

Replace:

```python
class PendingTxnQuerySchema(Schema):
    earliest = fields.Function(
        lambda obj: dt_2_ciso(obj.earliest),
        deserialize=ciso_2_dt,
        required=False,
    )
```

With:

```python
_CisoTimestamp = Annotated[
    datetime,
    BeforeValidator(lambda v: ciso_2_dt(v) if isinstance(v, str) else v),
    PlainSerializer(lambda dt: dt_2_ciso(dt), return_type=str),
]


class PendingTxnQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    earliest: _CisoTimestamp | None = None
```

This requires `from datetime import datetime` in the imports if it isn't already present (verify; `api.py` already uses datetime elsewhere).

- [ ] **Step 6: Update the 5 call sites**

Find each occurrence of `QuerySchema().load(request.args)` (lines 379, 414, 443, 472, 511). Replace each pattern:

```python
# Before:
args = TransferTxnQuerySchema().load(request.args)
# ... uses args['public_key'], args['amount'], args['address'] ...
```

With:

```python
# After:
try:
    model = TransferTxnQueryModel.model_validate(
        request.args.to_dict(flat=True)
    )
except ValidationError as e:
    return make_error_response(_pydantic_validation_error(e))
args = model.model_dump(exclude_none=True)
# ... uses args['public_key'], args['amount'], args['address'] ...
```

Where `_pydantic_validation_error(e)` is a small helper added at the top of `api.py`:

```python
def _pydantic_validation_error(e: ValidationError) -> Any:
    """Wrap a Pydantic ValidationError into the shape make_error_response expects."""
    return type(
        'AdaptedValidationError',
        (Exception,),
        {'messages': pydantic_errors_to_messages(e)},
    )()
```

This avoids modifying `make_error_response` itself — the existing function expects `err.messages`; we hand it an object with that attribute.

Apply the same pattern to:
- `SubjectTxnQuerySchema().load(request.args)` at lines 414, 443, 472 — use `SubjectTxnQueryModel`.
- `PendingTxnQuerySchema().load(request.args)` at line 511 — use `PendingTxnQueryModel`.

For the `PendingTxnQueryModel` site, the existing code does `args.get('earliest')` which returns a `datetime | None`. The `args = model.model_dump(exclude_none=True)` line works the same way — if `earliest` is None, it's not in the dict; `args.get('earliest')` returns `None` either way.

- [ ] **Step 7: Remove unused imports**

After the swap, `api.py` no longer needs:
- `from marshmallow import Schema, ValidationError, fields, validate` — already replaced.
- Verify the existing `from cancelchain.schema import validate_address_format, validate_public_key` is still needed (it IS — for backward compat in the original query schemas; but if all schemas now use the Address/PublicKey types directly, these can come out).

Run:
```bash
uv run ruff check src/cancelchain/api.py
```
Ruff will flag unused imports. Drop those it identifies.

- [ ] **Step 8: Verify**

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

Expected: 177 passed, 1 skipped. Tests exercising the API endpoints (`tests/test_api.py`, `tests/test_browser.py`) verify the new validation path.

If any test fails:
- Bad-input rejection tests (e.g., `test_invalid_amount`) — check that Pydantic's error response shape matches what tests assert. They probably assert on HTTP 400 status, not the response body, so should be fine.
- Endpoint smoke tests — verify `request.args.to_dict(flat=True)` returns dict-like data that Pydantic accepts.

- [ ] **Step 9: Commit**

```bash
git add src/cancelchain/api.py
git commit -m "$(cat <<'EOF'
feat(deps): API query schemas → Pydantic v2

Replaces the 3 Marshmallow query schemas with Pydantic models:

- TransferTxnQuerySchema → TransferTxnQueryModel (reuses
  schema.Address and schema.PublicKey custom types).
- SubjectTxnQuerySchema → SubjectTxnQueryModel (with local raw-subject
  validator).
- PendingTxnQuerySchema → PendingTxnQueryModel (with
  Annotated[datetime, BeforeValidator(ciso_2_dt),
  PlainSerializer(dt_2_ciso)] for the ciso-timestamp parse/format
  symmetry that fields.Function provided).

Each of the 5 call sites updated to use Model.model_validate(
  request.args.to_dict(flat=True)) wrapped in try/except. Small
_pydantic_validation_error adapter preserves the .messages attribute
that make_error_response expects.

Marshmallow import block removed from api.py. The Phase 1 spec's
"Marshmallow → Pydantic" Phase 4 goal is now complete for src/;
PR-6 finishes by removing marshmallow from runtime deps.

Phase 4 / PR 5 of 6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 10: Push and open PR**

```bash
git push -u origin feat/pydantic-api-queries
gh pr create --base main --title "feat(deps): API query schemas → Pydantic v2" --body "$(cat <<'EOF'
## Summary
- 3 Marshmallow query schemas → Pydantic models.
- 5 call sites updated to use \`Model.model_validate(request.args.to_dict(flat=True))\`.
- PendingTxnQueryModel uses \`Annotated[datetime, BeforeValidator(ciso_2_dt), PlainSerializer(dt_2_ciso)]\` to replace \`fields.Function\`.
- Marshmallow import removed from \`api.py\`.

After this PR, no \`src/cancelchain/\` file imports marshmallow. PR-6 removes the runtime dep + overrides.

Phase 4 / PR 5 of 6.

## Test plan
- [x] \`uv run pytest\` passes (177/178), including API endpoint tests.
- [x] \`uv run mypy\` + ruff clean.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 11: Stop — controller handles wor + mwg + sync**

---

## Task 7: PR-6 — Remove `marshmallow` from runtime dependencies

**Files:**
- Modify: `pyproject.toml`
- Possibly modify: `src/cancelchain/schema.py` (remove the transitional `SansNoneSchema` alias if PR-1 left it)
- Possibly modify: `src/cancelchain/transaction.py` (verify directive removal — should already be done in PR-3)
- Possibly modify: `src/cancelchain/models.py` (review only — directive there is for `db.Model`, not marshmallow)

- [ ] **Step 1: Branch off main**

```bash
git checkout main && git pull --ff-only
git checkout -b chore/remove-marshmallow
```

- [ ] **Step 2: Verify no Marshmallow imports remain**

```bash
grep -rn "marshmallow\|Marshmallow" src/cancelchain/
```

Expected: no matches. If `schema.py` still has the transitional `SansNoneSchema` alias from PR-1 (the `from marshmallow import Schema as _MarshmallowSchema`), remove it now.

- [ ] **Step 3: Edit `pyproject.toml`**

Remove the line:
```toml
"marshmallow>=3.19",
```
from `[project.dependencies]`.

Remove the block:
```toml
[[tool.mypy.overrides]]
module = ["marshmallow", "marshmallow.*"]
ignore_missing_imports = true
```
from `[tool.mypy]` overrides.

- [ ] **Step 4: Refresh the lockfile**

```bash
uv lock
grep -i marshmallow uv.lock
```
Expected: `grep` returns no matches. The lockfile is regenerated without marshmallow.

- [ ] **Step 5: Re-sync and verify**

```bash
uv sync --group dev
uv run python -c "import marshmallow" 2>&1 | head -2
```
Expected: `ModuleNotFoundError: No module named 'marshmallow'`.

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```
All must exit 0.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/cancelchain/schema.py
git commit -m "$(cat <<'EOF'
chore(deps): remove marshmallow from runtime dependencies

After PRs 1-5 swapped every Marshmallow Schema for a Pydantic v2
BaseModel, no source code under src/cancelchain/ imports marshmallow.
This PR finishes the migration:

- Removes \`marshmallow>=3.19\` from [project.dependencies].
- Removes [[tool.mypy.overrides]] module = ["marshmallow", "marshmallow.*"].
- Removes the transitional SansNoneSchema alias from schema.py (if PR-1
  added one).

uv lock regenerated; marshmallow no longer in uv.lock.

Phase 4 / PR 6 of 6 (final PR of Phase 4).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin chore/remove-marshmallow
gh pr create --base main --title "chore(deps): remove marshmallow from runtime dependencies" --body "$(cat <<'EOF'
## Summary
- Removes \`marshmallow>=3.19\` from \`[project.dependencies]\`.
- Removes the \`[[tool.mypy.overrides]]\` block for \`marshmallow.*\`.
- Removes the transitional \`SansNoneSchema\` alias from \`schema.py\` (if PR-1 left one).
- \`uv lock\` regenerated; \`marshmallow\` no longer in \`uv.lock\`.

Phase 4 / PR 6 of 6 (final PR).

## Test plan
- [x] \`grep -rn marshmallow src/\` returns nothing.
- [x] \`grep marshmallow uv.lock\` returns nothing.
- [x] \`uv run python -c "import marshmallow"\` raises ModuleNotFoundError.
- [x] \`uv run pytest\` passes (177/178).
- [x] \`uv run mypy\` + \`ruff check\` + \`ruff format --check\` all exit 0 (hard CI gates).
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Stop — controller handles wor + mwg + sync**

---

## Task 8: Phase 4 acceptance verification

**Files:** none modified. Final verification after all 6 impl PRs land.

- [ ] **Step 1: Confirm clean main**

```bash
git checkout main && git pull --ff-only
git log --oneline -8
```
Expected: 6 Phase 4 squash-merge commits visible.

- [ ] **Step 2: Fresh-clone simulation**

```bash
rm -rf .venv
uv sync --group dev
uv run python --version
```
Expected: Python 3.12.x.

- [ ] **Step 3: Marshmallow absent**

```bash
grep -rn marshmallow src/
echo ""
grep marshmallow pyproject.toml
echo ""
grep marshmallow uv.lock | head
echo ""
uv run python -c "import marshmallow" 2>&1 | head -3
```
Expected: nothing on first three; ModuleNotFoundError on fourth.

- [ ] **Step 4: Hard CI gates pass**

```bash
uv run ruff check src tests; echo "exit: $?"
uv run ruff format --check src tests; echo "exit: $?"
uv run mypy; echo "exit: $?"
```
All three exit 0.

- [ ] **Step 5: Tests pass on 3.12 and 3.13**

```bash
uv sync --group dev --python 3.12
uv run pytest 2>&1 | tail -3
```
Expected: 177 passed, 1 skipped.

```bash
UV_PYTHON=3.13 uv sync --group dev --python 3.13 --reinstall
UV_PYTHON=3.13 uv run --python 3.13 pytest 2>&1 | tail -3
```
Expected: same.

- [ ] **Step 6: CLI smoke**

```bash
uv run cancelchain --help
```
Expected: full command tree prints.

- [ ] **Step 7: Docker build smoke**

```bash
docker build -t cc-phase4-final .
```
Expected: build succeeds.

- [ ] **Step 8: Acceptance complete**

If Steps 1–7 all pass, Phase 4 is done. No commit.

---

## Notes on the wor / mwg workflow

Each impl PR (Tasks 2–7) ends with the controller running `wor` and `mwg`:

1. **`wor`:** poll PR until Copilot review completes. Read inline comments. Reply one at a time with verified `in_reply_to_id` (per the user's memory). User manually resolves threads.
2. **`mwg`:** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

Never skip `wor`, even when CI is green and local tests pass. Copilot consistently caught real bugs in Phases 2 and 3.

If Copilot review requests substantive changes, push a new commit to the PR branch (do not amend) and re-run `wor`.

---

## Risk: nested-model `model_dump` returns plain dicts

The biggest pitfall in Tasks 4 (transaction) and 5 (block) is the `cls(**model.model_dump())` pattern when the model has nested fields.

For `Transaction`, `model.model_dump()` returns `{'inflows': [{...}], 'outflows': [{...}], ...}` — those nested lists are `list[dict]`, but `Transaction.__init__` expects `list[Inflow]` / `list[Outflow]`.

For `Block`, same issue with `txns: list[Transaction]`.

The fix at each from_dict / from_json site: explicitly reconstruct the nested dataclasses from their dicts before passing to the outer dataclass constructor:

```python
data = model.model_dump()
data['inflows'] = [Inflow(**i) for i in data['inflows']]
data['outflows'] = [Outflow(**o) for o in data['outflows']]
return cls(**data)
```

Plan steps for Tasks 4 and 5 include this fix explicitly. If a test like `test_txn_from` fails with "AttributeError: 'dict' object has no attribute X" on a nested field, this is the cause.

---

## Roll-back posture

Each PR squash-merged independently. Forward-fix is preferred over revert because:
- Revert would re-introduce Marshmallow + remove Pydantic mid-stream
- Later PRs depend on earlier ones (PR-3 imports `OutflowModel` introduced in PR-2)

For a defect found post-merge, prefer a `fix(deps): ...` PR. If a structural problem requires reverting a PR, all subsequent PRs need to be reverted too, in reverse order.
