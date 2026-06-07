# EGU #3 / #176b — stake attestation + verifier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce and verify a portable "Verified on GumptionChain" stake attestation that composes gc-msg-v1 signing (#2.4) + on-chain provenance (#176a) + a consistency check, in JS and Python, with a pure dependency-injected verifier.

**Architecture:** A stake attestation is a gc-msg-v1 proof whose `message` is the canonical JSON of a claim `{txid, kind, subject|address, amount, handle?}`. New `gc-attestation.mjs` (JS) + `attestation.py` (Python) provide `buildStakeMessage`/`signStakeAttestation`/`parseStakeAttestation`/`verifyStake`. The verifier does no I/O: `fetchProvenance(txid)` is injected and MUST return the provenance object or `null` for not-found. Byte-identical canonical messages across languages are enforced by golden vectors.

**Tech Stack:** Vanilla ESM + Web Crypto (Node 20+ `node --test`), Python 3.12 (pytest, ruff/mypy strict). Zero npm.

**Spec:** `docs/superpowers/specs/2026-06-06-egu-3-stake-attestation-design.md`

**IMPORTANT (shell cwd):** run all commands from the repository root; use repo-root-relative paths in `git add`.

---

## File Structure

- **Create** `clients/wallet/gc-attestation.mjs` — JS attestation API.
- **Create** `clients/wallet/gc-attestation.test.mjs` — JS tests.
- **Modify** `clients/wallet/gc-errors.mjs` — add `BadAttestationError`.
- **Modify** `clients/wallet/index.mjs` — re-export new surface; bump `version` to `0.2.0`.
- **Modify** `clients/wallet/package.json` — bump `version` to `0.2.0`.
- **Modify** `clients/wallet/index.test.mjs` — add new symbols to the contract test.
- **Create** `clients/wallet/attestation-cli.mjs` — build/sign/verify harness for parity.
- **Create** `src/gumptionchain/attestation.py` — Python mirror.
- **Create** `tests/test_attestation.py` — Python tests.
- **Create** `tests/test_attestation_parity.py` — JS↔Python parity.
- **Create** `tests/test_attestation_vectors.py` + `clients/wallet/testdata/gc-attestation-vectors.json` — golden vectors.
- **Modify** `clients/wallet/passkey-wallet-demo.html` + `MANUAL-VERIFICATION.md`.

JS tests: `node --test clients/wallet/*.test.mjs`. Python: `uv run pytest`.

**Shared claim/kind rules** (used by both languages, keep identical):
- `kind` ∈ {`opposition`, `support`, `rescind`, `transfer`}.
- exactly one of `subject` (stake kinds) / `address` (transfer); the other absent.
- `amount`: positive integer (grains).
- `handle`: optional string.
- Canonical message key order: `txid, kind, (subject|address), amount, handle?`; compact JSON; omit absent optionals; Python `ensure_ascii=False`.

---

### Task 1: JS attestation module

**Files:**
- Modify: `clients/wallet/gc-errors.mjs`
- Create: `clients/wallet/gc-attestation.mjs`
- Test: `clients/wallet/gc-attestation.test.mjs`

- [ ] **Step 1: Add the typed error** — append to `clients/wallet/gc-errors.mjs`:

```javascript
// Input is not a structurally valid stake attestation (bad claim shape or a
// proof whose message is not a parseable claim).
export class BadAttestationError extends Error {}
```

- [ ] **Step 2: Write the failing tests** — create `clients/wallet/gc-attestation.test.mjs`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Wallet } from './gc-wallet.mjs';
import {
  buildStakeMessage, signStakeAttestation, parseStakeAttestation,
  verifyStake, BadAttestationError,
} from './gc-attestation.mjs';

const TS = '1700002000';
const CLAIM = { txid: 'tx1', kind: 'opposition', subject: 'goblins', amount: 300 };

