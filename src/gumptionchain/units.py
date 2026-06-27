"""GRIT <-> grains conversion — the single canonical boundary.

1 **GRIT** = 100 **grains** (`GRAIN_PER_GRIT`). The node always works in integer
grains; humans and member apps think in whole/decimal GRIT. This module is the
one place that conversion lives, so consumers never re-declare ``100`` locally
(an off-by-100 waiting to happen).

It is deliberately **dependency-free** — pure stdlib (``decimal``), importing
nothing from the node DB / Flask / chain layer — so a member app can
``from gumptionchain.units import grit_to_grains`` at module top without paying
the node's import cost (see the lightweight entry point, egu-354).

The node-proxy relay is the canonical conversion boundary for browser/member
traffic: it accepts and returns **whole GRIT**, converting to/from grains here,
so a member speaking to the proxy never handles grains at all.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

# 1 GRIT = 100 grains. The smallest amount is one grain (0.01 GRIT).
GRAIN_PER_GRIT = 100


def grit_to_grains(grit: str | int | float | Decimal) -> int:
    """Convert a GRIT amount to integer grains, exactly.

    Uses ``Decimal`` (not float) so e.g. ``0.07`` GRIT is exactly 7 grains.
    Raises ``ValueError`` for a non-numeric value or for finer-than-grain
    precision (more than 2 decimal places) — failing loud beats silently
    truncating a sub-grain amount to the wrong number of grains.
    """
    try:
        amount = Decimal(str(grit))
    except (InvalidOperation, ValueError):
        msg = f'not a valid GRIT amount: {grit!r}'
        raise ValueError(msg) from None
    grains = amount * GRAIN_PER_GRIT
    if grains != grains.to_integral_value():
        msg = f'GRIT precision finer than one grain (0.01): {grit!r}'
        raise ValueError(msg)
    return int(grains)


def grains_to_grit(grains: int) -> Decimal:
    """Convert integer grains to a GRIT ``Decimal`` (exact; e.g. 7 -> 0.07).

    Returns a ``Decimal`` so callers control display rounding without inheriting
    float error. ``str(grains_to_grit(n))`` is a faithful GRIT string.
    """
    return Decimal(int(grains)) / GRAIN_PER_GRIT
