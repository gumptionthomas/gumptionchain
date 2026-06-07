// Base verify glue: run verifyStake (from the vendored wallet module) over a
// proof, fetching provenance from the node's public endpoint. Pure logic in
// runVerify (fetchProvenance injectable for tests); renderVerdict lights the
// DOM verdict.
import {
  BadAttestationError,
  verifyStake,
} from '../wallet/gc-attestation.mjs';

// Re-exported so a page (base or an extension skin) can tell a user-input
// error (bad/invalid attestation) apart from a system error (e.g. provenance
// fetch failure) when rendering a message.
export { BadAttestationError };

// Adapter: node public provenance endpoint. 404 -> null (unknown txn); other
// failures propagate so they are NOT misreported as 'txn-not-found'.
export function nodeFetchProvenance(origin = '') {
  return async (txid) => {
    // Encode the path segment so a malformed txid (containing /, ?, #, …)
    // can't reshape the request into an unintended path/query.
    const resp = await fetch(
      `${origin}/transaction/${encodeURIComponent(txid)}/provenance.json`,
    );
    if (resp.status === 404) return null;
    if (!resp.ok) {
      throw new Error(`provenance fetch failed: ${resp.status}`);
    }
    return resp.json();
  };
}

export async function runVerify(proof, { fetchProvenance, minConfirmations } = {}) {
  return verifyStake(proof, {
    fetchProvenance: fetchProvenance ?? nodeFetchProvenance(),
    minConfirmations,
  });
}

// Render a full verdict into the DOM: the three checks, the overall seal, and
// the reasons line. Expects elements with data-check="signature|onchain|
// consistent", id="verdict-seal", and (optionally) id="verdict-reasons". Each
// element is optional, so a custom skin can omit any of them. This is the
// single composable entry point a consumer calls — it owns the whole render,
// not just the checks.
export function renderVerdict(verdict, root = document) {
  for (const key of ['signature', 'onchain', 'consistent']) {
    const el = root.querySelector(`[data-check="${key}"]`);
    if (el) {
      el.classList.toggle('check-pass', !!verdict.checks[key]);
      el.classList.toggle('check-fail', !verdict.checks[key]);
    }
  }
  const seal = root.querySelector('#verdict-seal');
  if (seal) seal.classList.toggle('verified', verdict.valid);
  const reasons = root.querySelector('#verdict-reasons');
  if (reasons) {
    reasons.textContent = verdict.reasons.join(', ') || 'all checks passed';
  }
}