// A provenance object shaped like #176a's GET /transaction/<txid> response.
function provenanceFor(address, { status = 'canonical', confirmations = 3 } = {}) {
  return {
    txid: 'tx1', address, status, confirmations,
    outflows: [
      { kind: 'opposition', subject: 'goblins', amount: 300 },
      { kind: 'transfer', address: 'GCchangeGC', amount: 9700 },
    ],
  };
}

test('buildStakeMessage uses fixed key order, omits absent optionals', () => {
  assert.equal(
    buildStakeMessage(CLAIM),
    '{"txid":"tx1","kind":"opposition","subject":"goblins","amount":300}',
  );
  assert.equal(
    buildStakeMessage({ ...CLAIM, handle: 'me.bsky.social' }),
    '{"txid":"tx1","kind":"opposition","subject":"goblins","amount":300,'
    + '"handle":"me.bsky.social"}',
  );
  assert.equal(
    buildStakeMessage({ txid: 't', kind: 'transfer', address: 'GCxGC', amount: 5 }),
    '{"txid":"t","kind":"transfer","address":"GCxGC","amount":5}',
  );
});

test('buildStakeMessage rejects malformed claims', () => {
  assert.throws(() => buildStakeMessage({ kind: 'opposition', subject: 's', amount: 1 }), BadAttestationError);
  assert.throws(() => buildStakeMessage({ txid: 't', kind: 'nope', subject: 's', amount: 1 }), BadAttestationError);
  assert.throws(() => buildStakeMessage({ txid: 't', kind: 'opposition', subject: 's', amount: 0 }), BadAttestationError);
  assert.throws(() => buildStakeMessage({ txid: 't', kind: 'opposition', address: 'a', amount: 1 }), BadAttestationError);
  assert.throws(() => buildStakeMessage({ txid: 't', kind: 'transfer', subject: 's', amount: 1 }), BadAttestationError);
});

test('sign -> parse round-trips the claim', async () => {
  const w = await Wallet.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });
  assert.equal(proof.scheme, 'gc-msg-v1');
  assert.deepEqual(parseStakeAttestation(proof), CLAIM);
});

test('parseStakeAttestation throws on a non-claim message', () => {
  assert.throws(
    () => parseStakeAttestation({ message: 'not json' }),
    BadAttestationError,
  );
});

test('verifyStake valid when signature + onchain + consistent all hold', async () => {
  const w = await Wallet.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });
  const fetchProvenance = async () => provenanceFor(await w.address());
  const v = await verifyStake(proof, { fetchProvenance });
  assert.equal(v.valid, true);
  assert.deepEqual(v.checks, { signature: true, onchain: true, consistent: true });
  assert.equal(v.signer, await w.address());
  assert.equal(v.confirmations, 3);
  assert.deepEqual(v.reasons, []);
});

test('verifyStake reports bad-signature on a tampered claim', async () => {
  const w = await Wallet.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });
  proof.message = buildStakeMessage({ ...CLAIM, amount: 999 });
  const v = await verifyStake(proof, { fetchProvenance: async () => provenanceFor(await w.address()) });
  assert.equal(v.valid, false);
  assert.ok(v.reasons.includes('bad-signature'));
});

test('verifyStake reports txn-not-found, not-canonical, insufficient-confirmations', async () => {
  const w = await Wallet.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });
  const addr = await w.address();

  const missing = await verifyStake(proof, { fetchProvenance: async () => null });
  assert.equal(missing.valid, false);
  assert.ok(missing.reasons.includes('txn-not-found'));

  const orphaned = await verifyStake(proof, {
    fetchProvenance: async () => provenanceFor(addr, { status: 'orphaned' }),
  });
  assert.ok(orphaned.reasons.includes('not-canonical'));

  const shallow = await verifyStake(proof, {
    fetchProvenance: async () => provenanceFor(addr, { confirmations: 1 }),
    minConfirmations: 6,
  });
  assert.ok(shallow.reasons.includes('insufficient-confirmations'));
});

