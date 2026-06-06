# EGU 1b — consensus constant retune — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retune the five chain consensus constants from Bitcoin-scale to friendly-Pi-fleet commodity-scale: 5-min blocks, 2-hr retarget, an easy benchmark-pending Pi difficulty floor, a flat 5 GRIT/block reward, and RSA-2048. No logic change — the retarget math and flat reward are already parameterized.

**Architecture:** Two independent edits — the timing/difficulty/reward constants in `chain.py`, and the RSA `KEY_SIZE` in `wallet.py` — each guarded by a focused test. Greenfield/pre-launch: no migration, no fork coordination. `MAX_TARGET` ships as a deliberately-easy placeholder finalized at mainnet via a documented hardware benchmark.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0, pytest, uv, ruff (line-length 80, single quotes), mypy strict.

**Spec:** `docs/superpowers/specs/2026-06-06-egu-1b-constant-retune-design.md` (issue #167)

---

## File map

| File | Change |
|---|---|
| `src/gumptionchain/chain.py` | Set `MAX_TARGET` (easy Pi placeholder + benchmark comment), `REWARD = 5 * GRAIN_PER_GRIT`, `TARGET_GOAL_SECONDS = 300`, `TARGET_INTERVAL = 24`. (`TARGET_INTERVAL_SECONDS` auto-derives to 7200.) |
| `src/gumptionchain/wallet.py` | `KEY_SIZE = 2048`. |
| `tests/test_chain.py` (or new `tests/test_consensus_constants.py`) | Guard test pinning the new settled constant values. |
| `tests/test_command.py`, `tests/test_miller.py` | Resize reward-dependent stake amounts **only if** the suite shows them going insufficient (likely not — see below). |
| `tests/test_wallet_audit.py` | Update the historical "3072" references in `test_wc2_import_rejects_degenerate_exponent`'s docstring to 2048. |

No schema change → `db check` unaffected. No migration.

---

## Background the implementer needs

Current constants (`src/gumptionchain/chain.py:43-49`):
```python
GRAIN_PER_GRIT = 100
GENESIS_HASH = mill_hash_str('GENESIS')
MAX_TARGET = '0' * 6 + 'F' * 58
REWARD = 100 * GRAIN_PER_GRIT
TARGET_GOAL_SECONDS = 600
TARGET_INTERVAL = 2016
TARGET_INTERVAL_SECONDS = TARGET_GOAL_SECONDS * TARGET_INTERVAL
```
and `src/gumptionchain/wallet.py:25`: `KEY_SIZE = 3072`.

Test-coupling facts (verified):
- Retarget tests (`test_chain.py:125,148,171`) `@patch('gumptionchain.chain.TARGET_INTERVAL', 5)`, so 2016→24 doesn't affect them.
- `MAX_TARGET` is patched to `'F' * 64` by `easy_mill_chain` (conftest), so its production value is isolated from CI.
- `REWARD` is symbolic in tests. The reward-dependent arithmetic **likely survives** the 100→5 GRIT drop because the test stakes are small: `test_command.py` uses `SUBJECT_GRIT = 2` (and asserts `REWARD_GRIT - 2*SUBJECT_GRIT` = `5 - 4 = 1`, still ≥ 0), and `test_miller.py` remits `2 * GRAIN_PER_GRIT = 200` grains against a `5 * GRAIN_PER_GRIT = 500`-grain coinbase. **Do not preemptively edit these** — run the suite and fix only what actually fails.
- `test_wallet_audit.py` weak-key test generates `key_size=KEY_SIZE`, so it follows to 2048; only its docstring names "3072".

---

## Task 1: Retune the chain timing/difficulty/reward constants

**Files:**
- Modify: `src/gumptionchain/chain.py`
- Test: `tests/test_consensus_constants.py` (new)

- [ ] **Step 1: Write the guard test (failing)**

Create `tests/test_consensus_constants.py`. It pins the settled values so accidental drift is caught, and confirms `TARGET_INTERVAL_SECONDS` derives correctly. It deliberately does **not** pin `MAX_TARGET` (that value is finalized at mainnet deploy via benchmark) — it only asserts the floor is "easy enough" to not be absurdly hard.

```python
from gumptionchain.chain import (
    GRAIN_PER_GRIT,
    MAX_TARGET,
    REWARD,
    TARGET_GOAL_SECONDS,
    TARGET_INTERVAL,
    TARGET_INTERVAL_SECONDS,
)


def test_egu_1b_consensus_constants():
    # 5-minute blocks (game-cadence UX, not throughput).
    assert TARGET_GOAL_SECONDS == 300
    # 2-hour retarget window for a small, volatile Pi fleet.
    assert TARGET_INTERVAL == 24
    assert TARGET_INTERVAL_SECONDS == 7200  # auto-derived: 300 * 24
    # Flat 5 GRIT/block base reward (loose-for-leakage, non-halving).
    assert REWARD == 5 * GRAIN_PER_GRIT
    assert REWARD == 500


def test_max_target_is_an_easy_placeholder_floor():
    # MAX_TARGET is the difficulty FLOOR (easiest target); the production
    # value is benchmark-tuned at mainnet deploy. It must be a 64-hex-digit
    # string and STRICTLY easier (numerically larger) than the legacy 6-zero
    # floor so a lone Pi can start the chain before the first retarget.
    assert len(MAX_TARGET) == 64
    legacy_floor = int('0' * 6 + 'F' * 58, 16)
    assert int(MAX_TARGET, 16) > legacy_floor
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_consensus_constants.py -q`
Expected: FAIL — current values are 600 / 2016 / 10000, and `MAX_TARGET` (6 zeros) equals the legacy floor, so the strict `>` placeholder check also fails until it is eased.

- [ ] **Step 3: Edit the constants**

In `src/gumptionchain/chain.py`, change lines 45-48 to:
```python
MAX_TARGET = '0' * 4 + 'F' * 60  # easy Pi-fleet floor — BENCHMARK before mainnet
REWARD = 5 * GRAIN_PER_GRIT
TARGET_GOAL_SECONDS = 300
TARGET_INTERVAL = 24
```
Leave `GRAIN_PER_GRIT`, `GENESIS_HASH`, and the `TARGET_INTERVAL_SECONDS = TARGET_GOAL_SECONDS * TARGET_INTERVAL` derivation line unchanged. Add a short comment above `MAX_TARGET` noting it is the benchmark-pending difficulty floor (set on real Pi `sha256(sha512)` hashrate so a lone Pi finds a block in ≤300s; err easier — too-easy is a self-correcting fast-start, too-hard stalls genesis).

- [ ] **Step 4: Run the guard test, expect PASS**

Run: `uv run pytest tests/test_consensus_constants.py -q`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full suite; triage reward-dependent failures**

Run: `uv run pytest -q`
Expected: green. The retarget tests are insulated (own patch), `MAX_TARGET` is insulated (`easy_mill_chain`), and the reward arithmetic should survive (stakes ≤ 5 GRIT). **If** any test fails with an insufficient-balance / negative-amount error from the smaller reward (most likely in `test_command.py` or `test_miller.py`), fix it by reducing the offending stake/remit amount or minting an extra block before the spend — preserving the test's original intent (the assertion's *structure*, just sized to fit a 5-GRIT reward). Do NOT weaken an assertion to mask a real balance bug. Report which tests (if any) needed resizing.

