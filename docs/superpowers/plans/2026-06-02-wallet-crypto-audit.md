# Wallet & Cryptographic-Primitives Threat-Modeled Audit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended for this plan — see note) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a threat-modeled security audit of the cancelchain cryptographic root of trust — a report at `docs/superpowers/audits/2026-06-02-wallet-crypto-audit.md` plus strict-xfail demonstration tests in `tests/test_wallet_audit.py` — driven by a three-phase multi-agent Workflow fan-out.

**Architecture:** A Workflow fans out one analyst agent per adversary category over the in-scope code (discover), adversarially refutes each candidate finding (verify), and synthesizes survivors into a report-ready finding set. The controller then writes the report and one `@pytest.mark.xfail(strict=True)` demonstration per finding (hygiene/dead-code findings use a bounded-state-assertion convention that never attempts real cryptanalysis). The audit ships as a docs PR; remediation of each finding is separate downstream work.

**Tech Stack:** Python 3.12+, pytest, `@pytest.mark.xfail(strict=True)`, the `cryptography` library (for constructing adversarial keys in-test), `tests/conftest.py` 3072-bit wallet fixtures, the Workflow multi-agent tool.

> **Execution note — run inline, not subagent-driven.** The audit's findings are synthesized by the Workflow (invoked by the controller, which holds the user's **explicit opt-in** — required to run a Workflow) and the report+tests are written directly from that in-context output. Fresh per-task subagents would have to be re-fed the entire finding set, defeating the point. Recommend **inline execution (executing-plans)**: the controller runs Task 2's Workflow and writes up Tasks 3–4 from its result. Tasks remain individually committable.

**Authoritative design:** `docs/superpowers/specs/2026-06-02-wallet-crypto-audit-design.md`. Read it first — scope razor, adversary categories, severity rubric, and the bounded-state-assertion test convention live there.

---

## File structure

| File | Responsibility | Created/Modified |
|---|---|---|
| `tests/test_wallet_audit.py` | One strict-xfail demonstration per finding + shared adversarial-key helpers | Create (Task 1 scaffold, Task 4 fill) |
| `docs/superpowers/audits/2026-06-02-wallet-crypto-audit.md` | The audit report (exec summary, traces, findings table, strengths, recommendations) | Create (Task 3) |
| `docs/superpowers/ROADMAP.md` | Add "Audit remediation — wallet/crypto findings" tracking entry | Modify (Task 5) |
| `/tmp/wallet-crypto-audit-findings.json` *(scratch, not committed)* | Workflow's synthesized finding set, consumed by Tasks 3–4 | Transient (Task 2) |

In-scope source (read-only for the audit — no source edits in this plan; remediation is downstream):
`src/cancelchain/wallet.py` (the whole module), `src/cancelchain/signing.py` (`_canonical`, `sign_headers`, `verify`), `src/cancelchain/schema.py` (`validate_address`, `validate_public_key`, `validate_signature`, `validate_address_format`, `validate_base64`), and `src/cancelchain/milling.py` (`mill_hash_bin` only, as the address-derivation hash).

---

## Task 1: Branch + test scaffold + baseline

**Files:** Create `tests/test_wallet_audit.py`

- [ ] **Step 1: Branch off main**

```bash
cd /home/gumptionthomas/Development/cancelchain
git checkout main && git pull --ff-only
git checkout -b audit/wallet-crypto
uv run pytest -q 2>&1 | tail -1   # baseline — record the count (e.g. "295 passed, 1 skipped")
```
If the suite is not green, STOP and report.

- [ ] **Step 2: Create the test scaffold**

Create `tests/test_wallet_audit.py` with the module docstring, imports, and shared adversarial-key helpers (e.g. `make_rsa_key(bits, e)` returning a b64 SubjectPublicKeyInfo for a non-standard exponent/size; `make_non_rsa_key()` for an EC/Ed25519 key). Per-finding tests (Task 4) are appended below it. Keep helpers deterministic and fast — no key brute force.

