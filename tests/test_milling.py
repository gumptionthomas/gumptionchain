"""Unit tests for the proof-of-work target math (#169).

expected_attempts / recommended_z back the MAX_TARGET benchmark harness
(scripts/benchmark_max_target.py); they have no runtime callers.
"""

import importlib.util
from pathlib import Path

from gumptionchain.milling import expected_attempts, recommended_z

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / 'scripts'
    / 'benchmark_max_target.py'
)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        'benchmark_max_target', _SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_expected_attempts_powers_of_16():
    # With target '0'*z + 'F'*(64-z), success probability per attempt is
    # ~16^-z, so the geometric expectation is exactly 16^z.
    assert expected_attempts(0) == 1
    assert expected_attempts(1) == 16
    assert expected_attempts(4) == 65536


def test_recommended_z_boundary_inclusive():
    # A budget of exactly 16^3 attempts affords z=3 (expected solve time
    # == goal counts as "within goal"). goal_seconds=1 keeps the
    # rate*goal product exact in float.
    assert recommended_z(rate_hps=16**3, goal_seconds=1) == 3


def test_recommended_z_floors_down():
    # One attempt short of 16^3 -> z=2: rounding down is the err-easier
    # rule (#169) - never recommend a floor the measured rate can't
    # clear within the goal.
    assert recommended_z(rate_hps=16**3 - 1, goal_seconds=1) == 2


def test_recommended_z_clamps_at_zero():
    # A rate too slow for even 16 attempts per block still yields the
    # easiest valid shape (z=0), never a negative.
    assert recommended_z(rate_hps=0.001, goal_seconds=300) == 0


def test_recommended_z_monotonic_in_rate():
    rates = (1.0, 100.0, 10_000.0, 1e6, 1e9)
    zs = [recommended_z(rate_hps=r, goal_seconds=300) for r in rates]
    assert zs == sorted(zs)


def test_benchmark_script_measures_a_positive_rate():
    # Tiny budget (sub-second): proves the measurement path runs the
    # real mill_work loop and yields a sane rate. No magnitude
    # assertion - CI hardware varies.
    bench = _load_script()
    rate = bench.measure_single(seconds=0.05, calibration=2_000)
    assert rate > 0
    assert recommended_z(rate_hps=rate, goal_seconds=300) >= 0


def test_benchmark_script_header_mirrors_unproven_header_shape():
    # The synthetic header must match Block.unproven_header's shape:
    # 6 comma-joined fields plus the trailing comma (proof appends
    # directly after), with 64-hex prev_hash/target/merkle_root.
    bench = _load_script()
    fields = bench.HEADER.split(',')
    assert len(fields) == 7
    assert fields[-1] == ''  # trailing comma
    assert len(fields[2]) == 64  # prev_hash
    assert len(fields[3]) == 64  # target
    assert len(fields[4]) == 64  # merkle_root
