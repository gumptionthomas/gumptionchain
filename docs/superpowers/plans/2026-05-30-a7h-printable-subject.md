# A7.h — Printable-Subject Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject subjects whose decoded raw string contains non-printable characters, at the validation layer.

**Architecture:** Add a `_valid_raw_subject(raw)` helper in `payload.py` (length bound + `str.isprintable()`) and route both `validate_subject` (after `decode_subject`) and `validate_raw_subject` through it. Single-file change; no schema change.

**Tech Stack:** Python 3.12, Pydantic v2 (the `Subject` annotated type backs `OutflowModel`), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-05-30-a7h-printable-subject-design.md`

---

## Prerequisites (read before starting)

- **Full-suite pytest needs `COLUMNS=200`** (a latent unrelated terminal-width bug in `tests/test_command.py::test_create_wallet`). Use `COLUMNS=200 uv run pytest` for full-suite runs.
- `str.isprintable()` semantics: returns `True` only if every character is printable — i.e. NOT in a Unicode "Other" category (`Cc` control, `Cf` format incl. bidi/ZWJ/zero-width, `Cs` surrogate, `Co` private-use, `Cn` unassigned) and NOT a "Separator" other than the ASCII space `U+0020`. It allows letters, marks, numbers, punctuation, symbols/emoji, and plain spaces. (`''.isprintable()` is `True`, but the length bound rejects empty separately.)
- `test_a7_h_non_printable_subject_accepted` is `@pytest.mark.xfail(strict=True)`. Because the fix makes it pass, its `xfail` MUST be removed in the **same commit** as the fix — otherwise the strict xfail turns an unexpected pass into a CI failure. That is why Task 1 un-xfails it before implementing.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/cancelchain/payload.py` | Subject encode/decode/validation | Add `_valid_raw_subject`; route both validators through it |
| `tests/test_payload.py` | payload unit tests | Add printability unit tests |
| `tests/test_verification_audit.py` | audit demonstration/regression tests | Un-xfail `test_a7_h…` |
| `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` | audit record | Mark A7.h remediated; update counts |
| `docs/superpowers/ROADMAP.md` | roadmap | Move A7.h to Closed; severity → 0/0/0/2 |

---

### Task 1: `_valid_raw_subject` + printability enforcement (un-xfail A7.h)

**Files:**
- Modify: `src/cancelchain/payload.py` (`validate_subject` lines 39-46, `validate_raw_subject` lines 49-55)
- Modify: `tests/test_payload.py` (imports + new tests)
- Modify: `tests/test_verification_audit.py` (remove A7.h xfail decorator, lines 556-568)

- [ ] **Step 1: Un-xfail the acceptance demonstrator**

In `tests/test_verification_audit.py`, delete the `@pytest.mark.xfail(...)` decorator block (lines 556-568) directly above `def test_a7_h_non_printable_subject_accepted`. The decorator to remove is exactly:

```python
@pytest.mark.xfail(
    reason=(
        'Audit finding A7.h — severity Low — validate_subject and '
        'validate_raw_subject (payload.py:39-55) enforce only length '
        'bounds and canonical base64-url round-trip; they accept any '
        'UTF-8 codepoint including null bytes, C0/C1 control characters, '
        'bidirectional override (RLO), and zero-width joiners. Subjects '
        'flow into CLI and API rendering paths that are unlikely to '
        'sanitize control bytes. See '
        'docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md'
    ),
    strict=True,
)
```

Leave `def test_a7_h_non_printable_subject_accepted(...)` and its body unchanged.

- [ ] **Step 2: Write failing unit tests**

In `tests/test_payload.py`, add `validate_raw_subject` and `_valid_raw_subject` to the `from cancelchain.payload import (...)` block (it currently imports `encode_subject, validate_subject` among others). Then append these tests:

```python
def test_validate_subject_rejects_non_printable():
    assert validate_subject(encode_subject('\x1b[31mRED')) is False  # ESC, Cc
    assert validate_subject(encode_subject('a\u202eb')) is False  # RLO, Cf
    assert validate_subject(encode_subject('a\u200db')) is False  # ZWJ, Cf
    assert validate_subject(encode_subject('a\u200bb')) is False  # ZWSP, Cf
    assert validate_subject(encode_subject('a\tb')) is False  # tab, Cc
    assert validate_subject(encode_subject('a\nb')) is False  # newline, Cc


def test_validate_subject_accepts_printable():
    assert validate_subject(encode_subject('Acme Corp')) is True
    assert validate_subject(encode_subject('café')) is True
    assert validate_subject(encode_subject('🍎')) is True


def test_validate_raw_subject_rejects_non_printable():
    assert validate_raw_subject('\x1b[31mRED') is False
    assert validate_raw_subject('a\u202eb') is False
    assert validate_raw_subject('a\u200db') is False
    assert validate_raw_subject('a\u200bb') is False
    assert validate_raw_subject('a\tb') is False


def test_validate_raw_subject_accepts_printable():
    assert validate_raw_subject('Acme Corp') is True
    assert validate_raw_subject('café') is True
    assert validate_raw_subject('🍎') is True


def test_valid_raw_subject_helper():
    assert _valid_raw_subject('Acme Corp') is True
    assert _valid_raw_subject('x' * 79) is True
    assert _valid_raw_subject('\x1b') is False  # control char
    assert _valid_raw_subject('') is False  # below min length
    assert _valid_raw_subject('x' * 80) is False  # above max length
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_payload.py tests/test_verification_audit.py::test_a7_h_non_printable_subject_accepted -v
```
Expected: FAIL. Two distinct failure shapes, both expected and both the "red" signal (do not treat them as a setup mistake):
- `tests/test_payload.py` errors at **collection** with `ImportError: cannot import name '_valid_raw_subject'` — the helper doesn't exist until Step 4. (This is why the whole module errors rather than showing per-test failures; the new unit tests can't run until the import resolves.)
- `test_a7_h_non_printable_subject_accepted` FAILS because `receive_transaction` does not raise on the control-char subject today.

(The accept-case tests — `test_validate_subject_accepts_printable`, `test_validate_raw_subject_accepts_printable` — would pass even without the fix, since the current validators already accept `'Acme Corp'`/`'café'`/`'🍎'`; they're correctness guards that the fix doesn't over-reject, not gap demonstrators.)

- [ ] **Step 4: Implement the helper and route both validators through it**

In `src/cancelchain/payload.py`, the current functions are:

```python
def validate_subject(subject: str) -> bool:
    try:
        raw_subject = decode_subject(subject)
        if MIN_SUBJECT_LENGTH <= len(raw_subject) <= MAX_SUBJECT_LENGTH:
            return encode_subject(raw_subject) == subject
    except Exception:
        pass
    return False


def validate_raw_subject(raw_subject: str) -> bool:
    try:
        if MIN_SUBJECT_LENGTH <= len(raw_subject) <= MAX_SUBJECT_LENGTH:
            return decode_subject(encode_subject(raw_subject)) == raw_subject
    except Exception:
        pass
    return False
```

Replace that block with (adding `_valid_raw_subject` immediately before `validate_subject`):

```python
def _valid_raw_subject(raw_subject: str) -> bool:
    return (
        MIN_SUBJECT_LENGTH <= len(raw_subject) <= MAX_SUBJECT_LENGTH
        and raw_subject.isprintable()
    )


def validate_subject(subject: str) -> bool:
    try:
        raw_subject = decode_subject(subject)
        if _valid_raw_subject(raw_subject):
            return encode_subject(raw_subject) == subject
    except Exception:
        pass
    return False


def validate_raw_subject(raw_subject: str) -> bool:
    try:
        if _valid_raw_subject(raw_subject):
            return decode_subject(encode_subject(raw_subject)) == raw_subject
    except Exception:
        pass
    return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/test_payload.py tests/test_verification_audit.py::test_a7_h_non_printable_subject_accepted -v
```
Expected: PASS (all payload unit tests + the un-xfailed A7.h regression).

