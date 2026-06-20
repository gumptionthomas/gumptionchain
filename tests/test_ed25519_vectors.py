"""Adversarial Ed25519 vector gate: speccheck + Wycheproof.

Verifies that gumptionchain.ed25519.verify() implements the strict Option-B
rule correctly on vendored edge-case and corpus vectors.

Option-B accept rule:
  ACCEPT iff ALL of:
    1. S < L  (canonical scalar)
    2. A decodes canonically (encoded y < P) and is NOT small-order
    3. R decodes canonically (encoded y < P)
    4. Cofactored equation [8]sB == [8](R + hA) holds

Fixtures are static — never fetched at test time.
"""

import json
from binascii import unhexlify
from pathlib import Path

import pytest

from gumptionchain.ed25519 import verify

FIX = Path(__file__).parent / 'fixtures' / 'ed25519'


def _load(name: str) -> object:
    return json.loads((FIX / name).read_text())


# ---------------------------------------------------------------------------
# Speccheck
# ---------------------------------------------------------------------------

# Expected accept/reject per case index, derived by running Option-B rules
# against each vector's properties (see analysis below for per-case reasoning).
#
# Summary of per-case properties (from speccheck README condition table):
#
# case 0: A=small, R=small, S=0
#   -> REJECT: A is small-order
#
# case 1: A=small, R=mixed, 0<S<L
#   -> REJECT: A is small-order
#
# case 2: A=mixed (non-small, full-prime-order), R=small, 0<S<L
#   -> ACCEPT: A canonical+non-small-order, R canonically encodes (small-order
#      R is not separately gated — only A is), S<L, cofactored eq holds.
#      (Small-order R can appear in valid mixed-order signatures.)
#
# case 3: A=mixed, R=mixed, 0<S<L; passes both cofactored and cofactorless
#   -> ACCEPT: all Option-B checks pass, cofactored eq holds
#
# case 4: A=mixed, R=mixed, 0<S<L; passes cofactored, fails cofactorless
#   -> ACCEPT: all Option-B checks pass, cofactored eq holds
#      (This is the distinguisher vector — cofactorless rejecters fail here.)
#
# case 5: A=mixed, R at prime order L, 0<S<L; passes cofactored without
#         pre-reduction, fails if scalar is pre-reduced before multiplication
#   -> ACCEPT: R decodes canonically, A non-small-order, S<L, cofactored eq
#      [8](R+hA)==[8]sB holds. Our verifier does NOT pre-reduce s.
#
# case 6: A at prime-order L, R at prime-order L, S>L (out of bounds)
#   -> REJECT: S >= L (non-canonical scalar)
#
# case 7: A at prime-order L, R at prime-order L, S>>L (far out of bounds)
#   -> REJECT: S >= L (non-canonical scalar)
#
# case 8: A=mixed, R=non-canonical encoding (y bit-pattern fails decode), 0<S<L
#   -> REJECT: R is a non-canonical encoding (does not decode)
#
# case 9: A=mixed, R=non-canonical encoding, 0<S<L
#   -> REJECT: R is a non-canonical encoding (does not decode)
#
# case 10: A=non-canonical encoding (small* in speccheck table), R=mixed, 0<S<L
#   -> REJECT: A is a non-canonical encoding (does not decode)
#
# case 11: A=non-canonical encoding, R=mixed, 0<S<L
#   -> REJECT: A is a non-canonical encoding (does not decode)

SPECCHECK_EXPECTED: dict[int, bool] = {
    0: False,  # A small-order -> reject
    1: False,  # A small-order -> reject
    2: True,  # A mixed non-small, R small, S<L, cofactored eq holds -> accept
    3: True,  # A mixed, R mixed, S<L, cofactored eq holds -> accept
    # case 4: A mixed, R mixed, S<L, cofactored eq holds; cofactorless fails
    4: True,
    5: True,  # A mixed, R prime-order, S<L, cofactored eq holds -> accept
    6: False,  # S >= L -> reject
    7: False,  # S >= L -> reject
    8: False,  # R non-canonical (does not decode) -> reject
    9: False,  # R non-canonical (does not decode) -> reject
    10: False,  # A non-canonical (does not decode) -> reject
    11: False,  # A non-canonical (does not decode) -> reject
}


