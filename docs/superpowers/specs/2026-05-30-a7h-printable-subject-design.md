# A7.h — Printable-Subject Enforcement Design

**Audit finding:** A7.h (Low) — *`validate_subject` / `validate_raw_subject` accept non-printable characters.*
**Source audit:** `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`

---

## Problem

A *subject* is the UTF-8 string (1–79 chars) a token is assigned to. On the
chain it is stored as a URL-safe base64 encoding of the raw string
(`encode_subject`); `decode_subject` recovers the raw string.

`validate_subject` (`src/cancelchain/payload.py`) and `validate_raw_subject`
enforce only two things: the raw length is `1 <= len <= 79`, and the value
round-trips through canonical base64url. They impose **no character-class
restriction**, so a subject can decode to any UTF-8 codepoint — null bytes,
C0/C1 control characters (`ESC`, `BEL`, `DEL`, newline), the bidirectional
override `RLO` (`U+202E`), zero-width joiner (`U+200D`), zero-width space
(`U+200B`), etc.

Subjects propagate to `BalanceView` HTML, CLI `subject` output, and
`wallet_leaderboard` JSON. Any consumer that doesn't strip control bytes
renders deceptively — e.g. a subject decoding to `'\x1b[31mRED'` injects an
ANSI color escape into terminal output. Severity Low (no value-conservation
or chain-correctness impact), but a real input-hygiene gap on a
consensus-validated field.

## Goal

Reject subjects whose decoded raw string contains non-printable characters,
at the validation layer, symmetrically in both `validate_subject` (the
encoded-form gate) and `validate_raw_subject` (the raw-form gate).

## Policy

A raw subject is acceptable iff **`str.isprintable()` is true** (in addition
to the existing length bound).

`str.isprintable()` returns false if any character is in a Unicode "Other"
category (`Cc` control, `Cf` format — includes bidi overrides, ZWJ, and
zero-width characters, `Cs` surrogate, `Co` private-use, `Cn` unassigned) or
any "Separator" category **except** the ASCII space `U+0020` (so tab,
newline, `NBSP`, line/paragraph separators are rejected).

It **allows** letters (`L*`), marks (`M*` — accents and combining marks for
non-Latin scripts), numbers (`N*`), punctuation (`P*`), symbols including
emoji (`S*`), and the plain ASCII space.

This is slightly stricter than the audit's literal sketch (which rejected
only `Cc`/`Cf`/`Cn`/`Cs`): `str.isprintable()` additionally rejects
private-use codepoints and non-ASCII whitespace, both of which can also
render deceptively, and it does so with a single principled stdlib call
rather than a per-character `unicodedata.category()` loop.

Examples:

| Raw subject | `isprintable()` | Result |
|---|---|---|
| `Acme Corp` | true | accepted |
| `café` | true | accepted |
| `🍎` | true | accepted |
| `'\x1b[31mRED'` (ESC + ANSI) | false | rejected |
| `"a" + U+202E + "b"` (RLO bidi override) | false | rejected |
| `"a" + U+200D + "b"` (ZWJ) | false | rejected |
| `"a" + U+200B + "b"` (zero-width space) | false | rejected |
| `"a\tb"` / `"a\nb"` (tab / newline) | false | rejected |

## Approach

Both `validate_subject` and `validate_raw_subject` already duplicate the
`MIN_SUBJECT_LENGTH <= len(raw) <= MAX_SUBJECT_LENGTH` length check.
Consolidate length + printability into one private helper — a single source
of truth for "valid raw-subject content" — rather than inlining
`isprintable()` twice.

### Component (one file: `src/cancelchain/payload.py`)

```python
def _valid_raw_subject(raw_subject: str) -> bool:
    return (
        MIN_SUBJECT_LENGTH <= len(raw_subject) <= MAX_SUBJECT_LENGTH
        and raw_subject.isprintable()
    )
```

- **`validate_subject(subject)`** — after `decode_subject(subject)`, gate on
  `_valid_raw_subject(raw_subject)` before the canonical round-trip check.
- **`validate_raw_subject(raw_subject)`** — gate on
  `_valid_raw_subject(raw_subject)` before its round-trip check.

The functions keep their existing `try/except → return False` shape; the
new helper introduces no new exceptions.

## Where it bites (consensus path)

`validate_subject` backs the `Subject` type
(`Annotated[str, AfterValidator(_check_subject)]`) used on
`OutflowModel.{subject, forgive, support}`, so the check runs during outflow
and transaction validation. A control-character subject is therefore rejected
at `Node.receive_transaction` (the finding's acceptance test) and at block
validation. `validate_raw_subject` covers the API/CLI raw-input path
(`api.py` subject endpoint) symmetrically.

This is consensus-affecting — it changes which transactions are valid — but
safe to tighten: there is no legacy chain to preserve and no deployed
installs, and all existing tests and fixtures construct only printable
subjects, so `Chain.validate()` full-chain revalidation of any existing chain
is unaffected.

## Error handling

Unchanged. `_check_subject` raises `ValueError` (wrapped to
`InvalidTransactionError` upstream) with `f'Invalid subject: {truncate(s)!r}'`.
The `!r` repr escapes any control character in the rejected value, so the
error message itself introduces no rendering vector.

## Testing

- **Acceptance:** un-xfail `test_a7_h_non_printable_subject_accepted` in
  `tests/test_verification_audit.py` — a subject decoding to `'\x1b[31mRED'`
  makes `receive_transaction` raise `InvalidTransactionError`. Removing its
  `@pytest.mark.xfail` makes it a passing regression test.
- **Unit tests** (`tests/test_payload.py`): `validate_subject` and
  `validate_raw_subject` reject ESC (`Cc`), RLO `U+202E` (`Cf`), ZWJ
  `U+200D` (`Cf`), zero-width space `U+200B` (`Cf`), tab, and newline; and
  accept plain ASCII text, text with internal spaces, accented text
  (`café`), and an emoji. A direct test of `_valid_raw_subject` covers the
  length-bound and printability branches.

## Documentation updates

- **Audit doc** (`docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`):
  mark A7.h remediated (status lead-in matching the A4.c / A7.b convention),
  flip its sub-attack Outcome to REJECTED, remove its row from the open
  findings table, update the intro count ("three remain open (A7.h, A7.e,
  A1.f)" → "two remain open (A7.e, A1.f)") and the findings-table count
  ("3 open findings … 3 Low (post-A7.b)" → "2 open findings … 2 Low
  (post-A7.h)"), and update the remediation-priority A7.h entry to an
  `✅ Implemented` status.
- **ROADMAP** (`docs/superpowers/ROADMAP.md`): update the open-findings
  count prose, remove the A7.h numbered item (renumber the rest), and add an
  A7.h entry to the Closed items section (mirroring the A7.b entry), severity
  → **0 Critical / 0 High / 0 Medium / 2 Low**. Do not modify the historical
  severity tallies in earlier closed entries.

## Out of scope (non-goals)

- **Unicode normalization (NFC/NFKC)** — changes subject *identity* (the
  stored bytes), a larger semantic decision than non-printable hygiene.
- **Homoglyph / confusables detection** — a substantial, separate effort
  (e.g. UTS-39 skeletons); not what A7.h is about.
- **Whitespace trimming** — leading/trailing ASCII spaces remain allowed
  (a different, lower-severity class than control characters).

## Acceptance criteria

1. `test_a7_h_non_printable_subject_accepted` passes with its `xfail`
   removed.
2. New `tests/test_payload.py` unit tests pass.
3. Full suite green (`COLUMNS=200 uv run pytest`), `ruff check`/`format`
   clean, `mypy` clean. No migration / schema change.