test('verifyStake reports signer-not-staker and claim-mismatch', async () => {
  const w = await Wallet.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });

  const notStaker = await verifyStake(proof, {
    fetchProvenance: async () => provenanceFor('GCsomeoneelseGC'),
  });
  assert.ok(notStaker.reasons.includes('signer-not-staker'));

  const addr = await w.address();
  const mismatch = await verifyStake(proof, {
    fetchProvenance: async () => ({
      txid: 'tx1', address: addr, status: 'canonical', confirmations: 3,
      outflows: [{ kind: 'opposition', subject: 'orcs', amount: 300 }],
    }),
  });
  assert.ok(mismatch.reasons.includes('claim-mismatch'));
});
```

- [ ] **Step 3: Run to verify failure**

Run: `node --test clients/wallet/gc-attestation.test.mjs`
Expected: FAIL — module missing.

- [ ] **Step 4: Implement** — create `clients/wallet/gc-attestation.mjs`:

```javascript
// "Verified on GumptionChain" stake attestations: a gc-msg-v1 proof whose
// message is the canonical JSON of a stake claim. verifyStake composes the
// signature (gc-msg-v1), on-chain provenance (injected fetchProvenance), and a
// consistency check. Pure — no I/O. No dependencies beyond sibling modules.
import { BadAttestationError } from './gc-errors.mjs';
import { signMessage, verifyMessage } from './gc-message.mjs';

const KINDS = new Set(['opposition', 'support', 'rescind', 'transfer']);

export { BadAttestationError } from './gc-errors.mjs';

function validateClaim(claim) {
  if (!claim || typeof claim !== 'object') {
    throw new BadAttestationError('claim must be an object');
  }
  const { txid, kind, subject, address, amount, handle } = claim;
  if (typeof txid !== 'string' || !txid) {
    throw new BadAttestationError('txid is required');
  }
  if (!KINDS.has(kind)) {
    throw new BadAttestationError(`invalid kind: ${kind}`);
  }
  if (!Number.isInteger(amount) || amount <= 0) {
    throw new BadAttestationError('amount must be a positive integer (grains)');
  }
  if (kind === 'transfer') {
    if (typeof address !== 'string' || !address) {
      throw new BadAttestationError('transfer requires address');
    }
    if (subject !== undefined) {
      throw new BadAttestationError('transfer must not set subject');
    }
  } else {
    if (typeof subject !== 'string' || !subject) {
      throw new BadAttestationError('stake requires subject');
    }
    if (address !== undefined) {
      throw new BadAttestationError('stake must not set address');
    }
  }
  if (handle !== undefined && handle !== null && typeof handle !== 'string') {
    throw new BadAttestationError('handle must be a string');
  }
}

export function buildStakeMessage(claim) {
  validateClaim(claim);
  const ordered = { txid: claim.txid, kind: claim.kind };
  if (claim.kind === 'transfer') {
    ordered.address = claim.address;
  } else {
    ordered.subject = claim.subject;
  }
  ordered.amount = claim.amount;
  if (claim.handle !== undefined && claim.handle !== null) {
    ordered.handle = claim.handle;
  }
  return JSON.stringify(ordered);
}

export async function signStakeAttestation(wallet, claim, { timestamp } = {}) {
  return signMessage(wallet, buildStakeMessage(claim), { timestamp });
}

export function parseStakeAttestation(proof) {
  if (!proof || typeof proof.message !== 'string') {
    throw new BadAttestationError('proof has no message');
  }
  let claim;
  try {
    claim = JSON.parse(proof.message);
  } catch {
    throw new BadAttestationError('message is not a stake claim');
  }
  validateClaim(claim);
  return claim;
}

function outflowMatches(outflows, claim) {
  return (outflows || []).some(
    (o) => o.kind === claim.kind
      && o.amount === claim.amount
      && (claim.kind === 'transfer'
        ? o.address === claim.address
        : o.subject === claim.subject),
  );
}

