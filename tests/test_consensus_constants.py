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