```python
"""Demonstration tests for the 2026-06-02 wallet/crypto threat-model audit.

Each test below demonstrates one audit finding and is marked
``@pytest.mark.xfail(strict=True)`` — strict mode means the test MUST fail
today (the gap is real) and forces the marker's removal when the finding is
remediated. See docs/superpowers/audits/2026-06-02-wallet-crypto-audit.md.

Hygiene / dead-code / unpinned-default findings use a *bounded-state-
assertion* convention: assert the present, observable state deterministically
(an import-graph fact, a serialization marker). No test attempts real key
cracking, brute force, or RSA/AES cryptanalysis.
"""

import pytest

from cancelchain.wallet import Wallet, b64encode

# 3072-bit wallet fixtures come from tests/conftest.py.
```

- [ ] **Step 3: Commit the scaffold** (`test: scaffold wallet/crypto audit demonstration tests`).

---

## Task 2: Run the audit Workflow (discover → verify → synthesize)

> Requires the user's explicit Workflow opt-in. The controller invokes a Workflow that mirrors the prior three audits.

- [ ] **Step 1:** Author and run a Workflow with three phases:
  - **Discover:** six analyst agents (one per adversary category in the design's §"Adversary categories"), each handed the in-scope file list, the scope razor, and its lens. Each returns structured candidate findings: `{id, adversary, attack, code_path (file:line), precondition, impact, proposed_severity}`.
  - **Verify:** for each candidate, ≥3 independent refuter agents try to disprove it against the trusted-boundary controls (pyca loader guarantees, the `key_size == 3072` check, the `verify` address self-certification, the `validate_base64` canonicalization round-trip, fail-closed validators, the permissioned `address = hash(pubkey)` model). A finding survives only if a majority fail to refute.
  - **Synthesize:** dedupe survivors, assign final severities per the rubric, and emit the finding set (plus a list of confirmed *strengths* to record).
- [ ] **Step 2:** Persist the synthesized finding set to `/tmp/wallet-crypto-audit-findings.json` (scratch; not committed). Each entry carries enough to write both the report row and the demonstration test.

**Pre-seeded candidates to ensure the fan-out covers** (the Workflow must confirm or refute each, not assume it):
- Dead `encrypt`/`decrypt` hybrid — zero `src/` callers post-PR #111 (and `encrypt` needs no private key; GCM nonce reuse risk is *deferred by deadness*, not proven sound).
- Imported-key parameter validation — degenerate public exponent (`e=1`/`e=3`/even `e`) / oversized modulus on `import_key`.
- Unpinned KDF behind `BestAvailableEncryption` for exported encrypted wallets.
- `decrypt` framing length-confusion (folds into dead-code if removal chosen).
- `validate_signature` broad-`except` false-`True` reachability; PKCS1v15 soundness.
- `validate_address_format` (structure) vs `validate_address` (key-backed) gap.
- **`schema.validate_signature` key↔address binding** — does every signature-validation caller also enforce the key backs the claimed address? (Assign to category 3; this audit owns the seam — both adjacent audits are closed.)
- **`Wallet.from_dict`/`from_json` with a missing `private_key` field → silent fresh-key generation** (not an error).
- **Wire base64 alignment** — `validate_base64` / `validate_signature` use standard (not URL-safe) b64; confirm URL-safe input is rejected fail-closed.
- Private-key exposure via `__repr__` / logging / `to_dict`/`to_json`.

**Refuter ground rules (for the Verify phase — prevents false refutations):**
- **pyca does NOT enforce a minimum public exponent.** `load_*_key` accepts `e=1`/`e=3`. The only post-load validation is `isinstance(key, RSA*)` + `key.key_size == KEY_SIZE` (`wallet.py:130-133`). Refuters must NOT treat "the loader accepted it" as proof of exponent safety.
- A degenerate-key finding survives if the key is **third-party-forgeable** (see design rubric exclusion 1) — do not kill it as "self-harm."
- `signing.verify`'s `isinstance`-before-`key_size` ordering and the `address == wallet.address` self-cert are real controls; confirm them rather than assuming.

---

## Task 3: Write the audit report

- [ ] Create `docs/superpowers/audits/2026-06-02-wallet-crypto-audit.md` from the synthesized set, structured like the prior three audits:

```markdown
# Wallet & Cryptographic-Primitives Threat-Modeled Audit

## Executive summary    (N Critical / N High / N Medium / N Low headline)
## Findings table        (id, adversary, severity, description, status, demonstration test)
## Adversary traces      (per category: attempts made, what was traced, what survived)
## Cross-cutting observations   (incl. confirmed STRENGTHS — address self-cert, base64
                                 canonicalization, OAEP encryption, fail-closed validators,
                                 key-size enforcement)
## Recommendations       (per finding: targeted fix vs. removal vs. document-and-accept)
```

- [ ] Commit (`docs(audit): wallet/crypto threat-model audit report`).

---

## Task 4: Write the demonstration tests

- [ ] Append one `@pytest.mark.xfail(strict=True)` test per finding to `tests/test_wallet_audit.py`:
  - **Correctness findings:** assert the buggy behavior under xfail (e.g. `Wallet(b64ks=degenerate_e_key)` constructs and `validate_signature` accepts a forged sig; or `validate_signature` returns `True` on a malleated/non-canonical input). Flips to a passing regression on remediation.
  - **Hygiene / dead-code / unpinned-default findings:** assert the present observable state deterministically. No real cracking. Make the assertion robust, not fragile:
    - *Dead-code reachability:* parse each `src/cancelchain/*.py` with `ast` and walk for `Attribute` nodes named `encrypt`/`decrypt` (or `import`/`importlib` graph analysis) — **not** a `grep`/`subprocess` shell-out (PATH- and CWD-fragile, matches comments/strings). Assert zero in-`src` call sites.
    - *Unpinned KDF:* export an encrypted PEM and assert against a **named, explicit byte/OID marker** of the current pyca default (document which OID, e.g. the PBKDF2/scrypt AlgorithmIdentifier), so the test states precisely what "unpinned default" means and won't pass vacuously or break opaquely if pyca changes its default — if it does, the test should fail loudly and be re-pinned.
- [ ] Run `uv run pytest tests/test_wallet_audit.py -q` — every test must `xfail` (strict). A test that *passes* means the gap isn't real → drop or re-classify the finding.
- [ ] Run the full suite, ruff, and mypy. Commit (`test: wallet/crypto audit demonstration tests (strict xfail per finding)`).

---

## Task 5: Roadmap entry + open the docs PR

- [ ] Add an "Audit remediation — wallet/crypto findings" entry to `docs/superpowers/ROADMAP.md` listing each open finding (id, severity, one-line) with its demonstration test, headed by the `N/N/N/N` count.
- [ ] Open the docs PR (`docs(audit): wallet/crypto threat-model audit — N/N/N/N + demonstration tests`). Run it through the internal cross-model review loop to convergence, then exactly one Copilot backstop. `mwg` once green.

---

## Self-review checklist (controller, before execution)

- [ ] Every candidate finding is classifiable via the scope razor (in-scope / cross-reference-only / out-of-scope) — no auth-layer or verification-layer finding claimed as new.
- [ ] Each surviving finding survived adversarial refutation against the listed trusted-boundary controls.
- [ ] "Self-owned weak wallet" candidates are killed unless they cross an address boundary (forge for / impersonate a *different* address) in the permissioned model.
- [ ] Every finding has a deterministic strict-xfail demonstration that performs **no** real key cracking or cryptanalysis.
- [ ] Confirmed strengths are recorded in the report's cross-cutting section (the audit documents what is sound, not only what is broken) — incl. the `base58check` checksum on address decode.
- [ ] Category 6 has grepped call sites for `Wallet` used as a dict key / set member (`__hash__ = None` makes that a `TypeError`); record as a non-finding if no such site exists.
- [ ] Full suite + ruff + mypy green before the PR.