@pytest.mark.parametrize('idx', sorted(SPECCHECK_EXPECTED))
def test_speccheck(idx: int) -> None:
    cases = _load('speccheck_cases.json')
    assert isinstance(cases, list)
    assert idx < len(cases), f'case {idx} missing from fixture'
    c = cases[idx]
    pub = unhexlify(c['pub_key'])
    sig = unhexlify(c['signature'])
    msg = unhexlify(c['message'])
    result = verify(pub, sig, msg)
    want = SPECCHECK_EXPECTED[idx]
    assert result is want, (
        f'speccheck case {idx}: got {result!r}, want {want!r}'
    )


# ---------------------------------------------------------------------------
# Wycheproof
# ---------------------------------------------------------------------------


# Wycheproof's 'valid'/'invalid' labels embed a cofactorLESS / RFC-strict
# convention. Our rule is COFACTORED (the strict strong-binding variant), so the
# two can LEGITIMATELY disagree on cofactor / small-order / non-canonical edge
# vectors (the same divergence speccheck case 4 demonstrates). This test
# therefore does NOT blindly trust the upstream label: a disagreement is
# tolerated ONLY on a vector flagged as one of those edge classes (and then
# recorded as a deliberate, documented divergence). A disagreement on ANY OTHER
# vector is a real verifier bug and fails the test — so this asserts "implements
# Option-B", not merely "agrees with Wycheproof".
COFACTOR_DIVERGENT_FLAGS = frozenset(
    {'SignatureMalleability', 'InvalidEncoding', 'CompressedSignature'}
)

# This pinned corpus was verified to contain NO cofactored-divergent vector, so
# this is empty. If a future fixture bump introduces one (our cofactored rule
# accepts a vector Wycheproof's convention rejects, or vice versa), vet it and
# add its tcId here with a justification — DO NOT re-point the consensus rule at
# the upstream label, which would silently swap our cofactored rule for
# Wycheproof's cofactorless one (the exact footgun the vendored verifier exists
# to prevent).
EXPECTED_DIVERGENCES: frozenset[int] = frozenset()


def test_wycheproof() -> None:
    data = _load('wycheproof_ed25519.json')
    assert isinstance(data, dict)
    groups = data['testGroups']
    checked = 0
    bugs: list[str] = []
    divergences: list[int] = []

    for group in groups:
        pub = unhexlify(group['publicKey']['pk'])
        for t in group['tests']:
            tc_id: int = t['tcId']
            msg = unhexlify(t['msg'])
            sig = unhexlify(t['sig'])
            want: bool = t['result'] == 'valid'
            got = verify(pub, sig, msg)
            checked += 1
            if got is want:
                continue
            # Our cofactored verdict differs from Wycheproof's. Acceptable ONLY
            # on a flagged cofactor-edge vector (a genuine convention diff);
            # any other disagreement is a real verifier bug.
            flags = set(t.get('flags', []))
            if flags & COFACTOR_DIVERGENT_FLAGS:
                divergences.append(tc_id)
            else:
                bugs.append(
                    f'tcId={tc_id} comment={t["comment"]!r} '
                    f'flags={sorted(flags)!r} got={got!r} want={want!r}'
                )

    assert checked > 0, 'no Wycheproof vectors were checked'
    assert not bugs, (
        f'{len(bugs)} unflagged Wycheproof disagreement(s) out of {checked} — '
        f'real verifier bugs, NOT convention differences:\n' + '\n'.join(bugs)
    )
    assert set(divergences) == EXPECTED_DIVERGENCES, (
        'cofactored-divergent Wycheproof set changed: '
        f'got {sorted(divergences)}, expected {sorted(EXPECTED_DIVERGENCES)}. '
        'Vet any new vector and add its tcId to EXPECTED_DIVERGENCES; do NOT '
        'change the consensus rule to match the upstream (cofactorless) label.'
    )