- [ ] **Step 6: Lint, format, types**

Run: `uv run ruff format src tests && uv run ruff check src tests && uv run mypy`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/gumptionchain/chain.py tests/test_consensus_constants.py tests/test_command.py tests/test_miller.py
git commit -m "$(cat <<'EOF'
feat(chain): EGU 1b retune — 5-min blocks, 2-hr retarget, easy Pi floor, 5 GRIT reward (#167)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```
(Only `git add` the test_command/test_miller files if Step 5 actually edited them.)

---

## Task 2: RSA key size 3072 → 2048

**Files:**
- Modify: `src/gumptionchain/wallet.py`
- Test: `tests/test_wallet.py` (or wherever wallet keygen is tested) + `tests/test_wallet_audit.py` (docstring)

- [ ] **Step 1: Write the key-size test (failing)**

Add a test asserting a freshly generated wallet's key is 2048-bit and that it signs+verifies end-to-end. Place it with the existing wallet tests (find the module that imports `Wallet` and tests signing — likely `tests/test_wallet.py`; if none, add to `tests/test_wallet_audit.py`). Confirm the actual `Wallet` API for generating + signing by reading an existing wallet test first; adapt the calls below to match.

```python
def test_wallet_key_size_is_2048():
    from gumptionchain.wallet import KEY_SIZE, Wallet

    assert KEY_SIZE == 2048
    w = Wallet()
    # The generated private key must be 2048-bit.
    assert w.private_key.key_size == 2048
    # Round-trip: a signature over a message verifies.
    message = 'egu-1b'
    signature = w.sign(message)
    assert w.verify(message, signature) is True
```
Adapt `w.private_key`, `w.sign`, `w.verify` to the real `Wallet` interface (read an existing test). The load-bearing assertions are `KEY_SIZE == 2048`, the generated key is 2048-bit, and a sign/verify round-trip succeeds at the new size.

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_wallet.py::test_wallet_key_size_is_2048 -q` (adjust path)
Expected: FAIL — `KEY_SIZE` is 3072, so `assert KEY_SIZE == 2048` (and the 2048-bit key assertion) fail.

- [ ] **Step 3: Edit `KEY_SIZE`**

In `src/gumptionchain/wallet.py:25`, change `KEY_SIZE = 3072` to `KEY_SIZE = 2048`. Leave `PUBLIC_EXPONENT = 65537` and the `key.key_size != KEY_SIZE` validation check unchanged (it follows `KEY_SIZE`).

- [ ] **Step 4: Run the key-size test + full suite, expect PASS**

Run: `uv run pytest tests/test_wallet.py::test_wallet_key_size_is_2048 -q` → PASS
Run: `uv run pytest -q` → green. The signing/verification/auth tests now exercise 2048-bit keys end-to-end; `public_key`/`signature` `String(700)` columns still fit (2048 keys/sigs are smaller than 3072). The suite should also run *slightly faster* (2048 keygen is cheaper than 3072), since test wallets are generated at runtime.

- [ ] **Step 5: Update the historical "3072" docstring**

In `tests/test_wallet_audit.py`, the `test_wc2_import_rejects_degenerate_exponent` docstring describes the WC2 scenario in terms of "3072" (the then-current key size: "`key_size == 3072`" and "a 3072-bit `e=3` key"). The test itself generates `key_size=KEY_SIZE` (now 2048), so update the two "3072" mentions in the docstring to "2048" so the narrative matches the current key profile. Grep for any other "3072" in `tests/` or `src/` and update stale references (do NOT change `PUBLIC_EXPONENT`/65537).

Run: `grep -rn "3072" src tests` → expected: no remaining references after the update.

- [ ] **Step 6: Lint, format, types, db check**

Run: `uv run ruff format src tests && uv run ruff check src tests && uv run mypy` → all green.
db check (no schema change, confirm): `FLASK_SQLALCHEMY_DATABASE_URI=sqlite:////tmp/_dbck.db uv run gumptionchain db upgrade && FLASK_SQLALCHEMY_DATABASE_URI=sqlite:////tmp/_dbck.db uv run gumptionchain db check && rm -f /tmp/_dbck.db` → no drift.

- [ ] **Step 7: Commit**

```bash
git add src/gumptionchain/wallet.py tests/
git commit -m "$(cat <<'EOF'
feat(wallet): EGU 1b — RSA key size 3072 -> 2048 for browser-wallet friendliness (#167)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** all five constants retuned (Tasks 1-2); `TARGET_INTERVAL_SECONDS` auto-derives; guard test pins the settled values; `MAX_TARGET` shipped as an easy commented placeholder (benchmark deferred to mainnet, documented); reward-arithmetic handled by suite-run-then-fix; "3072" references updated; RSA round-trip verified.
- **No logic change:** only constant values, a new guard test, a key-size test, optional reward-arithmetic resizing, and a docstring edit.
- **Consensus safety:** values stay shared code constants; no per-node env. Greenfield → no migration; `db check` confirms no schema drift.
- **MAX_TARGET placeholder:** `'0'*4 + 'F'*60` is easier than the legacy 6-zero floor (self-correcting fast-start on dev/testnet); CI is insulated by `easy_mill_chain`; mainnet value is benchmark-set.

## Definition of done

- `chain.py` constants = 300 / 24 / `5*GRAIN_PER_GRIT` / easy `MAX_TARGET` placeholder; `TARGET_INTERVAL_SECONDS` derives to 7200.
- `wallet.py` `KEY_SIZE = 2048`; wallets generate/sign/verify at 2048; columns fit; no "3072" left in the tree.
- Guard test + key-size test pass; any reward-dependent arithmetic that broke is resized (intent preserved) — reported.
- Full suite + ruff + ruff-format + mypy green; `db check` no drift.
- `MAX_TARGET` mainnet value left as a documented benchmark step, not finalized in this PR.