// fetchProvenance(txid) MUST resolve to the #176a provenance object, or null
// for an unknown txn. The verifier performs no transport itself.
export async function verifyStake(
  proof,
  { fetchProvenance, maxAge, minConfirmations } = {},
) {
  const claim = parseStakeAttestation(proof);
  const reasons = [];
  const checks = { signature: false, onchain: false, consistent: false };
  const signer = proof.address;

  const sig = await verifyMessage(proof, { maxAge });
  if (sig.valid && sig.address === signer) {
    checks.signature = true;
  } else {
    reasons.push(sig.reason === 'expired' ? 'expired' : 'bad-signature');
  }

  const provenance = await fetchProvenance(claim.txid);
  if (!provenance) {
    reasons.push('txn-not-found');
  } else if (provenance.status !== 'canonical') {
    reasons.push('not-canonical');
  } else if (
    minConfirmations !== undefined
    && (provenance.confirmations ?? 0) < minConfirmations
  ) {
    reasons.push('insufficient-confirmations');
  } else {
    checks.onchain = true;
  }

  if (checks.signature && checks.onchain && provenance) {
    if (provenance.address !== signer) {
      reasons.push('signer-not-staker');
    } else if (!outflowMatches(provenance.outflows, claim)) {
      reasons.push('claim-mismatch');
    } else {
      checks.consistent = true;
    }
  }

  return {
    valid: checks.signature && checks.onchain && checks.consistent,
    checks,
    signer,
    claim,
    provenance: provenance ?? null,
    confirmations: provenance?.confirmations ?? 0,
    reasons,
  };
}
```

- [ ] **Step 5: Run to verify pass**

Run: `node --test clients/wallet/gc-attestation.test.mjs`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add clients/wallet/gc-errors.mjs clients/wallet/gc-attestation.mjs clients/wallet/gc-attestation.test.mjs
git commit -m "feat(wallet): stake attestation + verifyStake (JS)"
```

---

### Task 2: Barrel re-export + version bump

**Files:**
- Modify: `clients/wallet/index.mjs`, `clients/wallet/package.json`, `clients/wallet/index.test.mjs`

- [ ] **Step 1: Update the contract test** — in `clients/wallet/index.test.mjs`, add the new functions to the `FUNCTIONS` array (`'signStakeAttestation', 'parseStakeAttestation', 'verifyStake'`) and `'BadAttestationError'` to the `ERRORS` array.

- [ ] **Step 2: Run to verify failure**

Run: `node --test clients/wallet/index.test.mjs`
Expected: FAIL — barrel does not export them yet; version mismatch once bumped.

- [ ] **Step 3: Re-export + bump** — in `clients/wallet/index.mjs`, change `export const version = '0.1.0';` to `export const version = '0.2.0';` and add:

```javascript
// Stake attestations (gc-msg-v1 + on-chain provenance composition)
export {
  signStakeAttestation, parseStakeAttestation, verifyStake,
} from './gc-attestation.mjs';
```

and add `BadAttestationError` to the existing `gc-errors.mjs` re-export block. In `clients/wallet/package.json`, change `"version": "0.1.0"` to `"version": "0.2.0"`.

- [ ] **Step 4: Run to verify pass**

Run: `node --test clients/wallet/index.test.mjs`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clients/wallet/index.mjs clients/wallet/package.json clients/wallet/index.test.mjs
git commit -m "feat(wallet): export stake attestation API; bump wallet to 0.2.0"
```

---

### Task 3: Python attestation module

**Files:**
- Create: `src/gumptionchain/attestation.py`
- Test: `tests/test_attestation.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_attestation.py`:

```python
import pytest
from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.attestation import (
    BadAttestationError,
    build_stake_message,
    parse_stake_attestation,
    sign_stake_attestation,
    verify_stake,
)
from gumptionchain.wallet import Wallet

