# EGU 1b — consensus constant retune — design

**Date:** 2026-06-06
**Status:** Approved design, pre-implementation
**Issue:** #167 (EGU #1, part of #151)
**Type:** Consensus parameter retune — **greenfield/pre-launch**, so no migration and no fork coordination; values finalized before mainnet genesis. No logic change (the retarget math and flat reward are already parameterized by the constants).

## Summary

Retune the five chain constants from Bitcoin-scale to **friendly Pi-fleet
commodity-scale**. The EGU #1 design philosophy is settled in #151 (flat
non-halving reward, friendly permissioned millers, net-flat supply via
stake-burn/sentiment-mint recycling, Model C wallets); this task sets the
**numeric values** and ships them. Because the project is pre-launch (greenfield,
no production chain), every value is hard-forkable now — the only "compatibility"
concern is choosing good launch values, not migrating an existing chain.

## Constants (current → 1b)

| Constant | File | Current | 1b |
|---|---|---|---|
| `TARGET_GOAL_SECONDS` (block time) | `chain.py:47` | 600 (10 min) | **300** (5 min) |
| `TARGET_INTERVAL` (retarget every N) | `chain.py:48` | 2016 | **24** |
| `TARGET_INTERVAL_SECONDS` (derived) | `chain.py:49` | 600×2016 | 300×24 = 7200 (auto) |
| `MAX_TARGET` (difficulty floor) | `chain.py:45` | `'0'*6 + 'F'*58` | **benchmark-tuned Pi floor** (placeholder) |
| `REWARD` (flat base reward) | `chain.py:46` | `100 * GRAIN_PER_GRIT` (10000 grains = 100 GRIT) | **`5 * GRAIN_PER_GRIT`** (500 grains = 5 GRIT) |
| `KEY_SIZE` (RSA) | `wallet.py:25` | 3072 | **2048** |

`TARGET_INTERVAL_SECONDS = TARGET_GOAL_SECONDS * TARGET_INTERVAL` is computed, so
it auto-derives to 7200 from the two edits above — do not hardcode it.

## Rationale (per constant)

