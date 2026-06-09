"""Benchmark the effective mill_hash rate through the production milling
loop and recommend the mainnet MAX_TARGET difficulty floor (#169).

Times milling.mill_work (the exact per-proof production code: header
concat + sha256(sha512) + int compare) with target=0, which no hash can
satisfy, so the full proof range always scans. The floor baseline is the
SINGLE-CORE rate (err-easier: a lone single-process Pi must keep the
chain live); the all-cores rate is reported for information only.

Run on the target Raspberry Pi from the gumptionchain repo root:

    uv run python scripts/benchmark_max_target.py

No DB, .env, or app context needed.
"""

from __future__ import annotations

import argparse
import multiprocessing
import platform
import time

from gumptionchain.chain import TARGET_GOAL_SECONDS
from gumptionchain.milling import (
    expected_attempts,
    mill_work,
    recommended_z,
)

# Mirrors Block.unproven_header: 'idx,timestamp,prev_hash,target,
# merkle_root,version,' (trailing comma; mill_work appends the proof
# integer directly). Only the byte length fed to sha512 per attempt
# matters to the rate, so realistic-length literals suffice.
HEADER = ','.join(
    (
        '1024',
        '2026-06-09T12:00:00.000000',
        'a3' * 32,
        '0' * 4 + 'F' * 60,
        'b4' * 32,
        '1',
        '',
    )
)

CALIBRATION_PROOFS = 20_000


def measure_single(
    seconds: float, calibration: int = CALIBRATION_PROOFS
) -> float:
    """Effective single-core H/s through the production mill_work loop."""
    # Calibrate: estimate the rate from a small fixed chunk (also warms
    # caches/interpreter state).
    t0 = time.perf_counter()
    mill_work((0, calibration, HEADER, 0))
    cal_rate = calibration / (time.perf_counter() - t0)
    # Measure: one chunk sized to ~`seconds` at the calibrated rate.
    n = max(calibration, int(cal_rate * seconds))
    t0 = time.perf_counter()
    mill_work((n, 2 * n, HEADER, 0))
    return n / (time.perf_counter() - t0)


def measure_multi(seconds: float, single_rate: float) -> float:
    """Aggregate all-cores H/s, replicating mill_block_mp's shape
    (Pool(cpu_count()) + imap_unordered over per-cpu chunks)."""
    cpus = multiprocessing.cpu_count()
    n = max(CALIBRATION_PROOFS, int(single_rate * seconds))
    work = [(i * n, (i + 1) * n, HEADER, 0) for i in range(cpus)]
    t0 = time.perf_counter()
    with multiprocessing.Pool(cpus) as pool:
        for _proof, _count in pool.imap_unordered(mill_work, work):
            pass
    return (n * cpus) / (time.perf_counter() - t0)


def _fmt_seconds(s: float) -> str:
    return f'{s:.1f} s' if s < 120 else f'{s / 60:.1f} min'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--seconds',
        type=float,
        default=10.0,
        help='measurement duration per mode (default: 10)',
    )
    parser.add_argument(
        '--skip-multi',
        action='store_true',
        help='skip the informational all-cores measurement',
    )
    parser.add_argument(
        '--goal',
        type=float,
        default=TARGET_GOAL_SECONDS,
        help=f'seconds per block (default: {TARGET_GOAL_SECONDS})',
    )
    args = parser.parse_args()

    cpus = multiprocessing.cpu_count()
    print('=== mill_hash benchmark (#169 MAX_TARGET floor) ===')  # noqa: T201
    print(f'platform : {platform.platform()}')  # noqa: T201
    print(f'machine  : {platform.machine()}')  # noqa: T201
    print(f'python   : {platform.python_version()}')  # noqa: T201
    print(f'cpus     : {cpus}')  # noqa: T201

    single = measure_single(args.seconds)
    print(f'single-core : {single:,.0f} H/s  (floor baseline)')  # noqa: T201
    if not args.skip_multi:
        multi = measure_multi(args.seconds, single)
        print(  # noqa: T201
            f'multi ({cpus}c)  : {multi:,.0f} H/s  (informational)'
        )

    z = recommended_z(rate_hps=single, goal_seconds=args.goal)
    print(f'goal        : {args.goal:.0f} s/block')  # noqa: T201
    print(f'recommended Z = {z}')  # noqa: T201
    for zz in (z, z + 1):
        solve = expected_attempts(zz) / single
        print(  # noqa: T201
            f'  expected single-core solve @ Z={zz}: {_fmt_seconds(solve)}'
        )
    print('paste into src/gumptionchain/chain.py:')  # noqa: T201
    print(f"MAX_TARGET = '0' * {z} + 'F' * {64 - z}")  # noqa: T201


if __name__ == '__main__':
    main()
