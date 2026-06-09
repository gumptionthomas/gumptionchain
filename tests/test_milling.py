"""Unit tests for the proof-of-work target math (#169).

expected_attempts / recommended_z back the MAX_TARGET benchmark harness
(scripts/benchmark_max_target.py); they have no runtime callers.
"""

from gumptionchain.milling import expected_attempts, recommended_z


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