TS = '1700002000'
CLAIM = {'txid': 'tx1', 'kind': 'opposition', 'subject': 'goblins', 'amount': 300}


def _wallet() -> Wallet:
    return Wallet(b58ks=VECTOR_WALLET_B58)


def _provenance(address: str, status: str = 'canonical', confirmations: int = 3):
    return {
        'txid': 'tx1', 'address': address, 'status': status,
        'confirmations': confirmations,
        'outflows': [
            {'kind': 'opposition', 'subject': 'goblins', 'amount': 300},
            {'kind': 'transfer', 'address': 'GCchangeGC', 'amount': 9700},
        ],
    }


def test_build_stake_message_order_and_omission() -> None:
    assert build_stake_message(CLAIM) == (
        '{"txid":"tx1","kind":"opposition","subject":"goblins","amount":300}'
    )
    assert build_stake_message({**CLAIM, 'handle': 'me.bsky.social'}) == (
        '{"txid":"tx1","kind":"opposition","subject":"goblins","amount":300,'
        '"handle":"me.bsky.social"}'
    )


def test_build_stake_message_rejects_malformed() -> None:
    with pytest.raises(BadAttestationError):
        build_stake_message({'kind': 'opposition', 'subject': 's', 'amount': 1})
    with pytest.raises(BadAttestationError):
        build_stake_message({'txid': 't', 'kind': 'x', 'subject': 's', 'amount': 1})
    with pytest.raises(BadAttestationError):
        build_stake_message({'txid': 't', 'kind': 'opposition', 'subject': 's', 'amount': 0})


def test_sign_then_parse_round_trips() -> None:
    proof = sign_stake_attestation(_wallet(), CLAIM, timestamp=int(TS))
    assert parse_stake_attestation(proof) == CLAIM


def test_verify_stake_valid() -> None:
    w = _wallet()
    proof = sign_stake_attestation(w, CLAIM, timestamp=int(TS))
    v = verify_stake(proof, lambda _txid: _provenance(w.address))
    assert v['valid'] is True
    assert v['checks'] == {'signature': True, 'onchain': True, 'consistent': True}
    assert v['signer'] == w.address
    assert v['confirmations'] == 3
    assert v['reasons'] == []


def test_verify_stake_failure_reasons() -> None:
    w = _wallet()
    proof = sign_stake_attestation(w, CLAIM, timestamp=int(TS))

    assert 'txn-not-found' in verify_stake(proof, lambda _t: None)['reasons']
    assert 'not-canonical' in verify_stake(
        proof, lambda _t: _provenance(w.address, status='pending')
    )['reasons']
    assert 'insufficient-confirmations' in verify_stake(
        proof, lambda _t: _provenance(w.address, confirmations=1),
        min_confirmations=6,
    )['reasons']
    assert 'signer-not-staker' in verify_stake(
        proof, lambda _t: _provenance('GCotherGC')
    )['reasons']

    bad = dict(proof)
    bad['message'] = build_stake_message({**CLAIM, 'amount': 999})
    assert 'bad-signature' in verify_stake(
        proof if False else bad, lambda _t: _provenance(w.address)
    )['reasons']

    mismatch = {
        'txid': 'tx1', 'address': w.address, 'status': 'canonical',
        'confirmations': 3,
        'outflows': [{'kind': 'opposition', 'subject': 'orcs', 'amount': 300}],
    }
    assert 'claim-mismatch' in verify_stake(
        proof, lambda _t: mismatch
    )['reasons']


def test_parse_rejects_non_claim() -> None:
    with pytest.raises(BadAttestationError):
        parse_stake_attestation({'message': 'not json'})
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_attestation.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement** — create `src/gumptionchain/attestation.py`:

```python
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from gumptionchain.message import sign_message, verify_message
from gumptionchain.wallet import Wallet

KINDS = frozenset({'opposition', 'support', 'rescind', 'transfer'})


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
    if not isinstance(txid, str) or not txid:
        msg = 'txid is required'
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
        if subject is not None:
            msg = 'transfer must not set subject'
            raise BadAttestationError(msg)
    else:
        if not isinstance(subject, str) or not subject:
            msg = 'stake requires subject'
            raise BadAttestationError(msg)
        if address is not None:
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
    return claim  # type: ignore[no-any-return]


def _outflow_matches(outflows: list[dict[str, Any]], claim: dict[str, Any]) -> bool:
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
    claim = parse_stake_attestation(proof)
    reasons: list[str] = []
    checks = {'signature': False, 'onchain': False, 'consistent': False}
    signer = proof.get('address')

    sig = verify_message(proof, max_age=max_age)
    if sig.get('valid') and sig.get('address') == signer:
        checks['signature'] = True
    else:
        reasons.append(
            'expired' if sig.get('reason') == 'expired' else 'bad-signature'
        )

    provenance = fetch_provenance(claim['txid'])
    if not provenance:
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
```

- [ ] **Step 4: Run tests + lint/type**

Run: `uv run pytest tests/test_attestation.py -q && uv run ruff check src/gumptionchain/attestation.py tests/test_attestation.py && uv run mypy`
Expected: PASS; ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/gumptionchain/attestation.py tests/test_attestation.py
git commit -m "feat(attestation): stake attestation + verify_stake (Python)"
```

---

### Task 4: Cross-language parity + golden vectors

**Files:**
- Create: `clients/wallet/attestation-cli.mjs`
- Create: `tests/test_attestation_parity.py`
- Create: `tests/test_attestation_vectors.py`
- Create: `clients/wallet/testdata/gc-attestation-vectors.json`

- [ ] **Step 1: Create the JS harness** — `clients/wallet/attestation-cli.mjs`:

```javascript
// Stake-attestation harness (test tool). Modes:
//   build  '{claim}'                          -> canonical message string
//   sign   '{"private_key_b58":..,"claim":{..},"timestamp":".."}' -> proof JSON
//   verify '{"proof":{..},"provenance":{..}|null,"minConfirmations":..}' -> verdict
import { Wallet } from './gc-wallet.mjs';
import {
  buildStakeMessage, signStakeAttestation, verifyStake,
} from './gc-attestation.mjs';

const mode = process.argv[2];
const arg = JSON.parse(process.argv[3]);

if (mode === 'build') {
  process.stdout.write(buildStakeMessage(arg));
} else if (mode === 'sign') {
  const w = await Wallet.fromPrivateKeyB58(arg.private_key_b58);
  const proof = await signStakeAttestation(w, arg.claim, { timestamp: arg.timestamp });
  process.stdout.write(JSON.stringify(proof));
} else if (mode === 'verify') {
  const verdict = await verifyStake(arg.proof, {
    fetchProvenance: async () => arg.provenance ?? null,
    minConfirmations: arg.minConfirmations,
  });
  process.stdout.write(JSON.stringify(verdict));
} else {
  process.stderr.write(`unknown mode: ${mode}\n`);
  process.exit(1);
}
```

- [ ] **Step 2: Parity test** — `tests/test_attestation_parity.py`:

```python
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.attestation import build_stake_message, sign_stake_attestation
from gumptionchain.wallet import Wallet

CLI = (
    Path(__file__).resolve().parent.parent
    / 'clients' / 'wallet' / 'attestation-cli.mjs'
)
TS = '1700002000'
CLAIM = {'txid': 'tx1', 'kind': 'opposition', 'subject': 'göblins', 'amount': 300}