- **5-min blocks** — on a lightly-transacted chain, block rate buys *only*
  confirmation-latency UX (throughput is a non-reason; emission is independently
  tunable via `REWARD`). 5 min fits the 2b2f cadence (a game ≈ 10 min, players
  play multiple back-to-back; 10 min "felt too long"). Only 2× the rate, so the
  chain-growth / sync-depth (#163) / fork-row (#164) pressure barely moves.
- **2-hr retarget (`TARGET_INTERVAL` = 24)** — a small, *volatile* Pi fleet
  (miners reboot/join/leave) needs difficulty to react in hours, not Bitcoin's
  2 weeks, or blocks stall when the fleet shrinks. 24 blocks is a large enough
  sample to not overreact to single-block timing noise; the ÷4/×4 clamp in
  `block_target` bounds any one adjustment.
- **`MAX_TARGET` = Pi difficulty floor** — the floor is the *easiest* allowed
  difficulty (largest target). Set it near a single Pi's 5-min `sha256(sha512)`
  capability so a lone Pi sustains cadence and retargeting raises difficulty as
  the fleet grows. See the "MAX_TARGET handling" section — it is benchmark-tuned;
  this spec ships an easy placeholder and the plan benchmarks it.
- **5 GRIT/block flat** — the base reward is the only *net-new* grit (the
  sentiment-mint recycle loop, #145, handles steady-state faucet funding), so it
  is the long-run float-growth knob + leakage buffer. ~1,440 grit/day at 288
  blocks/day — deliberately loose for significant anticipated leakage and rising
  usage (75 game-days and climbing), while ~10× below the inherited "comical"
  rate. Flat/non-halving (grit shouldn't appreciate, per #151).
- **RSA 2048** — browser-wallet friendliness (EGU #2). Smaller keys/sigs than
  3072 still fit the existing `String(700)` `public_key`/`signature` columns
  (no schema change); test wallets regenerate fresh, so the suite gets *faster*.

## Why no logic change

`Chain.block_target` (`chain.py:129-158`) is fully parameterized by
`TARGET_INTERVAL`, `TARGET_INTERVAL_SECONDS`, and `MAX_TARGET` — the ÷4/×4 clamp
and floor cap reference the constants directly. `Chain.block_reward`
(`chain.py:160-161`) returns the flat `REWARD`. So the retune is purely editing
values; the algorithms are unchanged.

## MAX_TARGET handling (the one benchmark-pending value)

`MAX_TARGET` is the difficulty **floor** and the genesis/initial target. It only
*binds* when the fleet is at its slowest (a lone Pi); above that, retargeting
drives difficulty to whatever achieves 300s/block for the actual fleet hashrate,
independent of the floor. The goal (#151) is "target a Pi, not an ASIC": the
floor should ≈ the difficulty a single Pi clears in ~300s.

This cannot be finalized pre-deployment without benchmarking `sha256(sha512)` on
the real hardware. Therefore:

- The spec ships a **deliberately-easy placeholder** so a dev/testnet runs
  (exact placeholder chosen in the plan; keep the `'0'*Z + 'F'*(64-Z)` shape).
- **Err easier, never harder.** Too-easy floor → a brief fast-start burst of
  trivial blocks that retargeting corrects within a few ÷4 steps (≈ a few
  `TARGET_INTERVAL`s). Too-hard floor → genesis-era blocks stall before the first
  retarget at block 24 (the chain can't start). Easy is self-correcting; hard is
  fatal.
- The plan adds a **benchmark step**: measure a target Pi's `sha256(sha512)`
  hashrate, set the mainnet floor so a lone Pi finds a block in ≤300s, recorded
  as the deploy-time value. CI is unaffected — tests patch `MAX_TARGET` to all-F
  via `easy_mill_chain`, so the production floor value is isolated from the suite.

## Test coupling

- **Retarget tests** (`test_chain.py:125,148,171`) already `@patch(
  'gumptionchain.chain.TARGET_INTERVAL', 5)`, so the 2016→24 change does not
  touch them.
- **`MAX_TARGET`** is patched to `'F'*64` by `easy_mill_chain` (conftest), so the
  production floor change is isolated from CI.
- **`REWARD` is symbolic in tests** (`== REWARD`, `2 * REWARD`,
  `REWARD_GRIT = REWARD / GRAIN_PER_GRIT`) → most auto-follow to 500 grains.
  **Exception — reward-dependent arithmetic:** tests that mint `REWARD` then
  spend/stake a *fixed* amount shrink the spendable balance 20× and may go
  insufficient/negative. Known sites: `test_command.py` (`SUBJECT_GRIT` and the
  `REWARD_GRIT - n*SUBJECT_GRIT` assertions) and `test_miller.py` (the
  `(3 * REWARD) - <stake>` balance assertion). The plan resizes those stake/
  subject amounts to fit a 5-GRIT minted balance, preserving each test's intent.
- **RSA 2048** — `wallet.py` keygen + the `key.key_size != KEY_SIZE` validation
  check auto-follow; `test_wallet_audit.py` has a comment/docstring referencing
  "3072" to update (its weak-key generation uses `KEY_SIZE`, so it follows). No
  `.pem` fixtures are committed (test wallets are generated at runtime).

## File-by-file changes

| File | Change |
|---|---|
| `src/gumptionchain/chain.py` | Set `MAX_TARGET` (easy Pi placeholder), `REWARD = 5 * GRAIN_PER_GRIT`, `TARGET_GOAL_SECONDS = 300`, `TARGET_INTERVAL = 24`. (`TARGET_INTERVAL_SECONDS` auto-derives.) |
| `src/gumptionchain/wallet.py` | `KEY_SIZE = 2048`. |
| `tests/test_command.py`, `tests/test_miller.py` | Resize reward-dependent stake/subject amounts to fit a 5-GRIT reward. |
| `tests/test_wallet_audit.py` (+ any other) | Update "3072" comment/docstring references to 2048. |
| `tests/` (optional) | A consensus-constants guard test pinning the 1b values so an accidental drift is caught. |

No schema change → `db check` unaffected. No migration.

## Testing

1. **Full suite green** after resizing the reward-dependent arithmetic — the
   symbolic `REWARD` tests follow; the retarget tests are insulated by their own
   patch; `easy_mill_chain` insulates `MAX_TARGET`.
2. **RSA 2048 round-trip** — confirm a wallet generates a 2048-bit key, the
   `key.key_size != KEY_SIZE` validation accepts it, and a signed request verifies
   (the existing wallet/signing tests cover this once `KEY_SIZE` flips; add an
   explicit `key_size == 2048` assertion if not already implied).
3. **Retarget sanity at the new constants** — a test exercising `block_target`
   under the *production* `TARGET_INTERVAL`/`TARGET_INTERVAL_SECONDS` (not the
   patched-5 path) to confirm the 2-hr window produces sane targets and the ÷4/×4
   clamp + `MAX_TARGET` cap behave. (The easy-mill tests already cover the floor;
   this confirms the retarget interval value.)
4. **Constants guard (optional)** — assert the five values, so a future
   accidental edit is caught at test time.
5. ruff + ruff-format + mypy green; `db check` no drift (no schema change).

## Out of scope

- **Final mainnet `MAX_TARGET`** — set at deploy from the Pi benchmark; the spec/
  plan ship only an easy testnet placeholder.
- **Submit-PoW anti-spam** — specced-but-deferred in #151 (YAGNI until abuse).
- **#163 / #164 / #165** — deferred perf items; none gates 1b.
- **EGU #2–#5** (#152–#155) — browser wallet, identity, 2b2f integration, hub.

## Decisions log

- **Block time 300s**, chosen by 2b2f game cadence/UX, not throughput — block
  time = the slowest value that still feels responsive (faster is pure overhead
  on a light chain). Only 2× the rate.
- **`TARGET_INTERVAL` 24** (2-hr window) — fast difficulty reaction for a small
  volatile fleet without single-block-noise overreaction.
- **`REWARD` 5 GRIT/block flat** — net-new money / float-growth knob sized loose
  for leakage + growth, ~10× below the inherited rate; recycle loop carries
  steady-state funding.
- **`MAX_TARGET`** benchmark-tuned Pi floor; easy placeholder ships, err easier
  (self-correcting) never harder (genesis stall).
- **RSA 2048** for browser-wallet friendliness; fits existing columns, no schema
  change.
- Greenfield → no migration, no fork coordination; consensus constants stay
  shared code constants (never per-node `GC_*` env — a node with different values
  forks the chain).

## Definition of done

- The five constants set to the 1b values in `chain.py` / `wallet.py`;
  `TARGET_INTERVAL_SECONDS` auto-derives to 7200.
- Reward-dependent test arithmetic resized; "3072" references updated; (optional)
  constants guard test added.
- Full suite + ruff + ruff-format + mypy green; `db check` no drift.
- RSA wallets generate/validate/sign at 2048.
- `MAX_TARGET` left as an easy, clearly-commented placeholder with a documented
  benchmark step for the mainnet value (not finalized in this PR).