- [ ] **Step 6: Verify no subject-validation regression elsewhere**

Run:
```bash
COLUMNS=200 uv run pytest tests/test_payload.py tests/test_chain.py tests/test_transaction.py tests/test_api.py -q
```
Expected: PASS (existing fixtures build only printable subjects, so nothing legitimate is newly rejected).

- [ ] **Step 7: Commit**

```bash
git add src/cancelchain/payload.py tests/test_payload.py tests/test_verification_audit.py
git commit -m "fix(a7h): reject non-printable subjects via str.isprintable()"
```

---

### Task 2: Docs — audit + ROADMAP

**Files:**
- Modify: `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`
- Modify: `docs/superpowers/ROADMAP.md`

> **All Find/Replace edits below are substring replacements** (use the Edit tool): match the quoted "Find" text and swap only that span, preserving any other text on the same line that the Find string doesn't include. Several target lines (e.g. the audit findings-table count, the ROADMAP open-count) continue with trailing sentences that must survive.

- [ ] **Step 1: Update the audit doc**

In `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`:

(a) Intro count (line 9):
Find: `Three have since been remediated (A2.e, A4.c, A7.b); three remain open (A7.h, A7.e, A1.f).`
Replace: `Four have since been remediated (A2.e, A4.c, A7.b, A7.h); two remain open (A7.e, A1.f).`

(b) Findings-table count line (line 38):
Find: `3 open findings: 0 Critical / 0 High / 0 Medium / 3 Low (post-A7.b).`
Replace: `2 open findings: 0 Critical / 0 High / 0 Medium / 2 Low (post-A7.h).`

(c) REMOVE the entire findings-table row for A7.h — the markdown line beginning `| A7.h | Low |` and ending `| `test_a7_h_non_printable_subject_accepted` |`. (The table lists only open findings; A2.e/A4.c/A7.b were removed from it when remediated. After removal the table lists A1.f, A7.e.)

(d) A7.h sub-attack Outcome (line 1043):
Find: `**Outcome:** ACCEPTED. The validation pipeline enforces length but not character-class restrictions on subjects.`
Replace: `**Outcome:** REJECTED (post-remediation). `validate_subject` / `validate_raw_subject` now require the decoded raw subject to satisfy `str.isprintable()`, so control characters, bidi overrides, and zero-width characters are refused. (Pre-remediation, the pipeline enforced length but not character class.)`

(e) Finding A7.h paragraph (line ~1045, the paragraph beginning `**Finding A7.h — Severity Low:**`): prefix it with `✅ **Remediated.** ` and append this sentence to the END of that paragraph:
` Remediated: a `_valid_raw_subject()` helper in `src/cancelchain/payload.py` now requires `str.isprintable()` (in addition to the length bound), and both `validate_subject` (after `decode_subject`) and `validate_raw_subject` route through it. Regression: `test_a7_h_non_printable_subject_accepted` plus unit tests in `tests/test_payload.py`.`
(Match the existing A4.c / A7.b convention — those deep-dives use a `✅ **Remediated.**` / `✅ **Implemented.**` lead-in.)

(f) Remediation-priority section heading (line 1166) and body (line 1168):
- Change the heading `### 4. A7.h (Low) — content-class check in `validate_raw_subject`` to `### 4. A7.h (Low) — ✅ Implemented — printable-subject check in `validate_subject` / `validate_raw_subject``.
- Prefix the body paragraph (line 1168, beginning `The fix lives at …`) with `✅ **Implemented.** `.
- In that paragraph, replace the sentence `Acceptance signal: `test_a7_h_non_printable_subject_accepted` flips from xfail to pass.` with `Acceptance signal: `test_a7_h_non_printable_subject_accepted` is now a passing regression test (xfail removed), with printability unit tests in `tests/test_payload.py`. Implemented via `str.isprintable()` rather than a per-category `unicodedata.category()` loop — stricter (also rejects private-use and non-ASCII whitespace) and simpler.`

