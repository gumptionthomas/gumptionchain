from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from gumptionchain.message import (
    BadProofError,
    sign_message,
    verify_message,
)
from gumptionchain.wallet import Wallet

KINDS = frozenset({'opposition', 'support', 'rescind', 'transfer'})

# A txid is a transaction's mill hash: 64-char lowercase hex (see
# milling.mill_hash_str -> .hexdigest()). Validating the canonical shape here
# (rather than only "non-empty string") means a malformed txid is rejected as a
# bad attestation up front, instead of slipping through to a provenance fetch
# that 404s and gets mis-reported as 'txn-not-found'. Kept in lockstep with the
# JS validator's TXID_RE in clients/wallet/gc-attestation.mjs.
_TXID_RE = re.compile(r'[0-9a-f]{64}')


class AttestationError(Exception):
    """Base class for stake-attestation errors."""


class BadAttestationError(AttestationError):
    """Input is not a structurally valid stake attestation."""


def _validate_claim(claim: Any) -> None:
    if not isinstance(claim, dict):
        msg = 'claim must be an object'
        raise BadAttestationError(msg)
    txid = claim.get('txid')
    kind = claim.get('kind')
    subject = claim.get('subject')
    address = claim.get('address')
    amount = claim.get('amount')
    handle = claim.get('handle')
    if not isinstance(txid, str) or not _TXID_RE.fullmatch(txid):
        msg = 'txid must be a 64-char hex digest'
        raise BadAttestationError(msg)
    if kind not in KINDS:
        msg = f'invalid kind: {kind}'
        raise BadAttestationError(msg)
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        msg = 'amount must be a positive integer (grains)'
        raise BadAttestationError(msg)
    if kind == 'transfer':
        if not isinstance(address, str) or not address:
            msg = 'transfer requires address'
            raise BadAttestationError(msg)
        # Reject the off-side key by presence (matching JS's !== undefined),
        # so {'kind':'transfer', 'subject': None} is rejected, not ignored.
        if 'subject' in claim:
            msg = 'transfer must not set subject'
            raise BadAttestationError(msg)
    else:
        if not isinstance(subject, str) or not subject:
            msg = 'stake requires subject'
            raise BadAttestationError(msg)
        if 'address' in claim:
            msg = 'stake must not set address'
            raise BadAttestationError(msg)
    if handle is not None and not isinstance(handle, str):
        msg = 'handle must be a string'
        raise BadAttestationError(msg)


def build_stake_message(claim: dict[str, Any]) -> str:
    _validate_claim(claim)
    ordered: dict[str, Any] = {'txid': claim['txid'], 'kind': claim['kind']}
    if claim['kind'] == 'transfer':
        ordered['address'] = claim['address']
    else:
        ordered['subject'] = claim['subject']
    ordered['amount'] = claim['amount']
    if claim.get('handle') is not None:
        ordered['handle'] = claim['handle']
    return json.dumps(ordered, separators=(',', ':'), ensure_ascii=False)


def sign_stake_attestation(
    wallet: Wallet, claim: dict[str, Any], timestamp: int | None = None
) -> dict[str, str]:
    return sign_message(wallet, build_stake_message(claim), timestamp=timestamp)


def parse_stake_attestation(proof: Any) -> dict[str, Any]:
    if not isinstance(proof, dict) or not isinstance(proof.get('message'), str):
        msg = 'proof has no message'
        raise BadAttestationError(msg)
    try:
        claim = json.loads(proof['message'])
    except ValueError as e:
        msg = 'message is not a stake claim'
        raise BadAttestationError(msg) from e
    _validate_claim(claim)
    # Require canonical encoding: the signed message must be exactly what
    # build_stake_message emits for this claim. Rejects non-canonical forms
    # (a float amount like 300.0, reordered keys, extra fields, whitespace)
    # so JS and Python agree on accept/reject for any signable input.
    if build_stake_message(claim) != proof['message']:
        msg = 'non-canonical stake claim encoding'
        raise BadAttestationError(msg)
    return claim  # type: ignore[no-any-return]


def _outflow_matches(
    outflows: list[dict[str, Any]], claim: dict[str, Any]
) -> bool:
    for o in outflows or []:
        if o.get('kind') != claim['kind'] or o.get('amount') != claim['amount']:
            continue
        if claim['kind'] == 'transfer':
            if o.get('address') == claim['address']:
                return True
        elif o.get('subject') == claim['subject']:
            return True
    return False


def verify_stake(
    proof: Any,
    fetch_provenance: Callable[[str], dict[str, Any] | None],
    max_age: int | None = None,
    min_confirmations: int | None = None,
) -> dict[str, Any]:
    # fetch_provenance(txid) MUST return the #176a provenance dict, or None for
    # an unknown txn; mapping a 404 to None is the injected adapter's job.
    # Genuine transport errors propagate by design — they must NOT be
    # misreported as 'txn-not-found' (which would mark a real canonical stake
    # unverifiable).
    claim = parse_stake_attestation(proof)
    reasons: list[str] = []
    checks = {'signature': False, 'onchain': False, 'consistent': False}
    signer = proof.get('address')

    try:
        sig = verify_message(proof, max_age=max_age)
    except BadProofError as e:
        # A malformed gc-msg-v1 envelope is a malformed attestation.
        msg = 'attestation is not a valid gc-msg-v1 proof'
        raise BadAttestationError(msg) from e
    if sig.get('valid') and sig.get('address') == signer:
        checks['signature'] = True
    else:
        reasons.append(
            'expired' if sig.get('reason') == 'expired' else 'bad-signature'
        )

    provenance = fetch_provenance(claim['txid'])
    if provenance is None:
        reasons.append('txn-not-found')
    elif provenance.get('status') != 'canonical':
        reasons.append('not-canonical')
    elif (
        min_confirmations is not None
        and (provenance.get('confirmations') or 0) < min_confirmations
    ):
        reasons.append('insufficient-confirmations')
    else:
        checks['onchain'] = True

    if checks['signature'] and checks['onchain'] and provenance:
        if provenance.get('address') != signer:
            reasons.append('signer-not-staker')
        elif not _outflow_matches(provenance.get('outflows', []), claim):
            reasons.append('claim-mismatch')
        else:
            checks['consistent'] = True

    return {
        'valid': all(checks.values()),
        'checks': checks,
        'signer': signer,
        'claim': claim,
        'provenance': provenance,
        'confirmations': (provenance or {}).get('confirmations', 0),
        'reasons': reasons,
    }
