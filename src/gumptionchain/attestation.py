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
from gumptionchain.signing_key import SigningKey

KINDS = frozenset({'opposition', 'support', 'rescind', 'transfer'})

# A txid is a transaction's mill hash: 64-char lowercase hex (see
# milling.mill_hash_str -> .hexdigest()). Validating the canonical shape here
# (rather than only "non-empty string") means a malformed txid is rejected as a
# bad attestation up front, instead of slipping through to a provenance fetch
# that 404s and gets mis-reported as 'txn-not-found'. Kept in lockstep with the
# JS validator's TXID_RE in clients/signing-key/gc-attestation.mjs.
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
    signing_key: SigningKey, claim: dict[str, Any], timestamp: int | None = None
) -> dict[str, str]:
    return sign_message(
        signing_key, build_stake_message(claim), timestamp=timestamp
    )


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


# ---------------------------------------------------------------------------
# SigningKey ↔ social-platform binding envelope.
# Verification of proof_url content and storage of the binding record are
# the hub's responsibility (gumption-hub), not this base layer.
# ---------------------------------------------------------------------------

# Platform identifier: lowercase alphanumeric + hyphen, 1-32 chars. Kept in
# lockstep with the JS validator's PLATFORM_RE in
# clients/signing-key/gc-attestation.mjs.
_PLATFORM_RE = re.compile(r'^[a-z0-9-]{1,32}$')
_MAX_HANDLE_LEN = 256
_MAX_PROOF_URL_LEN = 512


def _validate_binding_claim(claim: Any) -> None:
    if not isinstance(claim, dict):
        msg = 'claim must be an object'
        raise BadAttestationError(msg)
    platform = claim.get('platform')
    handle = claim.get('handle')
    if not isinstance(platform, str) or not _PLATFORM_RE.fullmatch(platform):
        msg = 'platform must be lowercase alphanumeric/hyphen, 1-32 chars'
        raise BadAttestationError(msg)
    if not isinstance(handle, str) or not handle:
        msg = 'handle must be a non-empty string'
        raise BadAttestationError(msg)
    if len(handle) > _MAX_HANDLE_LEN:
        msg = f'handle must be at most {_MAX_HANDLE_LEN} chars'
        raise BadAttestationError(msg)
    proof_url = claim.get('proof_url')
    if proof_url is not None:
        if not isinstance(proof_url, str):
            msg = 'proof_url must be a string'
            raise BadAttestationError(msg)
        if not proof_url or not proof_url.startswith('https://'):
            msg = 'proof_url must be an https:// URL'
            raise BadAttestationError(msg)
        if len(proof_url) > _MAX_PROOF_URL_LEN:
            msg = f'proof_url must be at most {_MAX_PROOF_URL_LEN} chars'
            raise BadAttestationError(msg)


def build_binding_message(claim: dict[str, Any]) -> str:
    _validate_binding_claim(claim)
    ordered: dict[str, Any] = {
        'platform': claim['platform'],
        'handle': claim['handle'],
    }
    if claim.get('proof_url') is not None:
        ordered['proof_url'] = claim['proof_url']
    return json.dumps(ordered, separators=(',', ':'), ensure_ascii=False)


def sign_social_binding(
    signing_key: SigningKey, claim: dict[str, Any], timestamp: int | None = None
) -> dict[str, str]:
    return sign_message(
        signing_key, build_binding_message(claim), timestamp=timestamp
    )


def parse_social_binding(proof: Any) -> dict[str, Any]:
    if not isinstance(proof, dict) or not isinstance(proof.get('message'), str):
        msg = 'proof has no message'
        raise BadAttestationError(msg)
    try:
        claim = json.loads(proof['message'])
    except ValueError as e:
        msg = 'message is not a binding claim'
        raise BadAttestationError(msg) from e
    _validate_binding_claim(claim)
    if build_binding_message(claim) != proof['message']:
        msg = 'non-canonical binding claim encoding'
        raise BadAttestationError(msg)
    return claim  # type: ignore[no-any-return]


def verify_binding(proof: Any, max_age: int | None = None) -> dict[str, Any]:
    # Pure half of binding verification: claim shape + gc-msg-v1
    # signature. The proof_url side (fetch + content check) is the
    # hub's stateful half; its verdict merges into `checks` downstream.
    claim = parse_social_binding(proof)
    reasons: list[str] = []
    checks = {'signature': False}
    signer = proof.get('address')
    try:
        sig = verify_message(proof, max_age=max_age)
    except BadProofError as e:
        msg = 'binding is not a valid gc-msg-v1 proof'
        raise BadAttestationError(msg) from e
    if sig.get('valid') and sig.get('address') == signer:
        checks['signature'] = True
    else:
        reasons.append(
            'expired' if sig.get('reason') == 'expired' else 'bad-signature'
        )
    return {
        'valid': all(checks.values()),
        'checks': checks,
        'signer': signer,
        'claim': claim,
        'reasons': reasons,
    }


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
    # fetch_provenance(txid) MUST return the provenance dict, or None for
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