def _node(mode: str, payload: dict) -> str:
    out = subprocess.run(  # noqa: S603
        ['node', str(CLI), mode, json.dumps(payload)],  # noqa: S607
        capture_output=True, text=True, check=True,
    )
    return out.stdout


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_canonical_message_is_byte_identical() -> None:
    # Non-ASCII subject exercises ensure_ascii=False parity.
    assert _node('build', CLAIM) == build_stake_message(CLAIM)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_js_signed_attestation_verifies_in_python() -> None:
    from gumptionchain.attestation import verify_stake

    proof = json.loads(_node('sign', {
        'private_key_b58': VECTOR_WALLET_B58, 'claim': CLAIM, 'timestamp': TS,
    }))
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    prov = {
        'txid': 'tx1', 'address': w.address, 'status': 'canonical',
        'confirmations': 5,
        'outflows': [{'kind': 'opposition', 'subject': 'göblins', 'amount': 300}],
    }
    assert verify_stake(proof, lambda _t: prov)['valid'] is True


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_python_signed_attestation_verifies_in_js() -> None:
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    proof = sign_stake_attestation(w, CLAIM, timestamp=int(TS))
    prov = {
        'txid': 'tx1', 'address': w.address, 'status': 'canonical',
        'confirmations': 5,
        'outflows': [{'kind': 'opposition', 'subject': 'göblins', 'amount': 300}],
    }
    verdict = json.loads(_node('verify', {'proof': proof, 'provenance': prov}))
    assert verdict['valid'] is True
```

- [ ] **Step 3: Golden vectors** — `tests/test_attestation_vectors.py`:

```python
import json
import os
from pathlib import Path

from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.attestation import build_stake_message, sign_stake_attestation
from gumptionchain.wallet import Wallet

VECTORS_PATH = (
    Path(__file__).resolve().parent.parent
    / 'clients' / 'wallet' / 'testdata' / 'gc-attestation-vectors.json'
)
_CASES = [
    {'claim': {'txid': 'tx1', 'kind': 'opposition', 'subject': 'goblins',
               'amount': 300}, 'timestamp': '1700002000'},
    {'claim': {'txid': 'tx2', 'kind': 'support', 'subject': 'göblins',
               'amount': 100, 'handle': 'me.bsky.social'},
     'timestamp': '1700002001'},
    {'claim': {'txid': 'tx3', 'kind': 'transfer', 'address': 'GCxGC',
               'amount': 5}, 'timestamp': '1700002002'},
]


def _expected() -> list[dict]:
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    out = []
    for c in _CASES:
        proof = sign_stake_attestation(
            w, c['claim'], timestamp=int(c['timestamp'])
        )
        out.append({
            **c,
            'message': build_stake_message(c['claim']),
            'signature': proof['signature'],
            'address': proof['address'],
        })
    return out


def test_attestation_vectors_match() -> None:
    expected = _expected()
    if os.environ.get('GC_REGEN_VECTORS'):
        VECTORS_PATH.write_text(json.dumps(expected, indent=2) + '\n')
    assert json.loads(VECTORS_PATH.read_text()) == expected
```

Generate the file once: `GC_REGEN_VECTORS=1 uv run pytest tests/test_attestation_vectors.py -q`, then confirm a plain run passes.

- [ ] **Step 4: JS golden-vector check** — append to `clients/wallet/gc-attestation.test.mjs`:

```javascript
import { readFileSync } from 'node:fs';

