# EGU #169 — MAX_TARGET Pi benchmark harness (prep)

**Date:** 2026-06-09
**Issue:** #169 (benchmark and set the mainnet `MAX_TARGET` difficulty floor on real Pi hardware — EGU 1b launch gate). Follows #167/#168; part of #151.
**Status:** design approved
**Scope decision:** Pi hardware is **not yet available** — this spec covers the prep work only: a benchmark harness validated on any machine, so the moment a Pi exists the gate closes with a run-paste-set workflow. Setting the actual constant is a separate, later one-line PR.

## Goal

Build `scripts/benchmark_max_target.py`: measure the **effective** `mill_hash`
rate through the production milling code path, and recommend the largest
`'0'*Z + 'F'*(64-Z)` MAX_TARGET whose expected single-core solve time is
≤ `TARGET_GOAL_SECONDS` (300 s) — rounding Z **down**, the err-easier rule
from the issue.

Decided during brainstorming:
- **Floor baseline = one Pi core** (not `--multi`). If the real lone miner
  runs all 4 cores, blocks come ~4× fast at the floor until retargeting
  corrects — self-correcting, never fatal. Built-in stall protection.
- **Keep the `'0'*Z` shape** (each Z step is 16× harder; floor-rounding the
  log gives the easier side of the step). Readable, matches the current
  constant and the guard test's shape.
- **Measure the real loop, not raw hashlib.** Raw `sha256(sha512())`
  throughput overestimates the rate severalfold (the miner pays Python
  per-proof overhead: f-string header concat, hex parse, int compare) — and
  an overestimated rate pushes Z *up*, the fatal too-hard direction.

## Background (verified in code)

- `MAX_TARGET = '0' * 4 + 'F' * 60` placeholder at `src/gumptionchain/chain.py:58`;
  `TARGET_GOAL_SECONDS = 300` (`chain.py:61`); retarget every
  `TARGET_INTERVAL = 24` blocks.
- The per-proof production work is `milling.mill_work(w)`
  (`src/gumptionchain/milling.py:44-52`): for each proof in
  `[work_start, work_stop)`, `mill_hash_str(f'{unproven_header}{proof}')`
  then `int(h, 16) < target`. Both `mill_block` (single-process) and
  `mill_block_mp` (`multiprocessing.Pool` + `imap_unordered`, chunk
  `worksize=100000`) funnel through it.
- `Block.unproven_header` (`src/gumptionchain/block.py:157`) is
  `','.join((idx, timestamp, prev_hash, target, merkle_root, version, ''))` —
  i.e. `idx,timestamp,prev_hash,target,merkle_root,version,` with a trailing
  comma; the proof integer is appended directly.
- With target `T_Z = int('0'*Z + 'F'*(64-Z), 16) = 16^(64-Z) - 1`, the
  success probability per attempt is `T_Z / 16^64 ≈ 16^-Z`, so **expected
  attempts per solve = 16^Z** exactly (geometric).
- Guard test `tests/test_consensus_constants.py::test_max_target_is_an_easy_placeholder_floor`
  pins `len(MAX_TARGET) == 64` and **strictly easier than the legacy 6-zero
  floor** (`int(MAX_TARGET,16) > int('0'*6+'F'*58, 16)`).

## Part A — pure math in `milling.py`

Two pure functions (no runtime callers — launch tooling that belongs with the
proof-of-work math, normally importable and mypy-strict-checked):

```python
def expected_attempts(z: int) -> int:
    # With target '0'*z + 'F'*(64-z), success probability per attempt is
    # ~16^-z, so the geometric expectation is exactly 16^z attempts.
    return 16**z


def recommended_z(rate_hps: float, goal_seconds: float) -> int:
    # Largest z whose expected solve time 16^z / rate is <= goal.
    # Flooring the log errs EASIER (numerically larger target) - the
    # non-fatal direction (#169): a too-easy floor self-corrects at the
    # first retarget; a too-hard floor stalls genesis. Clamped at 0.
    budget = rate_hps * goal_seconds  # attempts affordable per block
    z = 0
    while expected_attempts(z + 1) <= budget:
        z += 1
    return z
```

(Integer-loop formulation instead of `floor(log(budget, 16))` to avoid float
edge cases exactly at the boundary; z ≤ 64 in practice — the loop is trivial.)

