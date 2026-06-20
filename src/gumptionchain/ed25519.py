"""Pure-Python Ed25519 signature *verification* for consensus.

The verification DECISION must be deterministic across every node regardless
of installed OpenSSL/library version, so it cannot use `cryptography`/OpenSSL
(which verifies cofactorless and varies across versions). This module owns the
decision in pure Python.

Field/group arithmetic is adapted from the RFC 8032 (EdDSA) Section 6 reference
implementation. Three strict "Option B" rules are layered on top and marked
`# Option B:` below:
  1. reject small-order public keys (strong binding),
  2. reject non-canonical point encodings (enforced by recover_x: y >= p),
  3. use the COFACTORED verification equation [8]sB == [8](R + hA).
Canonical scalar (S < L) is enforced as in the reference.

Signing/keygen are NOT here — those use audited pyca and are not
consensus-divergent. This module is verification only and handles only public
data (no secrets, no side-channel surface).

Provenance and license of the reference arithmetic
---------------------------------------------------
The field/group/point routines are derived from the Python reference code in
RFC 8032 Section 6 ("Edwards-Curve Digital Signature Algorithm (EdDSA)"), an
IETF "Code Component":

    Copyright (c) 2017 IETF Trust and the persons identified as authors of
    the code. All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, is permitted pursuant to, and subject to the license terms
    contained in, the Revised BSD License set forth in Section 4.c of the IETF
    Trust's Legal Provisions Relating to IETF Documents
    (https://trustee.ietf.org/license-info).

The Option-B strict rules and the cofactored verification equation are
GumptionChain additions (MIT, per the repository LICENSE).
"""

from __future__ import annotations

import hashlib
from typing import cast

# Curve constants (RFC 8032 / Curve25519).
P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493

Point = tuple[int, int, int, int]  # extended homogeneous (X, Y, Z, T)
NEUTRAL: Point = (0, 1, 1, 0)


def _modp_inv(x: int) -> int:
    return pow(x, P - 2, P)


_D = -121665 * _modp_inv(121666) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)


def _recover_x(y: int, sign: int) -> int | None:
    # Option B: reject non-canonical encodings (y must be < P).
    if y >= P:
        return None
    x2 = (y * y - 1) * _modp_inv(_D * y * y + 1) % P
    if x2 == 0:
        if sign:
            return None
        return 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _point_add(p: Point, q: Point) -> Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = 2 * p[3] * q[3] * _D % P
    dd = 2 * p[2] * q[2] % P
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _point_mul(s: int, p: Point) -> Point:
    q = NEUTRAL
    while s > 0:
        if s & 1:
            q = _point_add(q, p)
        p = _point_add(p, p)
        s >>= 1
    return q


def _point_equal(p: Point, q: Point) -> bool:
    # T is redundant (T = XY/Z); equality of X and Y (projectively) suffices.
    if (p[0] * q[2] - q[0] * p[2]) % P != 0:
        return False
    return (p[1] * q[2] - q[1] * p[2]) % P == 0


_GY = 4 * _modp_inv(5) % P
# The base point's y = 4/5 mod P is always a valid curve point, so recover_x
# never returns None here. cast (not assert) so it holds under `python -O`.
_GX: int = cast(int, _recover_x(_GY, 0))
_G: Point = (_GX, _GY, 1, _GX * _GY % P)


def _point_decompress(s: bytes) -> Point | None:
    if len(s) != 32:
        return None
    y = int.from_bytes(s, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _sha512_mod_l(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), 'little') % L


def verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
    """Return True iff `signature` is a valid Ed25519 signature of `message`
    under `public_key`, per the strict Option-B consensus rule. Never raises
    on bad input — malformed/invalid -> False.
    """
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _point_decompress(public_key)
    if a is None:
        return False
    # Option B: reject small-order public keys (8 * A == identity).
    if _point_equal(_point_mul(8, a), NEUTRAL):
        return False
    r_bytes = signature[:32]
    r = _point_decompress(r_bytes)
    if r is None:
        return False
    s = int.from_bytes(signature[32:], 'little')
    if s >= L:  # canonical scalar
        return False
    # RFC 8032 §5.1: k = H(R || A || M) mod L
    h = _sha512_mod_l(r_bytes + public_key + message)
    sb = _point_mul(s, _G)
    ha = _point_mul(h, a)
    rhs = _point_add(r, ha)
    # Option B: cofactored equation [8]sB == [8](R + hA).
    return _point_equal(_point_mul(8, sb), _point_mul(8, rhs))