test('JS canonical messages match the committed golden vectors', () => {
  const vec = JSON.parse(readFileSync(
    new URL('./testdata/gc-attestation-vectors.json', import.meta.url),
  ));
  for (const c of vec) {
    assert.equal(buildStakeMessage(c.claim), c.message);
  }
});
```

(The signature columns are covered by the Python vector test + the live parity test; the JS check pins the byte-identical canonical message, which is the parity-critical part.)

- [ ] **Step 5: Run everything**

Run: `node --test clients/wallet/*.test.mjs && uv run pytest tests/test_attestation.py tests/test_attestation_parity.py tests/test_attestation_vectors.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add clients/wallet/attestation-cli.mjs clients/wallet/testdata/gc-attestation-vectors.json clients/wallet/gc-attestation.test.mjs tests/test_attestation_parity.py tests/test_attestation_vectors.py
git commit -m "test(attestation): JS<->Python parity + golden vectors"
```

---

### Task 5: Demo + manual verification

**Files:**
- Modify: `clients/wallet/passkey-wallet-demo.html`, `clients/wallet/MANUAL-VERIFICATION.md`

Browser-only glue; the logic is Node/pytest-covered.

- [ ] **Step 1: Add stake-attestation UI** — import from `./index.mjs`:
`import { signStakeAttestation, verifyStake } from './index.mjs';`
Add a panel wired to the demo's `currentWallet`:
- **Build & sign attestation:** inputs for `txid`, `kind`, `subject`, `amount` → `signStakeAttestation(currentWallet, claim)` → show the proof JSON (copyable).
- **Verify attestation:** paste a proof → `verifyStake(proof, { fetchProvenance })` where `fetchProvenance(txid)` does `fetch('/api/transaction/' + txid)` (signed if the node requires reader auth; note in the page that a public node uses `READER_ADDRESSES=["*"]`), returning the JSON or `null` on 404 → render `valid` + the three checks + `reasons`/`confirmations`. Surface `BadAttestationError.message` via the existing `fail()` path.

- [ ] **Step 2: Document** — add a "Stake attestation (#176b)" section to `MANUAL-VERIFICATION.md`: build+sign a claim for a real mined txid, verify it (expect `valid:true`, three checks green), then tamper the proof message and confirm `bad-signature`, and verify a non-canonical/unknown txid → the matching reason.

- [ ] **Step 3: Verify modules parse**

Run: `node --check clients/wallet/gc-attestation.mjs && node --check clients/wallet/attestation-cli.mjs`
Expected: exit 0. Browser flow is the manual gate.

- [ ] **Step 4: Commit**

```bash
git add clients/wallet/passkey-wallet-demo.html clients/wallet/MANUAL-VERIFICATION.md
git commit -m "docs(wallet): demo + manual steps for stake attestations"
```

---

## Final verification (before finishing the branch)

- [ ] `node --test clients/wallet/*.test.mjs` — all green.
- [ ] `uv run pytest` — full suite green (attestation unit/parity/vectors included).
- [ ] `uv run ruff check src tests && uv run ruff format --check src tests` — clean.
- [ ] `uv run mypy` — no new errors.
- [ ] Zero npm; `gc-attestation.mjs`/`attestation-cli.mjs` import only sibling `.mjs`.
- [ ] `index.mjs` and `package.json` both at `0.2.0`; barrel contract test passes.

## Self-review notes

- **Spec coverage:** claim shape + grains + fixed-order canonical (Task 1/3); byte-exact parity via golden vectors + non-ASCII subject (Task 4); `signStakeAttestation`/`parseStakeAttestation`/`verifyStake` (1/3); DI `fetchProvenance`, no I/O; verdict `{valid, checks, signer, claim, provenance, confirmations, reasons}` with the six reason codes; `BadAttestationError` throw-vs-data split; barrel export + version bump (Task 2); demo/manual (Task 5). All mapped.
- **Name/shape consistency:** JS `buildStakeMessage`/`signStakeAttestation`/`parseStakeAttestation`/`verifyStake` ↔ Python `build_stake_message`/`sign_stake_attestation`/`parse_stake_attestation`/`verify_stake`; identical claim keys, kind set, check names, and reason strings (`bad-signature`/`expired`/`txn-not-found`/`not-canonical`/`insufficient-confirmations`/`signer-not-staker`/`claim-mismatch`); canonical key order identical.
- **No placeholders:** complete code for every module/test.
- **Additive:** new modules + barrel re-export + version bump; no existing module behavior changed; no Python chain/endpoint change.