- [ ] **Step 2: Update the ROADMAP**

In `docs/superpowers/ROADMAP.md`:

(a) Open-count prose (line 48):
Find: `Three open findings from the 2026-05-29 verification pipeline audit (A2.e, A4.c, and A7.b are closed; see Closed items).`
Replace: `Two open findings from the 2026-05-29 verification pipeline audit (A2.e, A4.c, A7.b, and A7.h are closed; see Closed items).`

(b) Remove the A7.h numbered item (line 52, beginning `1. **A7.h — Low — `validate_subject` accepts non-printable characters.**`) and renumber the remaining items so A7.e, A1.f become 1, 2.

(c) At the END of the "## Closed items (historical reference)" section (after the A7.b bullet), add:
```
- ✅ **Audit finding A7.h — subjects accept non-printable characters** — closed by docs PR [#<N_docs>](https://github.com/gumptionthomas/cancelchain/pull/<N_docs>) (design+plan) and impl PR [#<N_impl>](https://github.com/gumptionthomas/cancelchain/pull/<N_impl>). `validate_subject` / `validate_raw_subject` now require the decoded raw subject to satisfy `str.isprintable()` (via a shared `_valid_raw_subject()` helper) — rejecting control characters, bidi overrides, ZWJ, zero-width and non-ASCII whitespace, private-use, and unassigned codepoints, while allowing letters, marks, numbers, punctuation, symbols/emoji, and plain spaces. No schema change. Brings audit severity to 0 Critical / 0 High / 0 Medium / 2 Low.
```
The `#<N_docs>` / `#<N_impl>` placeholders are filled when the PRs are opened (docs PR number on this branch; impl PR number after it opens), mirroring the A7.b closeout.

> CRITICAL: do NOT modify the severity tallies in the earlier closed-item bullets (A4.c says "4 Low", A7.b says "3 Low") — those are historical records of the state at each finding's close.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md docs/superpowers/ROADMAP.md
git commit -m "docs(a7h): mark A7.h remediated; update audit + ROADMAP counts"
```

---

### Task 3: Final gates

**Files:** none (verification only)

- [ ] **Step 1: Full suite**

Run: `COLUMNS=200 uv run pytest`
Expected: **250 passed, 2 xfailed, 1 skipped** (baseline was 244 passed / 3 xfailed / 1 skipped; this PR adds 5 `test_payload.py` unit tests as passing and moves `test_a7_h…` from xfailed to passed). No unexpectedly-passing xfails. If the baseline differs, the invariant is: +5 net new passing tests, and A7.h moved from xfailed to passed (xfailed drops by exactly 1, leaving A7.e and A1.f).

- [ ] **Step 2: xfail cross-check**

Run: `uv run pytest --runxfail tests/test_verification_audit.py -q`
Expected: A7.h is now in the passing set; only A7.e and A1.f surface as failures under `--runxfail`.

- [ ] **Step 3: Lint + types**

Run:
```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Expected: all clean. (No schema change ⇒ no migration / `db check` impact.)

- [ ] **Step 4: Confirm no migration drift**

Run: `git status --porcelain src/cancelchain/migrations/`
Expected: empty.

---

## Notes for the implementer

- The helper must be `_valid_raw_subject` (underscore-prefixed, module-private) and defined *before* `validate_subject` (which calls it).
- Do NOT change `decode_subject`/`encode_subject`, the length constants, or the `Subject` annotated type — the printability gate lives entirely in the two validators via the helper.
- This is a fix PR: no adjacent refactors, no normalization/trimming/confusables work (explicit non-goals in the spec). The pre-existing `test_create_wallet` terminal-width bug stays out of scope.
- When editing the audit doc, the existing Finding A7.h paragraph contains literal bidi/zero-width characters in its examples — anchor your edits on the plain-ASCII `**Finding A7.h — Severity Low:**` marker and do not disturb those example characters.
