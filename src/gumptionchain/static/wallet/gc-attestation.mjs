// "Verified on GumptionChain" stake attestations: a gc-msg-v1 proof whose
// message is the canonical JSON of a stake claim. verifyStake composes the
// signature (gc-msg-v1), on-chain provenance (injected fetchProvenance), and a
// consistency check. Pure — no I/O. No dependencies beyond sibling modules.
// Also: social-platform binding envelopes (wallet ↔ platform identity, #251).
import { BadAttestationError, BadProofError } from './gc-errors.mjs';
import { signMessage, verifyMessage } from './gc-message.mjs';

const KINDS = new Set(['opposition', 'support', 'rescind', 'transfer']);

// A txid is a transaction's mill hash: 64-char lowercase hex. Validating the
// canonical shape here (not just "non-empty string") rejects a malformed txid
// as a bad attestation up front, instead of letting it slip through to a
// provenance fetch that 404s and gets mis-reported as 'txn-not-found'. Kept in
// lockstep with the Python validator's _TXID_RE in attestation.py.
const TXID_RE = /^[0-9a-f]{64}$/;

export { BadAttestationError } from './gc-errors.mjs';

function validateClaim(claim) {
  if (!claim || typeof claim !== 'object') {
    throw new BadAttestationError('claim must be an object');
  }
  const { txid, kind, subject, address, amount, handle } = claim;
  if (typeof txid !== 'string' || !TXID_RE.test(txid)) {
    throw new BadAttestationError('txid must be a 64-char hex digest');
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
  // Require canonical encoding: the signed message must be exactly what
  // buildStakeMessage emits for this claim. Rejects non-canonical forms (a
  // float amount like 300.0, reordered keys, extra fields, whitespace) so JS
  // and Python agree on accept/reject for any signable input.
  if (buildStakeMessage(claim) !== proof.message) {
    throw new BadAttestationError('non-canonical stake claim encoding');
  }
  return claim;
}

// ---------------------------------------------------------------------------
// Wallet ↔ social-platform binding envelope (#251)
// Spec: docs/superpowers/specs/
//   2026-06-10-egu-251-social-binding-envelope-design.md
// Verification of proof_url content and storage of the binding record are
// the hub's responsibility (gumption-hub), not this base layer.
// ---------------------------------------------------------------------------

// Platform identifier: lowercase alphanumeric + hyphen, 1-32 chars. Kept in
// lockstep with the Python validator's _PLATFORM_RE in attestation.py.
const PLATFORM_RE = /^[a-z0-9-]{1,32}$/;
const MAX_HANDLE_LEN = 256;
const MAX_PROOF_URL_LEN = 512;

export function validateBindingClaim(claim) {
  if (!claim || typeof claim !== 'object') {
    throw new BadAttestationError('claim must be an object');
  }
  const { platform, handle } = claim;
  const proof_url = claim.proof_url ?? null;
  if (typeof platform !== 'string' || !PLATFORM_RE.test(platform)) {
    throw new BadAttestationError(
      'platform must be lowercase alphanumeric/hyphen, 1-32 chars',
    );
  }
  if (typeof handle !== 'string' || !handle) {
    throw new BadAttestationError('handle must be a non-empty string');
  }
  // Spread to count code points (not UTF-16 units); parity with Python len().
  if ([...handle].length > MAX_HANDLE_LEN) {
    throw new BadAttestationError(`handle must be at most ${MAX_HANDLE_LEN} chars`);
  }
  if (proof_url !== null) {
    if (typeof proof_url !== 'string') {
      throw new BadAttestationError('proof_url must be a string');
    }
    if (!proof_url.startsWith('https://')) {
      throw new BadAttestationError('proof_url must be an https:// URL');
    }
    if ([...proof_url].length > MAX_PROOF_URL_LEN) {
      throw new BadAttestationError(
        `proof_url must be at most ${MAX_PROOF_URL_LEN} chars`,
      );
    }
  }
}

export function buildBindingMessage(claim) {
  validateBindingClaim(claim);
  const ordered = { platform: claim.platform, handle: claim.handle };
  if (claim.proof_url !== undefined && claim.proof_url !== null) {
    ordered.proof_url = claim.proof_url;
  }
  return JSON.stringify(ordered);
}

export async function signSocialBinding(wallet, claim, { timestamp } = {}) {
  return signMessage(wallet, buildBindingMessage(claim), { timestamp });
}

export function parseSocialBinding(proof) {
  if (!proof || typeof proof.message !== 'string') {
    throw new BadAttestationError('proof has no message');
  }
  let claim;
  try {
    claim = JSON.parse(proof.message);
  } catch {
    throw new BadAttestationError('message is not a binding claim');
  }
  validateBindingClaim(claim);
  if (buildBindingMessage(claim) !== proof.message) {
    throw new BadAttestationError('non-canonical binding claim encoding');
  }
  return claim;
}

export async function verifyBinding(proof, { maxAge } = {}) {
  const claim = parseSocialBinding(proof);
  const reasons = [];
  const checks = { signature: false };
  const signer = proof.address;

  let sig;
  try {
    sig = await verifyMessage(proof, { maxAge });
  } catch (e) {
    if (e instanceof BadProofError) {
      throw new BadAttestationError('binding is not a valid gc-msg-v1 proof');
    }
    throw e;
  }
  if (sig.valid && sig.address === signer) {
    checks.signature = true;
  } else {
    reasons.push(sig.reason === 'expired' ? 'expired' : 'bad-signature');
  }

  return {
    valid: Object.values(checks).every(Boolean),
    checks,
    signer,
    claim,
    reasons,
  };
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
// for an unknown txn. The verifier performs no transport itself; mapping a
// 404 to null is the injected adapter's job. Genuine transport errors (e.g. a
// network failure) propagate by design — they must NOT be misreported as
// 'txn-not-found', which would mark a real canonical stake unverifiable.
export async function verifyStake(
  proof,
  { fetchProvenance, maxAge, minConfirmations } = {},
) {
  const claim = parseStakeAttestation(proof);
  const reasons = [];
  const checks = { signature: false, onchain: false, consistent: false };
  const signer = proof.address;

  let sig;
  try {
    sig = await verifyMessage(proof, { maxAge });
  } catch (e) {
    // A structurally malformed gc-msg-v1 envelope is a malformed attestation.
    if (e instanceof BadProofError) {
      throw new BadAttestationError('attestation is not a valid gc-msg-v1 proof');
    }
    throw e;
  }
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