## Part B — the script `scripts/benchmark_max_target.py`

Self-contained (no DB, no app context, no `.env`); follows the
`populate_dev_chain.py` conventions (module docstring with a run line,
`main()`, `# noqa: T201` prints).

- **Synthetic header:** a module constant mirroring the real
  `unproven_header` shape and realistic field lengths — plausible idx, a
  ciso8601-style timestamp, 64-hex `prev_hash`, 64-hex target string, 64-hex
  `merkle_root`, version string, trailing comma. (What matters to the rate is
  the byte length fed to sha512 per attempt; a comment says so.)
- **Measurement = the production function with an impossible target:**
  `mill_work((start, start + n, HEADER, 0))` — `target=0` means no hash can
  ever be `< 0`, so the full range is scanned. Zero duplicated milling logic;
  by construction the measured rate is what `mill_block` achieves.
- **Warmup-then-measure:** a short calibration chunk (~1 s worth, starting
  from a fixed small N and scaling) estimates the rate; the measured run
  then uses a chunk sized to `--seconds` (default 10), timed with
  `time.perf_counter()`. Single-core rate = `n / elapsed`.
- **Informational `--multi` mode** (default on; `--skip-multi` to skip):
  replicates `mill_block_mp`'s shape — `multiprocessing.Pool(cpu_count())`,
  `imap_unordered(mill_work, ...)` over `cpus` chunks — and reports the
  aggregate rate. Not used for the floor; printed so the report shows the
  real-fleet headroom.
- **Flags:** `--seconds` (per-mode measure duration, default 10),
  `--skip-multi`, `--goal` (defaults to `chain.TARGET_GOAL_SECONDS`).
- **Report** (plain prints):
  - platform / machine / python version / CPU count
  - single-core rate (H/s) and multi rate (H/s, if run)
  - recommended `Z = recommended_z(single_core_rate, goal)`
  - expected solve time at Z and at Z+1 (both sides of the 16× step)
  - the paste-ready line: `MAX_TARGET = '0' * Z + 'F' * (64 - Z)`
    rendered with the literal recommended Z value.

## Testing

- New `tests/test_milling.py` (no dedicated milling test module exists today —
  milling is only exercised incidentally via chain/api tests): unit tests for
  the math:
  - `expected_attempts(0) == 1`, `expected_attempts(4) == 65536`
  - `recommended_z` floor behavior: budget exactly `16^z` → `z` (boundary
    inclusive); budget `16^z - 1` → `z - 1`
  - rate so low the budget < 16 → `0` (clamp, no negative)
  - monotonic in rate
- **Smoke test for the script** (fast, CI-safe): import the script via
  `importlib.util.spec_from_file_location` (no `scripts/` package) and run
  its measurement helper with a tiny proof budget (~2 000 proofs, sub-second)
  asserting a positive finite rate; plus `recommended_z` integration on the
  measured number. No assertion on the rate's magnitude (CI hardware varies).
- Hard gates: `uv run ruff format --check src tests && uv run ruff check src
  tests && uv run mypy && uv run pytest`. No schema change (`db check` N/A
  but stays clean trivially).

## Workflow when the Pi arrives (closes the gate)

1. On the Pi: `uv run python scripts/benchmark_max_target.py` → paste report.
2. One-line PR: set `MAX_TARGET` in `chain.py` to the recommended shape.
3. Guard test: if the recommendation lands at **Z ≥ 6**, the placeholder
   assertion (`strictly easier than the 6-zero legacy floor`) no longer
   holds — replace it with a pin of the benchmarked value (the issue
   anticipates this: "update the guard test if it pins anything affected").
   At Z ≤ 5 the existing assertions keep passing.
4. Close #169.

This prep PR posts the run instructions as a comment on #169; the issue
stays open as the launch gate.

## Scope / care

- **No runtime behavior change.** The two `milling.py` functions have no
  runtime callers; the script is launch tooling. Consensus values untouched.
- **Single PR** (`feat/egu-169-max-target-benchmark`): script + math +
  tests + issue comment.

## Out of scope

- Setting the actual `MAX_TARGET` (needs the Pi — later PR).
- Multi-Pi / fleet benchmarking, thermal-throttle profiling (the err-easier
  margin absorbs these).
- A `gumptionchain benchmark` CLI subcommand (rejected: permanent runtime
  surface for a one-shot launch task).
